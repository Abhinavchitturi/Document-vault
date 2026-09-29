"""Secure Digital Document Vault: web API and page.

Endpoints:
    GET    /                              the web app (frontend/out, built with `npm run build`)
    GET    /api/info                      library size, categories, whether OCR is available
    GET    /api/documents                 all documents in the library
    PATCH  /api/documents/{id}            change a document's type, or correct its key facts
    GET    /api/upcoming?days=90          expiries and due dates coming up (and missed in the last 30 days)
    GET    /api/reminders                 open reminders + this person's reminder choices
    POST   /api/reminders/done | settings mark paid / renewed; change reminder days, time, Mac notifications
    GET    /api/reminders/schedule        future reminders for the phone to schedule
    GET    /api/reminders/calendar.ics    the same as a calendar file
    GET    /api/settings                  owner, models, where data lives
    POST   /api/settings                  switch the answer model (one already downloaded in Ollama)
    POST   /api/documents                 upload one or more files (form fields: files, category)
    GET    /api/documents/{id}/file       open the original file
    GET    /api/documents/{id}/pages/{n}/image       one page as an image (scans and photos straightened)
    POST   /api/documents/{id}/pages/{n}/highlights  where the answer's passage and facts are on that page
    DELETE /api/documents/{id}            remove a document and its search data
    GET    /api/search?q=...&category=...&method=... best passage per document (hybrid search by default)
    POST   /api/chat                      chat: answer streamed word by word (NDJSON), with sources
    POST   /api/ask                       the same answer in one response (scripts, tests)

Usage:
    python -m backend.api.app                 # then open http://localhost:8000 (passkeys need "localhost")
    python -m backend.api.app --llm-model gemma2:9b

Answers come from a local model through Ollama (see backend/llm/ollama.py); if
Ollama isn't running, search still works and the page says how to start it.

Sign-in (Phase 6, backend/auth/): every /api call needs a signed-in family member, and
each person only sees their own documents. The first visit sets up the admin's password.

    GET    /api/auth/status  POST /api/auth/setup | login | logout | password
    GET    /api/users   POST /api/users   POST /api/users/{id}/password   (admin)

The server listens on 127.0.0.1 only: until Phase 13 adds HTTPS, it must not be exposed to a network.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.auth.accounts import SESSION_DAYS, Accounts, AuthError, TooManyAttempts
from backend.auth.devices import Devices
from backend.classify.classifier import REVIEW_BELOW, Classifier
from backend.extract.facts import FactsReader
from backend.reminders.reminders import Reminders
from backend.auth.passkeys import Passkeys
from backend.llm.answer import LLM, answer_question, finalize, stream_answer
from backend.llm.ollama import LLMUnavailableError
from backend.retriever.retriever import METHODS, Retriever
from backend.viewer.highlight import highlights_for, page_image

from backend.parser.categories import CATEGORIES
from backend.parser.extract import IMAGE_SUFFIXES, SUPPORTED_SUFFIXES, PasswordRequiredError, OcrUnavailableError, UnsupportedFileError, ocr_available
from backend.upload.ingest import EmbedderLike, EmptyDocumentError, ingest_file
from backend.vector_store.store import DocumentStore

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "out"

# The page may only talk to this server: no scripts, fonts, images or requests from anywhere
# else. Applied to HTML pages only (a strict policy can break the browser's built-in PDF viewer).
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")

NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Document Vault</title>
<body style="font:16px/1.6 -apple-system,sans-serif;max-width:560px;margin:10vh auto;padding:0 16px">
<h1>Almost there</h1><p>The web app hasn't been built yet. In a terminal, from the project folder:</p>
<pre style="background:#f1f2f7;padding:12px;border-radius:8px">cd frontend
npm install
npm run build</pre><p>Then restart <code>python -m backend.api.app</code> and reload this page.</p></body>"""
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

logger = logging.getLogger("api")


def _public(doc: dict) -> dict:
    """Document record as sent to the browser (no internal storage details)."""
    shown = {k: v for k, v in doc.items() if k not in ("sha256", "stored_as")}
    confidence = doc.get("category_confidence")
    shown["needs_review"] = doc.get("category_source") == "auto" and confidence is not None and confidence < REVIEW_BELOW
    return shown


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    category: str | None = None
    k: int = Field(5, ge=1, le=10)


class ReminderMark(BaseModel):
    doc_id: str = Field(..., max_length=32)
    kind: Literal["expires", "due"]
    done: bool = True


class ReminderSettings(BaseModel):
    expiry_days: list[int] | None = Field(None, max_length=10)
    due_days: list[int] | None = Field(None, max_length=10)
    time_of_day: str | None = Field(None, max_length=5)
    desktop: bool | None = None


class DocumentUpdate(BaseModel):
    """A person re-filing a document or correcting its key facts (only the fields sent change)."""

    category: str | None = None
    expires_on: date | None = None
    due_on: date | None = None
    amount: str | None = Field(None, max_length=32)
    reference: str | None = Field(None, max_length=120)
    issuer: str | None = Field(None, max_length=200)


class SettingsUpdate(BaseModel):
    llm_model: str | None = Field(None, min_length=1, max_length=200)
    owner_name: str | None = Field(None, min_length=1, max_length=60)


class HighlightRequest(BaseModel):
    chunk_id: str | None = Field(None, max_length=100)  # the passage the answer used
    answer: str | None = Field(None, max_length=4000)  # the answer text, to find its facts on the page


class LoginRequest(BaseModel):
    name: str = Field(..., max_length=100)
    password: str = Field(..., max_length=500)


class SetupRequest(BaseModel):
    name: str = Field(..., max_length=100)
    password: str = Field(..., max_length=500)


class PasswordChange(BaseModel):
    current_password: str = Field(..., max_length=500)
    new_password: str = Field(..., max_length=500)


class PasswordReset(BaseModel):
    new_password: str = Field(..., max_length=500)


class PasskeyResponse(BaseModel):
    ceremony: str = Field(..., max_length=100)
    credential: dict  # the browser's PublicKeyCredential, as JSON
    name: str | None = Field(None, max_length=100)


class PairRequest(BaseModel):
    code: str = Field(..., max_length=100)
    device_name: str = Field("Phone", max_length=100)


class NewMember(BaseModel):
    name: str = Field(..., max_length=100)
    password: str = Field(..., max_length=500)
    is_admin: bool = False


SESSION_COOKIE = "vault_session"
PHONE_PUBLIC_PATHS = {"/api/mobile/hello", "/api/mobile/pair"}  # the only calls a phone makes before pairing
PUBLIC_PATHS = {"/api/auth/status", "/api/auth/login", "/api/auth/setup", "/api/auth/logout",
                "/api/auth/passkey/options", "/api/auth/passkey"}


def current_user(request: Request) -> dict:
    """The signed-in person (checked by the require_sign_in middleware)."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(403, "Only an admin can manage family members.")
    return user


def _client(request: Request) -> str:
    return request.client.host if request.client else ""


def _same_origin(request: Request) -> bool:
    """Browsers send Origin on cross-site form posts and fetches; refuse those that come from
    another website (the session cookie is SameSite=Strict too, this is a second lock)."""
    origin = request.headers.get("origin")
    if not origin:
        return True  # not a browser cross-site request (curl, scripts, same-origin GETs)
    return urlsplit(origin).netloc == request.headers.get("host", "")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    category: str | None = None
    k: int = Field(5, ge=1, le=10)  # passages given to the LLM


def _passage(chunk: dict, score: float, rank: int) -> dict:
    return {
        "rank": rank,
        "score": round(score, 4),
        "chunk_id": chunk["chunk_id"],
        "doc_id": chunk["doc_id"],
        "filename": chunk["filename"],
        "category": chunk["category"],
        "page": chunk["page"],
        "text": chunk["text"],
    }


def _llm_status(llm) -> dict:
    if llm is None:
        return {"ready": False, "message": "Answers are turned off (the app was started with --no-llm)."}
    check = getattr(llm, "status", None)
    ready, message = check() if check else (True, f"Answers ready ({llm.name})")
    return {"ready": ready, "message": message}


def create_app(
    store: DocumentStore,
    embedder: EmbedderLike,
    tokenizer,
    llm: LLM | None = None,
    frontend_dir: Path | None = FRONTEND_DIR,
    database_label: str = "",
    search_method: str = "hybrid",
    accounts: Accounts | None = None,
    passkey_hosts: tuple[str, ...] = ("localhost",),
    mode: Literal["web", "phone"] = "web",
    devices: Devices | None = None,
    phone_link: dict | None = None,
) -> FastAPI:
    """`store` is any view of the vault; each request works on the signed-in person's own view.

    mode="web"   the Mac's browser: pages + API, cookie sessions (http://localhost:8000)
    mode="phone" the phone app on the home network (backend/api/lan.py): API only, paired phones
                 only (Bearer device keys); run over HTTPS. `phone_link` = {"urls", "fingerprint"}
                 of that listener ("urls": a list, or a function giving the current list), given
                 to both apps: the web app puts it in the pairing QR code, phones refresh from it.
    """
    app = FastAPI(title="Secure Digital Document Vault", docs_url=None, redoc_url=None, openapi_url=None)
    accounts = accounts or Accounts(store.engine)
    classifier = Classifier(embedder, llm)  # Phase 8: sorts uploads marked "auto"
    facts_reader = FactsReader(llm)  # Phase 9: expiry, due date, amount, reference, issuer (Needle 3 if installed)
    reminders = Reminders(store.engine)  # Phase 10
    keys = Passkeys(store.engine, passkey_hosts)
    devices = devices or Devices(store.engine)

    def current_urls() -> list[str]:
        if not phone_link:
            return []
        urls = phone_link["urls"]
        return list(urls() if callable(urls) else urls)
    retrievers: dict[tuple[int, str], Retriever] = {}

    def vault_of(user: dict) -> DocumentStore:
        return store.for_user(user["id"])

    def retriever_for(user: dict, method: str | None = None) -> Retriever:
        key = (user["id"], method or search_method)
        if key not in retrievers:  # one per person and method: each keeps its own keyword index
            retrievers[key] = Retriever(vault_of(user), embedder, key[1])
        return retrievers[key]

    @app.middleware("http")
    async def require_sign_in(request: Request, call_next):
        """Every /api call needs a signed-in person, except signing in itself. Enforced here, for all
        routes at once, so a new endpoint can't forget it; endpoints read the person from request.state."""
        path = request.url.path
        if mode == "phone":
            if not path.startswith("/api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)  # no web pages on the network
            if path not in PHONE_PUBLIC_PATHS:
                auth = request.headers.get("authorization", "")
                token = auth[7:] if auth.lower().startswith("bearer ") else None
                user = await run_in_threadpool(devices.user_for_token, token)
                if user is None:
                    return JSONResponse({"detail": "This phone isn't paired (or was removed). Pair it again."},
                                        status_code=401)
                request.state.user = user
            return await call_next(request)
        if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin(request):
            return JSONResponse({"detail": "Request from another website refused."}, status_code=403)
        if path in PHONE_PUBLIC_PATHS:
            return JSONResponse({"detail": "Not found"}, status_code=404)  # phone-only calls
        if path.startswith("/api/") and path not in PUBLIC_PATHS:
            user = await run_in_threadpool(accounts.user_for_token, request.cookies.get(SESSION_COOKIE))
            if user is None:
                return JSONResponse({"detail": "Please sign in."}, status_code=401)
            request.state.user = user
        return await call_next(request)

    # ---------- signing in ----------

    def _set_cookie(request: Request, response: Response, token: str) -> None:
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", path="/")

    @app.get("/api/auth/status")
    def auth_status(request: Request) -> dict:
        user = accounts.user_for_token(request.cookies.get(SESSION_COOKIE))
        setup = accounts.setup_needed()
        try:
            keys.site(request.url.scheme, request.headers.get("host", ""))
            passkey_hint = None
        except AuthError as e:
            passkey_hint = str(e)
        return {"user": user, "setup_needed": setup, "suggested_name": accounts.suggested_name() if setup else None,
                "passkeys_available": passkey_hint is None, "passkey_hint": passkey_hint}

    @app.post("/api/auth/setup")
    def auth_setup(req: SetupRequest, request: Request, response: Response) -> dict:
        try:
            user = accounts.setup(req.name, req.password)
            user, token = accounts.login(user["name"], req.password, _client(request))
        except AuthError as e:
            raise HTTPException(400, str(e)) from e
        _set_cookie(request, response, token)
        logger.info("Vault set up: %s is the admin", user["name"])
        return {"user": user}

    @app.post("/api/auth/login")
    def auth_login(req: LoginRequest, request: Request, response: Response) -> dict:
        try:
            user, token = accounts.login(req.name, req.password, _client(request))
        except TooManyAttempts as e:
            raise HTTPException(429, str(e)) from e
        except AuthError as e:
            logger.warning("Failed sign-in for %r from %s", req.name[:60], _client(request))
            raise HTTPException(401, str(e)) from e
        _set_cookie(request, response, token)
        return {"user": user}

    def _site(request: Request) -> tuple[str, str]:
        try:
            return keys.site(request.url.scheme, request.headers.get("host", ""))
        except AuthError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/auth/passkey/options")
    def passkey_login_options(request: Request) -> dict:
        return keys.login_options(*_site(request))

    @app.post("/api/auth/passkey")
    def passkey_login(req: PasskeyResponse, request: Request, response: Response) -> dict:
        throttle = [f"client:{_client(request)}"]
        try:
            accounts.throttle(throttle)
            user = keys.login(req.ceremony, req.credential)
        except TooManyAttempts as e:
            raise HTTPException(429, str(e)) from e
        except AuthError as e:
            accounts.record_failure(throttle)
            logger.warning("Failed passkey sign-in from %s: %s", _client(request), e)
            raise HTTPException(401, str(e)) from e
        _set_cookie(request, response, accounts.start_session(user["id"]))
        return {"user": user}

    @app.get("/api/passkeys")
    def list_passkeys(user: dict = Depends(current_user)) -> list[dict]:
        return keys.list_for(user["id"])

    @app.post("/api/passkeys/options")
    def passkey_add_options(request: Request, user: dict = Depends(current_user)) -> dict:
        try:
            return keys.registration_options(user, *_site(request))
        except AuthError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/passkeys")
    def passkey_add(req: PasskeyResponse, user: dict = Depends(current_user)) -> dict:
        try:
            added = keys.register(user, req.ceremony, req.credential, req.name or "Passkey")
        except AuthError as e:
            raise HTTPException(400, str(e)) from e
        logger.info("%s added a passkey (%s)", user["name"], added["name"])
        return added

    @app.delete("/api/passkeys/{passkey_id}")
    def passkey_remove(passkey_id: str, user: dict = Depends(current_user)) -> dict:
        if not keys.remove(user["id"], passkey_id):
            raise HTTPException(404, "passkey not found")
        return {"removed": passkey_id}

    @app.post("/api/auth/logout")
    def auth_logout(request: Request, response: Response) -> dict:
        accounts.logout(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"signed_out": True}

    @app.post("/api/auth/password")
    def change_password(req: PasswordChange, request: Request, user: dict = Depends(current_user)) -> dict:
        try:
            accounts.change_password(user["id"], req.current_password, req.new_password,
                                     keep_token=request.cookies.get(SESSION_COOKIE))
        except TooManyAttempts as e:
            raise HTTPException(429, str(e)) from e
        except AuthError as e:
            raise HTTPException(400, str(e)) from e
        return {"changed": True}

    # ---------- family members (admin) ----------

    @app.get("/api/users")
    def list_users(user: dict = Depends(admin_user)) -> list[dict]:
        return accounts.list_users()

    @app.post("/api/users")
    def add_user(req: NewMember, user: dict = Depends(admin_user)) -> dict:
        try:
            member = accounts.add_member(req.name, req.password, is_admin=req.is_admin)
        except AuthError as e:
            raise HTTPException(400, str(e)) from e
        logger.info("%s added family member %s", user["name"], member["name"])
        return member

    @app.post("/api/users/{user_id}/password")
    def reset_password(user_id: int, req: PasswordReset, user: dict = Depends(admin_user)) -> dict:
        if user_id == user["id"]:
            raise HTTPException(400, "Change your own password under Your account (it needs your current one).")
        try:
            accounts.set_password(user_id, req.new_password)
        except AuthError as e:
            raise HTTPException(400, str(e)) from e
        devices.revoke_all(user_id)  # a reset is often "someone else may know it": unpair their phones too
        logger.info("%s reset the password of user %d", user["name"], user_id)
        return {"changed": True}

    # ---------- phones (Phase 7) ----------

    @app.get("/api/mobile/hello")
    def mobile_hello() -> dict:
        return {"app": "document-vault", "api": 1}

    @app.post("/api/mobile/pairing")
    def start_pairing(user: dict = Depends(current_user)) -> dict:
        """A QR code for the phone app: the vault's network address, its certificate fingerprint,
        and a one-time code (10 minutes)."""
        if not phone_link:
            raise HTTPException(409, "Phone access is off. Restart the vault with: python -m backend.api.app --phones")
        urls = current_urls()
        if not urls:
            raise HTTPException(409, "This Mac isn't on a network phones can reach. Connect to Wi-Fi or start Tailscale.")
        code, seconds = devices.start_pairing(user["id"])
        payload = json.dumps({"v": 1, "url": urls[0], "urls": urls, "fp": phone_link["fingerprint"], "code": code},
                             separators=(",", ":"))
        import io

        import segno

        svg = io.BytesIO()
        segno.make(payload, error="m").save(svg, kind="svg", scale=6, border=2, dark="#111", light="#fff")
        return {"code": code, "expires_in": seconds, "url": urls[0], "urls": urls,
                "fingerprint": phone_link["fingerprint"], "qr_svg": svg.getvalue().decode()}

    @app.get("/api/mobile/addresses")
    def mobile_addresses(user: dict = Depends(current_user)) -> dict:
        """Where this vault can be reached right now (Wi-Fi, Tailscale): a paired phone refreshes its
        list whenever it connects, so a changed home address doesn't strand it."""
        return {"urls": current_urls()}

    @app.post("/api/mobile/pair")
    def pair_phone(req: PairRequest, request: Request) -> dict:
        throttle = [f"pair:{_client(request)}"]
        try:
            accounts.throttle(throttle)
            user, device, token = devices.pair(req.code, req.device_name)
        except TooManyAttempts as e:
            raise HTTPException(429, str(e)) from e
        except AuthError as e:
            accounts.record_failure(throttle)
            raise HTTPException(401, str(e)) from e
        logger.info("%s paired a phone (%s)", user["name"], device["name"])
        return {"token": token, "user": user, "device": device}

    @app.get("/api/devices")
    def list_devices(user: dict = Depends(current_user)) -> dict:
        urls = current_urls()
        return {"enabled": phone_link is not None, "url": urls[0] if urls else None, "urls": urls,
                "tunnel": any(u.startswith("https://100.") for u in urls), "devices": devices.list_for(user["id"])}

    @app.delete("/api/devices/{device_id}")
    def remove_device(device_id: int, user: dict = Depends(current_user)) -> dict:
        if not devices.revoke(user["id"], device_id):
            raise HTTPException(404, "phone not found")
        logger.info("%s removed a paired phone", user["name"])
        return {"removed": device_id}

    # ---------- documents ----------

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", CSP)
        return response

    @app.get("/api/info")
    def info(user: dict = Depends(current_user)) -> dict:
        mine = vault_of(user)
        counts = Counter(d["category"] for d in mine.documents.values())
        return {
            "model": embedder.model_name,
            "owner": mine.owner,
            "is_admin": user["is_admin"],
            "llm": llm.name if llm else None,
            "llm_status": _llm_status(llm),
            "num_documents": len(mine.documents),
            "num_chunks": len(mine.chunks),
            "categories": {c: counts.get(c, 0) for c in CATEGORIES},
            "ocr_available": ocr_available(),
            "supported_types": sorted(SUPPORTED_SUFFIXES),
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        }

    @app.get("/api/documents")
    def list_documents(user: dict = Depends(current_user)) -> list[dict]:
        return [_public(d) for d in vault_of(user).list_documents()]

    @app.post("/api/documents")
    def upload(files: list[UploadFile] = File(...), category: str = Form("auto"),
               password: str | None = Form(None, max_length=200),
               user: dict = Depends(current_user)) -> list[dict]:
        """`password` opens a locked PDF (e-Aadhaar, e-PAN); without it such a file comes back with
        status "error" and code "password_required", and the page asks for it."""
        mine = vault_of(user)
        if category != "auto" and category not in CATEGORIES:
            raise HTTPException(400, f"category must be 'auto' or one of {CATEGORIES}")
        results = []
        for upload_file in files:
            name = Path(upload_file.filename or "document").name
            suffix = Path(name).suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES:
                results.append({"filename": name, "status": "error",
                                "error": f"Unsupported file type. Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"})
                continue
            with tempfile.TemporaryDirectory() as tmp:
                # Keep the extension: extraction picks its parser from it.
                tmp_path = Path(tmp) / f"upload{suffix}"
                size = 0
                with open(tmp_path, "wb") as out:
                    while block := upload_file.file.read(1 << 20):
                        size += len(block)
                        if size > MAX_UPLOAD_BYTES:
                            break
                        out.write(block)
                if size > MAX_UPLOAD_BYTES:
                    results.append({"filename": name, "status": "error",
                                    "error": f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"})
                    continue
                try:
                    result = ingest_file(tmp_path, mine, embedder, tokenizer, category, filename=name,
                                         classifier=classifier, facts_reader=facts_reader, password=password)
                except PasswordRequiredError as e:
                    results.append({"filename": name, "status": "error", "error": str(e),
                                    "code": "wrong_password" if e.wrong else "password_required"})
                    continue
                except (UnsupportedFileError, OcrUnavailableError, EmptyDocumentError, ValueError) as e:
                    results.append({"filename": name, "status": "error", "error": str(e)})
                    continue
                except Exception:  # a corrupt file shouldn't take the server down
                    logger.exception("Failed to ingest %s", name)
                    results.append({"filename": name, "status": "error", "error": "Could not read this file."})
                    continue
            results.append({"filename": name, "status": "added" if result.created else "duplicate",
                            "document": _public(result.document)})
        return results

    @app.get("/api/documents/{doc_id}/file")
    def open_file(doc_id: str, user: dict = Depends(current_user)) -> FileResponse:
        doc, path = _owned_file(user, doc_id)
        return FileResponse(path, filename=doc["filename"], content_disposition_type="inline")

    def _owned_file(user: dict, doc_id: str) -> tuple[dict, Path]:
        mine = vault_of(user)
        doc = mine.get(doc_id)  # only this person's documents are visible
        path = mine.file_path(doc_id)
        if not doc or not path or not path.exists():
            raise HTTPException(404, "document not found")
        return doc, path

    @app.get("/api/documents/{doc_id}/pages/{page}/image")
    def page_image_route(doc_id: str, page: int, user: dict = Depends(current_user)) -> Response:
        _, path = _owned_file(user, doc_id)
        if path.suffix.lower() != ".pdf" and path.suffix.lower() not in IMAGE_SUFFIXES:
            raise HTTPException(400, "page images are only available for PDFs, scans and photos")
        try:
            image, media_type = page_image(path, page)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return Response(image, media_type=media_type, headers={"Cache-Control": "private, no-store"})

    @app.post("/api/documents/{doc_id}/pages/{page}/highlights")
    def page_highlights(doc_id: str, page: int, req: HighlightRequest, user: dict = Depends(current_user)) -> dict:
        _, path = _owned_file(user, doc_id)
        chunk = next((c for c in vault_of(user).chunks if c["chunk_id"] == req.chunk_id and c["doc_id"] == doc_id), None)
        try:
            result = highlights_for(path, page, chunk["text"] if chunk else None, req.answer)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return asdict(result)

    @app.patch("/api/documents/{doc_id}")
    def update_document(doc_id: str, req: DocumentUpdate, user: dict = Depends(current_user)) -> dict:
        if req.category is not None and req.category not in CATEGORIES:
            raise HTTPException(400, f"category must be one of {CATEGORIES}")
        mine = vault_of(user)
        doc = mine.get(doc_id)
        if doc is None:
            raise HTTPException(404, "document not found")
        sent = req.model_dump(exclude_unset=True)
        facts = {k: v for k, v in sent.items() if k != "category"}
        if facts:
            if "amount" in facts and facts["amount"] is not None:  # "Rs. 1,000.50" -> "1000.50"
                number = re.search(r"\d[\d,]*(?:\.\d+)?", facts["amount"])
                facts["amount"] = number.group().replace(",", "") if number else None
            facts = {k: (v.isoformat() if isinstance(v, date) else (v or None)) for k, v in facts.items()}
            doc = mine.update_facts(doc_id, facts, "person")  # corrected by a person: never overwritten
        if req.category is not None:
            from backend.upload.ingest import passage_texts

            chunks = [{**c, "category": req.category} for c in mine.chunks_of(doc_id)]
            vectors = embedder.encode_passages(passage_texts(chunks)) if chunks else None
            doc = mine.update_category(doc_id, req.category, vectors)
        return _public(doc)

    @app.get("/api/upcoming")
    def upcoming(days: int = Query(90, ge=1, le=730), user: dict = Depends(current_user)) -> list[dict]:
        """Expiries and due dates from 30 days ago (recently missed) to `days` ahead, soonest first.
        The base for reminders (Phase 10)."""
        today = date.today()
        items = []
        for doc in vault_of(user).documents.values():
            for field, kind in (("expires_on", "expires"), ("due_on", "due")):
                if doc.get(field):
                    when = date.fromisoformat(doc[field])
                    left = (when - today).days
                    if -30 <= left <= days:
                        items.append({"doc_id": doc["doc_id"], "filename": doc["filename"], "category": doc["category"],
                                      "kind": kind, "date": doc[field], "days_left": left,
                                      "amount": doc.get("amount"), "issuer": doc.get("issuer")})
        return sorted(items, key=lambda i: i["date"])

    # ---------- reminders (Phase 10) ----------

    @app.get("/api/reminders")
    def list_reminders(user: dict = Depends(current_user)) -> dict:
        return {"items": reminders.items(vault_of(user)), "settings": reminders.settings(user["id"])}

    @app.post("/api/reminders/done")
    def reminder_done(req: ReminderMark, user: dict = Depends(current_user)) -> dict:
        doc = vault_of(user).get(req.doc_id)
        if doc is None or not doc.get("expires_on" if req.kind == "expires" else "due_on"):
            raise HTTPException(404, "no such reminder")
        reminders.mark_done(doc, req.kind, req.done)
        return {"items": reminders.items(vault_of(user))}

    @app.post("/api/reminders/settings")
    def reminder_settings_save(req: ReminderSettings, user: dict = Depends(current_user)) -> dict:
        try:
            return reminders.save_settings(user["id"], req.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/reminders/schedule")
    def reminder_schedule(user: dict = Depends(current_user)) -> dict:
        """Every future reminder, for the phone app to schedule as phone notifications."""
        return {"time_of_day": reminders.settings(user["id"])["time_of_day"],
                "reminders": reminders.schedule(vault_of(user))}

    @app.get("/api/reminders/calendar.ics")
    def reminder_calendar(user: dict = Depends(current_user)) -> Response:
        return Response(reminders.calendar_file(vault_of(user)), media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="document-vault.ics"',
                                 "Cache-Control": "private, no-store"})

    def settings_view(user: dict) -> dict:
        list_models = getattr(llm, "list_models", None)
        return {
            "owner": vault_of(user).owner,
            "is_admin": user["is_admin"],
            "llm_model": getattr(llm, "model", None) if llm else None,
            "llm_models": list_models() if list_models else [],
            "llm_status": _llm_status(llm),
            "embedding_model": embedder.model_name,
            "database": database_label or "SQLite file in the vault folder",
            "index_dir": str(store.root),
            "ocr_available": ocr_available(),
            "search_method": search_method,
            "facts_reader": facts_reader.name,
        }

    @app.get("/api/settings")
    def get_settings(user: dict = Depends(current_user)) -> dict:
        return settings_view(user)

    @app.post("/api/settings")
    def save_settings(req: SettingsUpdate, user: dict = Depends(current_user)) -> dict:
        if req.owner_name is not None:
            mine = vault_of(user)
            old = mine.owner
            try:
                mine.rename_owner(req.owner_name)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            logger.info("%s is now called %s", old, mine.owner)
            user = {**user, "name": mine.owner}
        if req.llm_model is None:
            return settings_view(user)
        if not user["is_admin"]:  # one model serves the whole family
            raise HTTPException(403, "Only an admin can change the answer model.")
        if llm is None or not hasattr(llm, "model"):
            raise HTTPException(400, "Answers are turned off; start the app without --no-llm to choose a model.")
        installed = llm.list_models() if hasattr(llm, "list_models") else []
        if req.llm_model not in installed:
            raise HTTPException(400, f"{req.llm_model} isn't downloaded. Run: ollama pull {req.llm_model}")
        llm.model = req.llm_model
        logger.info("Answer model switched to %s by %s", req.llm_model, user["name"])
        return settings_view(user)

    @app.delete("/api/documents/{doc_id}")
    def delete(doc_id: str, user: dict = Depends(current_user)) -> dict:
        if not vault_of(user).delete(doc_id):
            raise HTTPException(404, "document not found")
        return {"deleted": doc_id}

    @app.get("/api/search")
    def search(
        q: str = Query(..., min_length=1, max_length=500),
        k: int = Query(5, ge=1, le=20),
        category: str | None = None,
        method: str | None = None,
        user: dict = Depends(current_user),
    ) -> dict:
        if category and category not in CATEGORIES:
            raise HTTPException(400, f"category must be one of {CATEGORIES}")
        if method and method not in METHODS:
            raise HTTPException(400, f"method must be one of {METHODS}")
        method = method or search_method
        t0 = time.perf_counter()
        hits = retriever_for(user, method).documents(q, top_k=k, category=category or None)
        took_ms = (time.perf_counter() - t0) * 1000
        return {
            "question": q,
            "category": category,
            "method": method,
            "took_ms": round(took_ms, 1),
            "results": [_passage(chunk, score, i + 1) for i, (chunk, score) in enumerate(hits)],
        }

    def chat_context(user: dict, question: str, history: list[ChatTurn], k: int, category: str | None):
        """Passages for a chat turn. A follow-up ("and when is it due?") says little on its
        own, so also search with the previous question and add those passages at the end."""
        retriever = retriever_for(user)
        hits = retriever.context(question, top_k=k, category=category)
        previous = next((t.content for t in reversed(history) if t.role == "user"), None)
        if previous:
            seen = {c["chunk_id"] for c, _ in hits}
            for chunk, score in retriever.context(f"{previous} {question}", top_k=3, category=category):
                if chunk["chunk_id"] not in seen:
                    hits.append((chunk, score))
                    seen.add(chunk["chunk_id"])
        return hits

    @app.post("/api/chat")
    def chat(req: ChatRequest, user: dict = Depends(current_user)) -> StreamingResponse:
        if req.category and req.category not in CATEGORIES:
            raise HTTPException(400, f"category must be one of {CATEGORIES}")
        if llm is None:
            raise HTTPException(503, "Answers are turned off (the app was started with --no-llm).")
        hits = chat_context(user, req.question, req.history, req.k, req.category or None)
        sources = [_passage(chunk, score, i + 1) for i, (chunk, score) in enumerate(hits)]
        history = [t.model_dump() for t in req.history]

        def events():
            yield json.dumps({"type": "sources", "sources": sources}) + "\n"
            text = ""
            try:
                for piece in stream_answer(req.question, [c for c, _ in hits], llm, history=history):
                    text += piece
                    yield json.dumps({"type": "token", "text": piece}) + "\n"
            except LLMUnavailableError as e:
                yield json.dumps({"type": "error", "message": str(e)}) + "\n"
                return
            except Exception:  # never leave the chat bubble hanging
                logger.exception("Answer failed")
                yield json.dumps({"type": "error", "message": "Something went wrong while writing the answer."}) + "\n"
                return
            answer = finalize(text, len(hits))
            yield json.dumps({"type": "done", "citations": answer.citations, "found": answer.found,
                              "llm": llm.name}) + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/api/ask")
    def ask(req: AskRequest, user: dict = Depends(current_user)) -> dict:
        if req.category and req.category not in CATEGORIES:
            raise HTTPException(400, f"category must be one of {CATEGORIES}")
        if llm is None:
            raise HTTPException(503, "No local LLM is configured. Start the app without --no-llm.")
        t0 = time.perf_counter()
        hits = retriever_for(user).context(req.question, top_k=req.k, category=req.category or None)
        retrieval_ms = (time.perf_counter() - t0) * 1000
        try:
            answer = answer_question(req.question, [chunk for chunk, _ in hits], llm)
        except LLMUnavailableError as e:
            raise HTTPException(503, str(e)) from e
        return {
            "question": req.question,
            "answer": answer.text,
            "found": answer.found,
            "citations": answer.citations,
            "sources": [_passage(chunk, score, i + 1) for i, (chunk, score) in enumerate(hits)],
            "llm": llm.name,
            "retrieval_ms": round(retrieval_ms, 1),
            "took_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    if mode == "phone":
        return app
    if frontend_dir and (frontend_dir / "index.html").exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="web")  # after the API routes
    else:
        @app.get("/", include_in_schema=False)
        def not_built() -> Response:
            return Response(NOT_BUILT, media_type="text/html")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index-dir", default="data/index", help="FAISS vectors and original files")
    parser.add_argument("--database-url", default=None,
                        help="metadata database (default: $VAULT_DATABASE_URL or postgresql+psycopg://localhost/vault)")
    parser.add_argument("--owner", default=None,
                        help="before sign-in is set up: whose documents the setup screen adopts (default: your user name)")
    parser.add_argument("--model", default=None, help="embedding model (default: snowflake-arctic-embed-m-v1.5)")
    parser.add_argument("--device", default=None, help="cuda / mps / cpu; default picks automatically")
    parser.add_argument("--llm-model", default=None, help="local Ollama model (default: llama3.1:8b)")
    parser.add_argument("--no-llm", action="store_true", help="search only, no answers")
    parser.add_argument("--search", default="hybrid", choices=METHODS,
                        help="hybrid (meaning + exact words, default), vector (meaning only) or keyword (BM25 only)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--phones", action="store_true",
                        help="also serve the phone app over HTTPS on the home network (see backend/api/lan.py)")
    parser.add_argument("--phone-port", type=int, default=8443)
    parser.add_argument("--phone-address", action="append", default=[],
                        help="extra address phones may use (repeatable), e.g. a Tailscale name")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import uvicorn

    from backend.chunker.chunker import HFTokenizer
    from backend.embeddings.embedder import DEFAULT_MODEL, Embedder
    from backend.llm.ollama import DEFAULT_MODEL as DEFAULT_LLM
    from backend.llm.ollama import OllamaLLM
    from backend.upload.ingest import passage_texts

    from backend.database.db import DatabaseUnavailableError, database_url, default_owner

    try:
        store = DocumentStore(args.index_dir, database_url(args.database_url), owner=default_owner(args.owner))
    except DatabaseUnavailableError as e:
        raise SystemExit(str(e)) from e
    embedder = Embedder(args.model or DEFAULT_MODEL, device=args.device)
    if store.model and store.model != embedder.model_name:
        logger.info("Vault was embedded with %s; re-embedding with %s", store.model, embedder.model_name)
        store.reembed(lambda chunks: embedder.encode_passages(passage_texts(chunks)), embedder.model_name)
    elif store.missing_vectors:
        logger.warning("Some passages have no search vectors (interrupted save?); rebuilding them")
        store.reembed(lambda chunks: embedder.encode_passages(passage_texts(chunks)), embedder.model_name)
    if not ocr_available():
        logger.warning("Tesseract not found: photos and scanned PDFs can't be read. macOS: brew install tesseract")

    llm = None if args.no_llm else OllamaLLM(args.llm_model or DEFAULT_LLM)
    from sqlalchemy.engine import make_url

    db_label = make_url(database_url(args.database_url)).render_as_string(hide_password=True)
    accounts, devices = Accounts(store.engine), Devices(store.engine)
    tokenizer = HFTokenizer(embedder.model_name)
    phone_server, phone_link = None, None
    if args.phones:
        from backend.api.lan import ensure_certificate, keep_awake, phone_urls

        cert, key, fp = ensure_certificate(Path(args.index_dir).parent / "tls")
        phone_link = {"urls": lambda: phone_urls(args.phone_port, args.phone_address), "fingerprint": fp}
        phone_app = create_app(store, embedder, tokenizer, llm, search_method=args.search, accounts=accounts,
                               mode="phone", devices=devices, phone_link=phone_link)
        phone_server = uvicorn.Server(uvicorn.Config(phone_app, host="0.0.0.0", port=args.phone_port,
                                                     ssl_certfile=str(cert), ssl_keyfile=str(key), log_level="warning"))
        import threading

        threading.Thread(target=phone_server.run, name="phones", daemon=True).start()
        keep_awake()
        urls = phone_link["urls"]()
        logger.info("Phone access on %s (certificate %s…). Pair phones in Settings -> Phones.",
                    ", ".join(urls) or "no network yet", fp[:16])
        if not any(u.startswith("https://100.") for u in urls):
            logger.info("Tailscale isn't running: phones can reach the vault on this Wi-Fi only. "
                        "For anywhere access see README -> Phone app -> From anywhere.")
    app = create_app(store, embedder, tokenizer, llm, database_label=db_label, search_method=args.search,
                     accounts=accounts, devices=devices, phone_link=phone_link)
    if accounts.setup_needed():
        logger.info("First start with sign-in: open http://localhost:%d and choose your password.", args.port)
    else:
        logger.info("%d family members. Answers: %s. Open http://localhost:%d",
                    len(accounts.list_users()), llm.name if llm else "off", args.port)
    from backend.reminders import desktop
    from backend.reminders.reminders import Reminders as _Reminders

    stop_reminders = desktop.start(store, _Reminders(store.engine))  # Mac notifications while running
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port)
    finally:
        stop_reminders.set()
        if phone_server:
            phone_server.should_exit = True


if __name__ == "__main__":
    main()
