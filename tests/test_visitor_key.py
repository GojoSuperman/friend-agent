import pytest
from openai import Omit
from fastapi.testclient import TestClient
from app import llm, main
from app.llm import FakeLLM
from app.config import PROVIDERS

def test_visitor_llm_uses_provider_defaults(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://evil.example/v1")
    m = llm.make_visitor_llm("openai", "k-123")
    assert "api.openai.com" in str(m.client.base_url) and m.model == PROVIDERS["openai"]["model"]
    g = llm.make_visitor_llm("gemini", "k-123")
    assert g.model == PROVIDERS["gemini"]["model"] and "generativelanguage" in str(g.client.base_url)
    assert isinstance(llm.make_visitor_llm("claude", "k-123"), llm.ClaudeLLM)

@pytest.mark.parametrize("provider,key", [("fake", "k"), ("openai", ""), ("unknown", "k")])
def test_visitor_llm_rejects_bad_input(provider, key):
    with pytest.raises(ValueError):
        llm.make_visitor_llm(provider, key)

def err(status, text=""):
    e = Exception(text); e.status_code = status; return e

def test_is_key_error():
    class AuthenticationError(Exception): pass
    assert llm.is_key_error(AuthenticationError())
    assert llm.is_key_error(err(401)) and llm.is_key_error(err(403))
    assert llm.is_key_error(err(400, "API key not valid. Please pass a valid API key."))
    assert llm.is_key_error(err(429, "You exceeded your current quota (insufficient_quota)"))
    assert not llm.is_key_error(err(400, "messages: too long"))
    assert not llm.is_key_error(err(429, "rate limit")) and not llm.is_key_error(TimeoutError())

def test_visitor_llm_ignores_server_env(monkeypatch):
    monkeypatch.setenv("OPENAI_ORG_ID", "org-server")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-server")
    monkeypatch.setenv("OPENAI_PROJECT", "proj-server")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    for provider in ("openai", "gemini"):
        c = llm.make_visitor_llm(provider, "k-123").client
        assert not c.organization and not c.project
        sent = [v for k, v in c.default_headers.items() if k in ("OpenAI-Organization", "OpenAI-Project")]
        assert all(isinstance(v, Omit) for v in sent) and "org-server" not in str(sent)
    assert str(llm.make_visitor_llm("claude", "k-123").client.base_url).startswith("https://api.anthropic.com")


# ── Task 3: 방문자 헤더로 잠금·사용자·LLM 나누기 ─────────────────────

def locked(monkeypatch):
    monkeypatch.setattr(main.settings, "access_code", "test-code")
    return TestClient(main.app)

VID = "0b6f7c1e-1a2b-4c3d-8e9f-0123456789ab"
H = {"X-LLM-Provider": "openai", "X-LLM-Key": "sk-visitor-test-key", "X-Visitor-Id": VID}

def fake_visitor(monkeypatch, cls=FakeLLM, seen=None):
    monkeypatch.setattr(main, "make_visitor_llm", lambda p, k: (seen.append(p) if seen is not None else None) or cls(delay=0))

def test_no_lock_ignores_visitor_headers(monkeypatch):
    seen = []; fake_visitor(monkeypatch, seen=seen)
    from fastapi.testclient import TestClient
    assert TestClient(main.app).post("/api/chat", json={"message": "안녕"}, headers=H).status_code == 200
    assert seen == []

def test_visitor_header_uses_visitor_llm(monkeypatch):
    client = locked(monkeypatch); seen = []; fake_visitor(monkeypatch, seen=seen)
    r = client.post("/api/chat", json={"message": "안녕"}, headers=H)
    assert r.status_code == 200 and '"type": "done"' in r.text and seen == ["openai"]

def test_visitor_cannot_see_or_delete_presenter_data(monkeypatch):
    client = locked(monkeypatch); fake_visitor(monkeypatch)
    client.post("/access", data={"code": "test-code"})
    client.post("/api/chat", json={"message": "발표자 비밀 메모"})
    client.cookies.clear()
    assert "발표자 비밀 메모" not in client.get("/api/messages", headers=H).text
    client.delete("/api/data", headers=H)
    client.post("/access", data={"code": "test-code"})
    assert "발표자 비밀 메모" in client.get("/api/messages").text

def test_visitor_cookie_opens_page_only(monkeypatch):
    client = locked(monkeypatch); client.cookies.set("llm_mode", "visitor")
    assert client.get("/", follow_redirects=False).status_code == 200
    assert client.get("/version", follow_redirects=False).status_code == 303
    r = client.post("/api/chat", json={"message": "안녕"})
    assert r.status_code == 401 and r.json()["reason"] == "locked"

@pytest.mark.parametrize("bad", [{"X-LLM-Provider": "fake"}, {"X-LLM-Key": ""}, {"X-LLM-Key": "k" * 301},
                                 {"X-Visitor-Id": "../etc"}, {"X-LLM-Provider": None}])
def test_bad_visitor_headers_are_400(monkeypatch, bad):
    h = {k: v for k, v in {**H, **bad}.items() if v is not None}
    r = locked(monkeypatch).post("/api/chat", json={"message": "안녕"}, headers=h)
    assert r.status_code == 400 and r.json()["reason"] == "visitor_header"

def test_visitor_key_wins_over_access_cookie(monkeypatch):
    client = locked(monkeypatch); client.post("/access", data={"code": "test-code"})
    seen = []; fake_visitor(monkeypatch, seen=seen)
    client.post("/api/chat", json={"message": "안녕"}, headers=H)
    assert seen == ["openai"]

class KeyFail(FakeLLM):
    def chat_stream(self, messages): raise _auth_error()
    def complete_text(self, messages): raise _auth_error()

def _auth_error():
    class AuthenticationError(Exception): pass
    return AuthenticationError("Incorrect API key provided: sk-visitor-test-key")

def test_wrong_key_chat_is_friendly(monkeypatch):
    client = locked(monkeypatch); fake_visitor(monkeypatch, KeyFail)
    r = client.post("/api/chat", json={"message": "안녕"}, headers=H)
    assert "API 키를 확인해 주세요" in r.text and "sk-visitor" not in r.text

def test_wrong_key_greeting_is_401_key(monkeypatch):
    client = locked(monkeypatch); fake_visitor(monkeypatch, KeyFail)
    client.delete("/api/data", headers=H)  # 최근 대화가 없어야 인사가 LLM을 부른다
    g = client.post("/api/greeting", headers=H)
    assert g.status_code == 401 and g.json()["reason"] == "key" and "sk-visitor" not in g.text

def test_server_key_error_is_not_reported_as_visitor_key(monkeypatch):
    client = locked(monkeypatch); client.post("/access", data={"code": "test-code"})
    main.app.dependency_overrides[main.get_llm] = lambda: KeyFail(delay=0)
    try:
        r = client.post("/api/chat", json={"message": "안녕"})
        assert "API 키를 확인해 주세요" not in r.text
    finally:
        main.app.dependency_overrides.clear()

def test_visitor_cannot_use_ocr(monkeypatch):
    monkeypatch.setattr(main.settings, "ocr_enabled", True)
    r = locked(monkeypatch).post("/api/ocr", headers=H, files={"photo": ("a.png", b"x", "image/png")})
    assert r.status_code == 403

def test_key_never_logged(monkeypatch, capsys):
    client = locked(monkeypatch); fake_visitor(monkeypatch, KeyFail)
    client.post("/api/chat", json={"message": "안녕"}, headers=H)
    client.delete("/api/data", headers=H); client.post("/api/greeting", headers=H)
    out = capsys.readouterr()
    assert "sk-visitor-test-key" not in out.out + out.err
