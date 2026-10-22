import pytest
from openai import Omit
from app import llm
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
