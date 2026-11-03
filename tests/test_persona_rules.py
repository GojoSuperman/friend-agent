"""11강 실험(friend-reply 데이터셋, LLM 심사)으로 효과를 확인한 답 지시문 규칙이 고정 규칙에 남아 있는지 본다."""
from app.persona import DEFAULT_CHARACTER, FIXED_RULES, build_persona

V2_RULES = [
    "'공휴일이 없습니다'이면 그 기간에 공휴일이 없다고만 말하고",
    "월요일이면 앞의 토·일과 이어 사흘 연휴라고 말한다",
    "있는데도 모른다고 얼버무리거나 날짜를 빼지 않는다",
]


def test_v2_rules_are_in_fixed_rules():
    for rule in V2_RULES:
        assert rule in FIXED_RULES


def test_fixed_rules_come_first_in_persona():
    prompt = build_persona(DEFAULT_CHARACTER)
    assert prompt.startswith("[지켜야 할 규칙")
    assert all(rule in prompt for rule in V2_RULES)
