"""Passkeys: Touch ID / Face ID / fingerprint sign-in, verified with real signatures."""

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder, signed_in
from tests.webauthn_device import SoftDevice

SUNITA = "sunita's password 1"


def make_app(tmp_path, hosts=("testserver",)):
    store = DocumentStore(tmp_path / "index", owner="abhinav")
    return create_app(store, FakeEmbedder(), RegexTokenizer(), frontend_dir=None, passkey_hosts=hosts)


def add_passkey(client, device, name="Mac"):
    start = client.post("/api/passkeys/options").json()
    return client.post("/api/passkeys", json={"ceremony": start["ceremony"], "name": name,
                                              "credential": device.create(start["options"])})


def passkey_sign_in(app, device, **kw):
    client = TestClient(app)
    start = client.post("/api/auth/passkey/options").json()
    r = client.post("/api/auth/passkey", json={"ceremony": start["ceremony"], "credential": device.get(start["options"], **kw)})
    return client, r


def test_add_a_passkey_then_sign_in_with_it(tmp_path):
    app = make_app(tmp_path)
    me = signed_in(app)
    mac = SoftDevice()
    added = add_passkey(me, mac)
    assert added.status_code == 200 and added.json()["name"] == "Mac"
    assert [p["name"] for p in me.get("/api/passkeys").json()] == ["Mac"]

    client, r = passkey_sign_in(app, mac)
    assert r.status_code == 200 and r.json()["user"]["name"] == "abhinav"
    assert "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/info").json()["owner"] == "abhinav"
    assert me.get("/api/passkeys").json()[0]["last_used_at"]


def test_a_passkey_signs_in_only_its_owner(tmp_path):
    app = make_app(tmp_path)
    admin = signed_in(app)
    admin.post("/api/users", json={"name": "Sunita", "password": SUNITA})
    sunita = TestClient(app)
    sunita.post("/api/auth/login", json={"name": "Sunita", "password": SUNITA})
    phone = SoftDevice()
    add_passkey(sunita, phone, "Sunita's phone")

    _, r = passkey_sign_in(app, phone)
    assert r.json()["user"]["name"] == "Sunita"
    assert admin.get("/api/passkeys").json() == []  # each person lists only their own
    her_key = sunita.get("/api/passkeys").json()[0]["id"]
    assert admin.delete(f"/api/passkeys/{her_key}").status_code == 404
    assert sunita.delete(f"/api/passkeys/{her_key}").status_code == 200
    _, again = passkey_sign_in(app, phone)
    assert again.status_code == 401 and "isn't registered" in again.json()["detail"]


def test_fingerprint_or_face_is_required(tmp_path):
    app = make_app(tmp_path)
    me = signed_in(app)
    no_biometric = SoftDevice(user_verifies=False)  # e.g. a bare security key tap, no fingerprint
    assert add_passkey(me, no_biometric).status_code == 400

    mac = SoftDevice()
    add_passkey(me, mac)
    mac.user_verifies = False
    _, r = passkey_sign_in(app, mac)
    assert r.status_code == 401


def test_replays_other_sites_and_cloned_devices_are_refused(tmp_path):
    app = make_app(tmp_path)
    me = signed_in(app)
    mac = SoftDevice()
    add_passkey(me, mac)

    client = TestClient(app)
    start = client.post("/api/auth/passkey/options").json()
    answer = mac.get(start["options"])
    assert client.post("/api/auth/passkey", json={"ceremony": start["ceremony"], "credential": answer}).status_code == 200
    replay = client.post("/api/auth/passkey", json={"ceremony": start["ceremony"], "credential": answer})
    assert replay.status_code == 401  # each challenge works once

    _, phished = passkey_sign_in(app, mac, origin="http://evil.example")
    assert phished.status_code == 401  # signed for another site

    cred = next(iter(mac.credentials.values()))
    cred["count"] = 0  # a copied key would repeat an old counter
    _, cloned = passkey_sign_in(app, mac)
    assert cloned.status_code == 401


def test_adding_needs_sign_in_and_passkeys_need_localhost(tmp_path):
    app = make_app(tmp_path, hosts=("localhost",))
    client = TestClient(app)
    assert client.post("/api/passkeys/options").status_code == 401
    status = client.get("/api/auth/status").json()
    assert status["passkeys_available"] is False and "http://localhost" in status["passkey_hint"]
    assert client.post("/api/auth/passkey/options").status_code == 400
    local = TestClient(app, base_url="http://localhost:8000")
    assert local.get("/api/auth/status").json()["passkeys_available"] is True
    options = local.post("/api/auth/passkey/options").json()["options"]
    assert '"rpId": "localhost"' in options and '"userVerification": "required"' in options


def test_repeated_failed_passkey_sign_ins_are_slowed(tmp_path):
    app = make_app(tmp_path)
    signed_in(app)
    stranger = SoftDevice()
    stranger.create({"rp": {"id": "testserver"}, "user": {"id": "eA"}, "challenge": "eA"})  # never registered
    client = TestClient(app)
    for _ in range(5):
        start = client.post("/api/auth/passkey/options").json()
        r = client.post("/api/auth/passkey", json={"ceremony": start["ceremony"], "credential": stranger.get(start["options"])})
        assert r.status_code == 401
    start = client.post("/api/auth/passkey/options").json()
    r = client.post("/api/auth/passkey", json={"ceremony": start["ceremony"], "credential": stranger.get(start["options"])})
    assert r.status_code == 429
