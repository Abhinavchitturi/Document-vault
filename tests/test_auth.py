"""Phase 6: sign-in, family accounts, and each person seeing only their own documents."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api.app import SESSION_COOKIE, create_app
from backend.auth.accounts import Accounts, AuthError
from backend.auth.passwords import hash_password, verify_password
from backend.chunker.chunker import RegexTokenizer
from backend.database.schema import sessions
from backend.upload.ingest import ingest_file
from backend.vector_store.store import DocumentStore
from tests.fakes import PASSWORD, FakeEmbedder, signed_in

SUNITA = "sunita's password 1"


def make_app(tmp_path, owner="abhinav", frontend_dir=None):
    store = DocumentStore(tmp_path / "index", owner=owner)
    return store, create_app(store, FakeEmbedder(), RegexTokenizer(), frontend_dir=frontend_dir)


def upload(client, name, content):
    return client.post("/api/documents", files=[("files", (name, content))]).json()[0]


def login(app, name, password):
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"name": name, "password": password})
    assert r.status_code == 200, r.text
    return client


def test_passwords_are_salted_scrypt():
    a, b = hash_password(PASSWORD), hash_password(PASSWORD)
    assert a.startswith("scrypt$") and a != b and PASSWORD not in a
    assert verify_password(PASSWORD, a) and not verify_password("wrong password", a)
    assert not verify_password(PASSWORD, None) and not verify_password(PASSWORD, "garbage")


def test_nothing_is_readable_before_signing_in(tmp_path):
    site = tmp_path / "out"
    site.mkdir()
    (site / "index.html").write_text("<!doctype html>app")
    _, app = make_app(tmp_path, frontend_dir=site)
    client = TestClient(app)
    for method, path in [("get", "/api/info"), ("get", "/api/documents"), ("get", "/api/search?q=bill"),
                         ("get", "/api/settings"), ("get", "/api/documents/x/file"), ("post", "/api/chat"),
                         ("get", "/api/users"), ("post", "/api/auth/password")]:
        assert getattr(client, method)(path).status_code == 401, path
    assert client.get("/").status_code == 200  # the page itself holds no data; it shows the sign-in screen
    status = client.get("/api/auth/status").json()
    assert (status["user"], status["setup_needed"], status["suggested_name"]) == (None, True, "abhinav")


def test_setup_adopts_the_existing_owner_and_happens_once(tmp_path):
    store, app = make_app(tmp_path)
    (tmp_path / "bill.txt").write_text("Water bill due 15 July")
    ingest_file(tmp_path / "bill.txt", store, FakeEmbedder(), RegexTokenizer())
    weak = TestClient(app).post("/api/auth/setup", json={"name": "Abhinav", "password": "short"})
    assert weak.status_code == 400 and "8 characters" in weak.json()["detail"]

    client = signed_in(app, name="Abhinav")  # typed with a capital: the owner is renamed, documents kept
    me = client.get("/api/auth/status").json()["user"]
    assert me["name"] == "Abhinav" and me["is_admin"]
    assert [d["filename"] for d in client.get("/api/documents").json()] == ["bill.txt"]
    again = TestClient(app).post("/api/auth/setup", json={"name": "intruder", "password": "intruder pass"})
    assert again.status_code == 400
    assert DocumentStore(tmp_path / "index").owner == "Abhinav"  # restarts open the admin's view


def test_sign_in_out_and_cookie(tmp_path):
    store, app = make_app(tmp_path)
    signed_in(app)
    wrong = TestClient(app).post("/api/auth/login", json={"name": "abhinav", "password": "not it at all"})
    assert wrong.status_code == 401 and wrong.json()["detail"] == "Wrong name or password."
    nobody = TestClient(app).post("/api/auth/login", json={"name": "nobody", "password": PASSWORD})
    assert nobody.json()["detail"] == "Wrong name or password."  # doesn't reveal which names exist

    client = TestClient(app)
    r = client.post("/api/auth/login", json={"name": "ABHINAV", "password": PASSWORD})  # any capitalization
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=" in cookie
    token = client.cookies[SESSION_COOKIE]
    with store.engine.connect() as conn:
        stored = conn.execute(select(sessions.c.token_hash)).scalars().all()
    assert token not in stored  # only the hash is kept

    assert client.get("/api/info").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/info").status_code == 401
    replay = TestClient(app)
    replay.cookies.set(SESSION_COOKIE, token)
    assert replay.get("/api/info").status_code == 401  # a signed-out token is dead for good


def test_each_person_sees_only_their_own_documents(tmp_path):
    store, app = make_app(tmp_path)
    admin = signed_in(app)
    mine = upload(admin, "car_insurance.txt", b"Car insurance policy KMI/PC/25/0093318 expires 14 March 2027")["document"]
    assert admin.post("/api/users", json={"name": "Sunita", "password": SUNITA}).status_code == 200
    sunita = login(app, "sunita", SUNITA)

    assert sunita.get("/api/documents").json() == []
    assert sunita.get("/api/search", params={"q": "KMI/PC/25/0093318"}).json()["results"] == []
    doc = mine["doc_id"]
    assert sunita.get(f"/api/documents/{doc}/file").status_code == 404
    assert sunita.get(f"/api/documents/{doc}/pages/1/image").status_code == 404
    assert sunita.post(f"/api/documents/{doc}/pages/1/highlights", json={}).status_code == 404
    assert sunita.patch(f"/api/documents/{doc}", json={"category": "bill"}).status_code == 404
    assert sunita.delete(f"/api/documents/{doc}").status_code == 404

    hers = upload(sunita, "car_insurance.txt", b"Car insurance policy KMI/PC/25/0093318 expires 14 March 2027")
    assert hers["status"] == "added" and hers["document"]["doc_id"] != doc  # same file, her own copy
    assert [r["doc_id"] for r in sunita.get("/api/search", params={"q": "KMI/PC/25/0093318"}).json()["results"]] == \
        [hers["document"]["doc_id"]]
    assert [d["doc_id"] for d in admin.get("/api/documents").json()] == [doc]

    # The vector index is shared: rebuilding it from one person's view keeps everyone's vectors.
    store.for_user(sunita.get("/api/auth/status").json()["user"]["id"]).reembed(
        lambda chunks: FakeEmbedder().encode_passages([c["text"] for c in chunks]), "fake/bow")
    assert not store.missing_vectors
    assert admin.get("/api/search", params={"q": "car insurance", "method": "vector"}).json()["results"][0]["doc_id"] == doc


def test_only_admins_manage_members_and_the_model(tmp_path):
    _, app = make_app(tmp_path)
    admin = signed_in(app)
    admin.post("/api/users", json={"name": "Sunita", "password": SUNITA})
    sunita = login(app, "Sunita", SUNITA)
    assert sunita.get("/api/users").status_code == 403
    assert sunita.post("/api/users", json={"name": "Guest", "password": "guest password"}).status_code == 403
    assert sunita.post("/api/users/1/password", json={"new_password": "taken over!!"}).status_code == 403
    assert sunita.post("/api/settings", json={"llm_model": "x"}).status_code == 403
    members = admin.get("/api/users").json()
    assert [(m["name"], m["is_admin"], m["can_sign_in"]) for m in members] == [("abhinav", True, True), ("Sunita", False, True)]
    assert "password_hash" not in members[0]
    assert admin.post("/api/users", json={"name": "sunita", "password": SUNITA}).status_code == 400  # already has one


def test_password_change_and_reset_sign_out_other_sessions(tmp_path):
    _, app = make_app(tmp_path)
    admin = signed_in(app)
    sunita_id = admin.post("/api/users", json={"name": "Sunita", "password": SUNITA}).json()["id"]
    laptop, phone = login(app, "Sunita", SUNITA), login(app, "Sunita", SUNITA)

    bad = laptop.post("/api/auth/password", json={"current_password": "nope nope", "new_password": "new password 2"})
    assert bad.status_code == 400
    ok = laptop.post("/api/auth/password", json={"current_password": SUNITA, "new_password": "new password 2"})
    assert ok.status_code == 200
    assert laptop.get("/api/info").status_code == 200 and phone.get("/api/info").status_code == 401

    # Forgotten password: the admin sets a new one; every session of hers ends.
    assert admin.post(f"/api/users/{sunita_id}/password", json={"new_password": "from the admin"}).status_code == 200
    assert laptop.get("/api/info").status_code == 401
    assert TestClient(app).post("/api/auth/login", json={"name": "Sunita", "password": "new password 2"}).status_code == 401
    login(app, "Sunita", "from the admin")


def test_repeated_wrong_passwords_lock_the_name(tmp_path):
    _, app = make_app(tmp_path)
    signed_in(app)
    client = TestClient(app)
    for _ in range(5):
        assert client.post("/api/auth/login", json={"name": "abhinav", "password": "guess guess"}).status_code == 401
    locked = client.post("/api/auth/login", json={"name": "abhinav", "password": PASSWORD})
    assert locked.status_code == 429 and "Try again in" in locked.json()["detail"]


def test_requests_from_other_websites_are_refused(tmp_path):
    _, app = make_app(tmp_path)
    client = signed_in(app)
    evil = client.post("/api/documents", files=[("files", ("x.txt", b"x"))], headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    login_csrf = TestClient(app).post("/api/auth/login", json={"name": "abhinav", "password": PASSWORD},
                                      headers={"Origin": "http://attacker.test"})
    assert login_csrf.status_code == 403
    same = client.post("/api/documents", files=[("files", ("x.txt", b"hello there"))], headers={"Origin": "http://testserver"})
    assert same.status_code == 200


def test_first_password_from_the_terminal_makes_an_admin(tmp_path):
    store = DocumentStore(tmp_path / "index", owner="abhinav")
    accounts = Accounts(store.engine)
    accounts.set_password(accounts.find_user("Abhinav")["id"], "set from terminal")
    assert accounts.list_users()[0]["is_admin"] and not accounts.setup_needed()
    try:
        accounts.set_password(999, "whatever password")
        raise AssertionError("expected AuthError")
    except AuthError:
        pass


def test_older_users_table_gets_sign_in_columns(tmp_path):
    import sqlite3

    from sqlalchemy import inspect

    from backend.database.db import connect

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(100) UNIQUE NOT NULL, email VARCHAR(320),"
                     " created_at TIMESTAMP NOT NULL)")
        conn.execute("INSERT INTO users (name, created_at) VALUES ('abhinav', '2026-01-01 00:00:00')")
    engine = connect(f"sqlite:///{path}")
    assert {"password_hash", "is_admin"} <= {c["name"] for c in inspect(engine).get_columns("users")}
    assert Accounts(engine).setup_needed() and Accounts(engine).suggested_name() == "abhinav"
