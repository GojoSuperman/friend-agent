"""트레이스를 버전, 세션, 사용자로 나눌 수 있는지 본다(15강 로그로 개선하기 0단계).

가짜 기록기 대신 실제 Langfuse SDK 에 메모리 내보내기(InMemorySpanExporter)를 붙여서,
네트워크 없이 실제로 내보내질 관찰 단위의 속성을 확인한다.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import tracing
from app.main import app

client = TestClient(app)


@pytest.fixture
def spans(monkeypatch):
    from langfuse import Langfuse

    exporter = InMemorySpanExporter()
    lf = Langfuse(public_key=f"pk-lf-{uuid.uuid4()}", secret_key="sk-lf-test", base_url="http://127.0.0.1:9",
                  span_exporter=exporter, flush_at=1, mask=tracing.mask)
    monkeypatch.setattr(tracing, "_client", lf)
    monkeypatch.setattr(tracing, "_ready", True)

    def finished():
        lf.flush()
        return {s.name: dict(s.attributes) for s in exporter.get_finished_spans()}

    return finished


def chat(message, headers=None):
    r = client.post("/api/chat", json={"message": message}, headers=headers or {})
    assert r.status_code == 200
    return r.text


def test_every_observation_carries_version_session_user(spans, monkeypatch):
    monkeypatch.setenv("COMMIT_SHA", "abcdef1234567890")
    chat("오늘 회사에서 좀 지쳤어", {"X-Chat-Session": "s-1234abcd"})
    got = spans()
    assert {"chat-turn", "safety", "choose-tools", "reply", "memory-update"} <= set(got)
    for name, attrs in got.items():
        assert attrs.get("langfuse.version") == "abcdef1", name
        assert attrs.get("session.id") == "s-1234abcd", name
        assert attrs.get("user.id") == tracing.pseudonym("local-user"), name


def test_user_id_is_pseudonym_not_raw():
    p = tracing.pseudonym("local-user")
    assert p != "local" and len(p) == 16 and p == tracing.pseudonym("local-user")
    assert tracing.pseudonym("other") != p


def test_without_session_header_version_still_set(spans, monkeypatch):
    monkeypatch.delenv("COMMIT_SHA", raising=False)
    chat("오늘 산책했어")
    got = spans()
    assert "chat-turn" in got
    for name, attrs in got.items():
        assert attrs.get("langfuse.version") == "local", name
        assert "session.id" not in attrs, name


@pytest.mark.parametrize("bad", ["", "짧", "a" * 65, "has space", "<script>"])
def test_bad_session_header_is_ignored(bad):
    assert tracing.clean_session(bad) is None


def test_good_session_header_kept():
    assert tracing.clean_session("load-3f9c2a1b") == "load-3f9c2a1b"


def test_greeting_trace_has_version(spans, monkeypatch):
    monkeypatch.setenv("COMMIT_SHA", "1234567aaaa")
    from app.main import store
    store._user("local-user")["messages"].clear()  # 처음 만난 사이 → 먼저 말을 건다
    r = client.post("/api/greeting", headers={"X-Chat-Session": "s-greet-01"})
    assert r.status_code == 200 and r.json().get("greeting"), r.text
    attrs = spans()["greeting"]
    assert attrs["langfuse.version"] == "1234567" and attrs["session.id"] == "s-greet-01"


def test_client_options_split_environment_and_release(monkeypatch):
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("COMMIT_SHA", "fedcba9876")
    assert tracing.client_options() == {"environment": "staging", "release": "fedcba9"}
    monkeypatch.setenv("ENV", "Langfuse Prod!")  # Langfuse 가 받지 않는 이름은 안전한 글자로 바꾼다
    assert tracing.client_options()["environment"] == "app-langfuse-prod"
