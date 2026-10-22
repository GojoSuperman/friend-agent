from fastapi.testclient import TestClient

from app import main


def locked(monkeypatch):
    monkeypatch.setattr(main.settings, "access_code", "test-code")
    return TestClient(main.app)


def test_no_code_means_no_lock():
    assert main.settings.access_code == ""
    assert TestClient(main.app).get("/").status_code == 200


def test_locked_page_redirects_and_api_is_401(monkeypatch):
    client = locked(monkeypatch)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/access"
    assert client.post("/api/chat", json={"message": "안녕"}).status_code == 401
    assert client.get("/health").status_code == 200


def test_wrong_code_is_rejected(monkeypatch):
    client = locked(monkeypatch)
    r = client.post("/access", data={"code": "wrong"}, follow_redirects=False)
    assert r.status_code == 401
    assert "access" not in r.cookies


def test_right_code_unlocks(monkeypatch):
    client = locked(monkeypatch)
    r = client.post("/access", data={"code": "test-code"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.cookies.get("access") and r.cookies["access"] != "test-code"
    assert client.get("/").status_code == 200
    assert client.get("/api/me").status_code == 200
