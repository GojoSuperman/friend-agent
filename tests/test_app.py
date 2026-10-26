import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app, store

client = TestClient(app)


def chat(message):
    r = client.post("/api/chat", json={"message": message})
    assert r.status_code == 200
    return [json.loads(line) for line in r.text.splitlines()]


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_weather_question_picks_weather_tool():
    turn = next(e for e in chat("내일 서울 날씨 어때?") if e["type"] == "turn")
    assert [t["name"] for t in turn["tools"]] == ["get_weather"]


def test_crisis_message_gets_fixed_safety_reply():
    events = chat("요즘 너무 죽고 싶어")
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "109" in text


def test_reminder_is_saved_with_timezone():
    chat("내일 아침 8시에 물 마시라고 알려 줘")
    pending = store.list_reminders("local-user", status="pending")
    assert pending and pending[-1]["due_at"].tzinfo is not None


def test_due_reminder_is_delivered_only_once():
    due = datetime.now(timezone.utc) - timedelta(minutes=1)
    store.add_reminder("local-user", "테스트 약속", due)
    now = datetime.now(timezone.utc)
    first = store.claim_due_reminders("local-user", now)
    second = store.claim_due_reminders("local-user", now)
    assert [r["text"] for r in first].count("테스트 약속") == 1
    assert "테스트 약속" not in [r["text"] for r in second]


def test_ocr_is_off_by_default():
    r = client.post("/api/ocr", files={"photo": ("a.png", b"x", "image/png")})
    assert r.status_code == 404


def test_pdf_is_off_by_default():
    r = client.post("/api/pdf", files={"document": ("a.pdf", b"x", "application/pdf")})
    assert r.status_code == 404


def test_pdf_without_pdftotext_says_program_missing(monkeypatch):
    import app.main as main
    from app import pdf

    def missing(*args, **kwargs):
        raise FileNotFoundError("pdftotext")

    monkeypatch.setattr(main.settings, "ocr_enabled", True)
    monkeypatch.setattr(pdf.subprocess, "run", missing)
    r = client.post("/api/pdf", files={"document": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 500
    assert "프로그램이 서버에 없습니다" in r.json()["detail"]


def test_home_has_voice_controls():
    html = client.get("/").text
    for id_ in ("micBtn", "muteBtn"):
        assert f'id="{id_}"' in html
