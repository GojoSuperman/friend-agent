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
        finished.raw = {s.name: s for s in exporter.get_finished_spans()}
        return {name: dict(s.attributes) for name, s in finished.raw.items()}

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


# ── Cloud Run 은 요청마다 traceparent 헤더를 붙이고, 샘플링 플래그가 00 인 요청이 많다.
#    FastAPI 의 OpenTelemetry span 을 부모로 물려받으면 Langfuse 관찰 단위도 '기록 안 함'이 되어 조용히 버려진다
#    (staging 시험 실행에서 9건 중 6건 누락). 루트는 요청 문맥과 끊고 새 트레이스로 시작해야 한다.

@pytest.mark.parametrize("flag", ["00", "01"])
def test_turn_is_recorded_whatever_the_incoming_sampling_flag(spans, flag):
    incoming = "0af7651916cd43dd8448eb211c80319c"
    chat("오늘 산책했어", {"traceparent": f"00-{incoming}-b7ad6b7169203331-{flag}"})
    got = spans()
    assert {"chat-turn", "safety", "reply"} <= set(got)


def test_chat_turn_is_a_true_root_with_its_own_trace(spans):
    incoming = "0af7651916cd43dd8448eb211c80319c"
    chat("오늘 산책했어", {"traceparent": f"00-{incoming}-b7ad6b7169203331-01"})
    spans()
    root = spans.raw["chat-turn"]
    assert root.parent is None  # 요청(FastAPI) span 의 자식이 아니다
    assert format(root.context.trace_id, "032x") != incoming
    assert spans.raw["reply"].parent.span_id == root.context.span_id


# ── 콜드 스타트 직후 동시에 들어온 첫 요청들이 client() 에서 None 을 받아 기록이 빠졌다
#    (15강 5단계: 새 인스턴스에서 동시 첫 턴 3개 중 2개 누락, 로컬 재현 3개 중 2개 None)

def test_concurrent_first_calls_all_get_the_client(monkeypatch):
    import threading

    monkeypatch.setattr(tracing, "_client", None)
    monkeypatch.setattr(tracing, "_ready", False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", f"pk-lf-{uuid.uuid4()}")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:9")
    got, barrier = [], threading.Barrier(8)

    def hit():
        barrier.wait()
        got.append(tracing.client())

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 8 and all(c is not None for c in got) and len({id(c) for c in got}) == 1
