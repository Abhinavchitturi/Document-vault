"""Phase 7: pairing phones, and the phone listener only serving paired phones."""

import json
import stat

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.lan import ensure_certificate
from backend.auth.accounts import Accounts
from backend.auth.devices import Devices
from backend.chunker.chunker import RegexTokenizer
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder, signed_in

LINK = {"urls": ["https://192.168.1.20:8443"], "fingerprint": "ab" * 32}
SUNITA = "sunita's password 1"


def apps(tmp_path, link=LINK):
    store = DocumentStore(tmp_path / "index", owner="abhinav")
    accounts, devices = Accounts(store.engine), Devices(store.engine)
    common = dict(accounts=accounts, devices=devices, frontend_dir=None)
    web = create_app(store, FakeEmbedder(), RegexTokenizer(), phone_link=link, **common)
    phone = create_app(store, FakeEmbedder(), RegexTokenizer(), mode="phone", phone_link=link, **common)
    return web, phone


def pair(web_client, phone_app, name="Abhinav's iPhone"):
    qr = web_client.post("/api/mobile/pairing").json()
    phone = TestClient(phone_app, base_url="https://192.168.1.20:8443")
    r = phone.post("/api/mobile/pair", json={"code": qr["code"], "device_name": name})
    assert r.status_code == 200, r.text
    phone.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return phone, qr


def test_pairing_qr_holds_address_fingerprint_and_code(tmp_path):
    web, phone_app = apps(tmp_path)
    me = signed_in(web)
    qr = me.post("/api/mobile/pairing").json()
    assert qr["url"] == LINK["urls"][0] and qr["fingerprint"] == LINK["fingerprint"] and qr["expires_in"] == 600
    assert qr["qr_svg"].startswith("<?xml") or "<svg" in qr["qr_svg"]
    assert TestClient(web).post("/api/mobile/pairing").status_code == 401  # signed-in people only
    assert me.post("/api/mobile/pair", json={"code": qr["code"]}).status_code == 404  # phones pair on the phone listener

    web_off, _ = apps(tmp_path / "off", link=None)
    off = signed_in(web_off).post("/api/mobile/pairing")
    assert off.status_code == 409 and "--phones" in off.json()["detail"]


def test_a_paired_phone_works_as_its_owner(tmp_path):
    web, phone_app = apps(tmp_path)
    me = signed_in(web)
    phone, qr = pair(me, phone_app)
    assert phone.get("/api/info").json()["owner"] == "abhinav"
    photo = phone.post("/api/documents", files=[("files", ("bill.txt", b"Gas bill Rs. 1,986 due 20 August 2026"))]).json()[0]
    assert photo["status"] == "added"
    assert [d["filename"] for d in me.get("/api/documents").json()] == ["bill.txt"]  # same vault on the Mac
    assert phone.get("/api/search", params={"q": "gas bill"}).json()["results"][0]["filename"] == "bill.txt"
    devices = me.get("/api/devices").json()
    assert devices["enabled"] and [d["name"] for d in devices["devices"]] == ["Abhinav's iPhone"]

    again = TestClient(phone_app).post("/api/mobile/pair", json={"code": qr["code"], "device_name": "x"})
    assert again.status_code == 401  # a code works once


def test_phone_listener_serves_only_paired_phones(tmp_path):
    web, phone_app = apps(tmp_path)
    signed_in(web)
    stranger = TestClient(phone_app)
    assert stranger.get("/api/mobile/hello").json()["app"] == "document-vault"
    assert stranger.get("/").status_code == 404  # no web pages on the network
    assert stranger.get("/api/documents").status_code == 401
    assert stranger.get("/api/documents", headers={"Authorization": "Bearer made-up"}).status_code == 401
    guess = stranger.post("/api/auth/login", json={"name": "abhinav", "password": "correct horse battery"})
    assert guess.status_code == 401  # no password sign-in from the network, so no guessing either
    for _ in range(5):
        stranger.post("/api/mobile/pair", json={"code": "guess"})
    assert stranger.post("/api/mobile/pair", json={"code": "guess"}).status_code == 429


def test_removing_a_phone_or_resetting_the_password_cuts_it_off(tmp_path):
    web, phone_app = apps(tmp_path)
    admin = signed_in(web)
    phone, _ = pair(admin, phone_app)
    device_id = admin.get("/api/devices").json()["devices"][0]["id"]
    assert admin.delete(f"/api/devices/{device_id}").status_code == 200
    assert phone.get("/api/info").status_code == 401

    sunita_id = admin.post("/api/users", json={"name": "Sunita", "password": SUNITA}).json()["id"]
    sunita = TestClient(web)
    sunita.post("/api/auth/login", json={"name": "Sunita", "password": SUNITA})
    her_phone, _ = pair(sunita, phone_app, "Sunita's phone")
    assert her_phone.get("/api/documents").json() == []  # her own vault, not the admin's
    her_device = sunita.get("/api/devices").json()["devices"][0]["id"]
    assert admin.delete(f"/api/devices/{her_device}").status_code == 404  # only she can remove her phones
    admin.post(f"/api/users/{sunita_id}/password", json={"new_password": "brand new one"})
    assert her_phone.get("/api/info").status_code == 401


def test_certificate_is_made_once_and_kept_on_every_network(tmp_path):
    cert, key, fp = ensure_certificate(tmp_path / "tls")
    assert len(fp) == 64 and stat.S_IMODE(key.stat().st_mode) == 0o600
    assert ensure_certificate(tmp_path / "tls")[2] == fp  # same certificate next start, whatever the Wi-Fi
    assert json.dumps(fp)  # plain hex, fits in the QR payload


def test_phones_get_every_address_and_refresh_them(tmp_path):
    urls = ["https://192.168.1.20:8443", "https://100.101.102.103:8443"]
    link = {"urls": lambda: list(urls), "fingerprint": "ab" * 32}
    web, phone_app = apps(tmp_path, link=link)
    me = signed_in(web)
    qr = me.post("/api/mobile/pairing").json()
    assert qr["urls"] == urls and qr["url"] == urls[0]  # Wi-Fi first, then the tunnel
    assert me.get("/api/devices").json()["tunnel"] is True
    phone, _ = pair(me, phone_app)
    urls[0] = "https://192.168.1.44:8443"  # the router gave the Mac a new address
    assert phone.get("/api/mobile/addresses").json()["urls"][0] == "https://192.168.1.44:8443"
    assert TestClient(phone_app).get("/api/mobile/addresses").status_code == 401  # paired phones only


def test_phone_urls_lists_wifi_then_tunnel(monkeypatch):
    from backend.api import lan

    monkeypatch.setattr(lan, "lan_address", lambda: "192.168.1.20")
    monkeypatch.setattr(lan, "tailscale_address", lambda: "100.101.102.103")
    assert lan.phone_urls(8443, ["192.168.1.20", "mac.tail1234.ts.net"]) == [
        "https://192.168.1.20:8443", "https://100.101.102.103:8443", "https://mac.tail1234.ts.net:8443"]
    monkeypatch.setattr(lan, "lan_address", lambda: None)
    monkeypatch.setattr(lan, "tailscale_address", lambda: None)
    assert lan.phone_urls(8443) == []
