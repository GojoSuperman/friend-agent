"""대화 생성 스크립트의 네트워크 없는 부분(응답 해석, 계획 계산, 키 읽기)을 본다."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("simulate_users", Path(__file__).parent.parent / "scripts" / "simulate_users.py")
sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sim)


def test_parse_chat_collects_reply_id_and_error():
    text = '{"type": "delta", "text": "안"}\n{"type": "delta", "text": "녕"}\n{"type": "done", "message_id": 7}\n'
    assert sim.parse_chat(text)[:3] == ("안녕", 7, None)
    assert sim.parse_chat('{"type": "error", "message": "실패"}\n')[2] == "실패"


def test_plan_counts_and_filters():
    _, sessions, turns = sim.plan(None, 99)
    assert turns == sum(len(s) for p in sim.PERSONAS.values() for s in p["sessions"]) and turns >= 80
    chosen, sessions, turns = sim.plan(["weather"], 1)
    assert list(chosen) == ["weather"] and sessions == 1 and turns == 8


def test_read_key_prefers_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=openai\nLLM_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert sim.read_key(env) == ("openai", "from-file")
    monkeypatch.setenv("LLM_API_KEY", "from-env")
    assert sim.read_key(env)[1] == "from-env"
