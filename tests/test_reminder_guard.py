"""약속 안전장치(15강 C2): 부탁하는 말이 없으면 모델이 고른 create_reminder/cancel_reminder 를 뺀다. 모델 호출 없음."""
import pytest

from app.router import reminder_guard

U = lambda c: {"role": "user", "content": c}
A = lambda c: {"role": "assistant", "content": c}
CREATE = {"name": "create_reminder", "args": {"text": "x", "due_at": "2026-11-10T09:00:00+09:00"}}
CANCEL = {"name": "cancel_reminder", "args": {"query": "x"}}
LIST = {"name": "list_reminders", "args": {}}
WEATHER = {"name": "get_weather", "args": {"location": "서울", "date": "2026-11-09"}}


def names(tools):
    return [t["name"] for t in tools]


@pytest.mark.parametrize("message", [
    "내일 아침 9시에 병원 가는 거 알려줘", "다음주 화요일 엄마 생신 기억해줘", "목요일 밤 11시에 제출 알림 해줘",
    "30분 뒤에 빨래 꺼내라고 말해 줘", "금요일에 우산 챙기라고 리마인드 해 줘", "내일 7시에 깨워줘",
    "강남 약속 잊지 않게 해줘", "보고서 내는 거 꼭 챙겨줘", "그것도 알림 맞춰줘", "8시에 알람 설정해 줘",
])
def test_request_keeps_create(message):
    tools, guards = reminder_guard(message, [], [CREATE])
    assert names(tools) == ["create_reminder"] and not guards


@pytest.mark.parametrize("message", [
    "다음주 금요일에 면접 있어 좀 떨려", "나 다음 주 토요일에 이사 가", "내일 오후에 치과 예약 있어 ㅠㅠ",
    "그럼 마감까지 며칠 남았어?", "다음 달 3일이 엄마 생신이야", "토요일에 친구 결혼식 가는데 뭐 입지",
])
def test_no_request_drops_create(message):
    tools, guards = reminder_guard(message, [], [CREATE, WEATHER])
    assert names(tools) == ["get_weather"] and guards


@pytest.mark.parametrize("message", ["알림 잘 등록됐어?", "약속 다 잘 들어갔어?", "방금 말한 알림 들어갔는지 확인해줘",
                                     "이번 주 약속 다시 보여줘", "내 알림 목록 좀 보여줄래"])
def test_check_question_keeps_list_drops_create(message):
    tools, _ = reminder_guard(message, [U("목요일 밤 11시에 제출 알림 해줘"), A("저장해둘게!")], [CREATE, LIST])
    assert names(tools) == ["list_reminders"]


def test_change_with_reminder_context_keeps_create_and_cancel():
    history = [U("내일 아침 9시에 병원 가는 거 알려줘"), A("내일 아침 9시에 병원 가는 거 알려줄게!")]
    tools, _ = reminder_guard("아 병원은 10시로 바꿔줘", history, [CANCEL, CREATE])
    assert names(tools) == ["cancel_reminder", "create_reminder"]


def test_change_word_without_reminder_context_drops():
    tools, _ = reminder_guard("머리 스타일 바꿔볼까", [U("요즘 지루해"), A("뭔가 새로운 걸 해 볼까?")], [CREATE])
    assert tools == []


def test_ellipsis_after_request_keeps_create():
    history = [U("다음주 화요일 저녁에 엄마 생신 약속 기억해줘"), A("기억해 둘게!")]
    tools, _ = reminder_guard("모레 점심에 택배 찾는 것도", history, [CREATE])
    assert names(tools) == ["create_reminder"]


def test_ellipsis_without_request_drops():
    tools, _ = reminder_guard("모레 점심에 택배 찾는 것도", [U("오늘 피곤해"), A("푹 쉬어!")], [CREATE])
    assert tools == []


def test_restatement_after_change_drops_both():
    history = [U("아 병원은 10시로 바꿔줘"), A("병원 약속은 10시로 바꿨어.")]
    tools, _ = reminder_guard("아니 그게 아니라 9시 말고 10시라고", history, [CANCEL, CREATE])
    assert tools == []


def test_cancel_request_keeps_cancel():
    tools, _ = reminder_guard("엄마한테 전화하는 알림 취소해 줘", [], [CANCEL])
    assert names(tools) == ["cancel_reminder"]


def test_other_tools_untouched():
    tools, guards = reminder_guard("내일 서울 날씨 어때?", [], [WEATHER])
    assert tools == [WEATHER] and guards == []
