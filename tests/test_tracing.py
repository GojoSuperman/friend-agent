"""Langfuse 계측 구조 테스트. 네트워크 없이 기록만 모으는 가짜 클라이언트로 관찰 단위의 이름, 종류, 순서를 본다."""
import json

import pytest
from fastapi.testclient import TestClient

from app import tracing
from app.main import app

client = TestClient(app)


class Recorder:
    """Langfuse 관찰 단위 대역. start_observation/update/end 를 기록한다."""

    def __init__(self, log, name="(client)", as_type=None, parent=None, **fields):
        self.log, self.name, self.as_type, self.parent = log, name, as_type, parent
        self.fields, self.ended = dict(fields), False
        self.trace_id = "trace-1"

    def start_observation(self, *, name, as_type="span", **fields):
        obs = Recorder(self.log, name, as_type, self, **fields)
        self.log.append(obs)
        return obs

    def update(self, **fields):
        self.fields.update(fields)
        return self

    def end(self, **kwargs):
        self.ended = True

    def create_score(self, **fields):
        self.log.append(("score", fields))


@pytest.fixture
def observations(monkeypatch):
    log = []
    monkeypatch.setattr(tracing, "_client", Recorder(log))
    monkeypatch.setattr(tracing, "_ready", True)
    return log


def chat(message):
    r = client.post("/api/chat", json={"message": message})
    assert r.status_code == 200
    return [json.loads(line) for line in r.text.splitlines()]


def tree(log):
    return [(o.name, o.as_type, o.parent.name) for o in log]


def test_small_talk_turn_structure(observations):
    chat("오늘 회사에서 좀 지쳤어")
    assert tree(observations) == [
        ("chat-turn", "span", "(client)"),
        ("safety", "span", "chat-turn"),
        ("choose-tools", "generation", "chat-turn"),
        ("reply", "generation", "chat-turn"),
        ("memory-update", "generation", "chat-turn"),
    ]
    assert all(o.ended for o in observations)
    by = {o.name: o for o in observations}
    assert by["choose-tools"].fields["metadata"]["decided_by"] == "rule"  # 가짜 LLM 은 규칙이 고른다
    assert by["choose-tools"].fields["usage_details"] == {"input": 0, "output": 0}
    assert by["reply"].fields["model"] == "fake" and "completion_start_time" in by["reply"].fields
    assert by["chat-turn"].fields["output"] and "message_id" in by["chat-turn"].fields["metadata"]


def test_unsupported_city_records_failed_tool(observations, monkeypatch):
    # 실제 모델은 get_weather(도쿄)를 고른다. 가짜 LLM 의 키워드 규칙은 모르는 도시를 사용자 지역으로 바꾸므로,
    # 모델이 고른 경우를 대역으로 만든다(외부 API 를 부르기 전에 '지원하지 않는 지역'으로 실패한다).
    from app import agent
    from app.router import ToolChoice

    def model_like(message, history, now, location, llm, trace=None):
        return ToolChoice([{"name": "get_weather", "args": {"location": "도쿄", "date": now.date().isoformat()}}],
                          0.93, "도쿄 날씨를 물었다", "model")
    monkeypatch.setattr(agent, "choose", model_like)
    chat("도쿄 날씨 어때?")
    by = {o.name: o for o in observations}
    tool = by["tool:get_weather"]
    assert tool.fields["output"]["ok"] is False and tool.fields["level"] == "WARNING"
    # 실패한 도구 결과가 답 만들기 입력의 [도구 결과]에 그대로 들어갔는지
    system = by["reply"].fields["input"][0]["content"]
    assert "[도구 결과]" in system and tool.fields["output"]["summary"] in system


def test_crisis_turn_has_no_model_call(observations):
    chat("요즘 너무 죽고 싶어")
    assert [o.name for o in observations] == ["chat-turn", "safety"]
    assert observations[1].fields["output"] == {"crisis": True}


def test_mask_hides_phone_email_card():
    data = {"m": "연락은 010-1234-5678, a.b@example.com, 카드 1234-5678-9012-3456", "n": [3, "02 345 6789"]}
    masked = tracing.mask(data=data)
    assert masked["m"] == "연락은 [전화번호], [이메일], 카드 [카드번호]"
    assert masked["n"] == [3, "[전화번호]"]


def test_no_keys_means_no_client(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None)
    monkeypatch.setattr(tracing, "_ready", False)
    assert tracing.start("chat-turn") is tracing.NOOP


# ── 8장: 화면 반응을 점수로 ─────────────────────────────────────────

def last_reply_id(events):
    return next(e["message_id"] for e in events if e["type"] == "done")


def scores(log):
    return [f for o in log if isinstance(o, tuple) for f in [o[1]]]


def test_feedback_and_report_become_boolean_scores(observations):
    mid = last_reply_id(chat("오늘 산책했어"))
    assert client.post("/api/feedback", json={"message_id": mid, "value": "down"}).json() == {"ok": True, "scored": True}
    assert client.post("/api/report", json={"message_id": mid, "reason": "연락처 010-1234-5678 이 보여요"}).status_code == 200
    fb, rp = scores(observations)
    assert (fb["name"], fb["value"], fb["data_type"], fb["trace_id"]) == ("user-feedback", 0, "BOOLEAN", "trace-1")
    assert (rp["name"], rp["value"], rp["comment"]) == ("report", 1, "연락처 [전화번호] 이 보여요")
    # 반응을 바꾸면 같은 점수 ID 로 덮어쓴다
    client.post("/api/feedback", json={"message_id": mid, "value": "up"})
    assert scores(observations)[-1]["score_id"] == fb["score_id"] and scores(observations)[-1]["value"] == 1


def test_cannot_score_someone_elses_message(observations):
    from app.main import store
    other = store.add_message("someone-else", "assistant", "남의 답", {"trace_id": "trace-x"})
    assert client.post("/api/feedback", json={"message_id": other["id"], "value": "down"}).status_code == 404
    assert client.post("/api/report", json={"message_id": other["id"], "reason": ""}).status_code == 404
    assert scores(observations) == []


def test_feedback_without_keys_is_ok_and_skipped():
    events = chat("안녕")
    r = client.post("/api/feedback", json={"message_id": last_reply_id(events), "value": "up"})
    assert r.status_code == 200 and r.json() == {"ok": True, "scored": False}
