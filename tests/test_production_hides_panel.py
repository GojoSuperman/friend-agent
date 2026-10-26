import json

from fastapi.testclient import TestClient

from app import config, main

# 개발용 "이번 턴" 패널이 보내는 이벤트(도구, 확신도, 안전장치, 문맥). production 사용자에게 나가면 안 된다
# 기억 이벤트(memory_start, memory)는 패널과 상관없이 늘 보내는 기능이라 넣지 않는다
PANEL_EVENTS = {"turn", "tool", "context"}


def test_production_response_has_no_panel_info(monkeypatch):
    # 예시 E12: ENV=production 에서 메시지를 보낸다 → 응답에 도구, 확신도, 안전장치 정보가 들어 있지 않다
    monkeypatch.setattr(config, "load_dotenv", lambda path: None)  # 로컬 .env 가 결과를 바꾸지 않게 한다
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("SHOW_TURN_PANEL", raising=False)
    monkeypatch.setattr(main, "settings", config.load_settings())
    client = TestClient(main.app)

    # 도구를 부르는 메시지와 안전장치가 걸리는 메시지 둘 다 확인한다
    for message in ["내일 서울 날씨 어때?", "요즘 너무 죽고 싶어"]:
        r = client.post("/api/chat", json={"message": message})
        events = [json.loads(line) for line in r.text.splitlines()]
        assert events and not [e["type"] for e in events if e["type"] in PANEL_EVENTS]
        assert not [e for e in events if "confidence" in e or "guards" in e]
