"""Langfuse 계측. 메시지 한 턴을 트레이스 하나로, 단계마다 관찰 단위 하나로 남긴다.

관찰 단위(교안 10강 4장의 계측 구조):
  chat-turn(트레이스) ─ safety(span), choose-tools(generation), tool:<도구 이름>(span),
                        reply(generation), memory-update(generation), memory-summary(generation)
  greeting(트레이스, 모델 호출 하나라 루트를 generation 으로 둔다)

- LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY 가 없으면 아무것도 보내지 않는다(대역 객체가 호출을 받아 버린다).
- 부모를 항상 직접 지정한다(parent.start_observation). /api/chat 은 제너레이터를 스레드 풀에서 한 조각씩 돌리므로,
  "지금 실행 중인 관찰 단위"를 컨텍스트로 이어받는 방식에 기대지 않는다.
- 입력과 출력 원문은 남기되, 보내기 전에 전화번호, 이메일, 카드 번호를 가린다(mask).
- 서버 로그에는 여전히 원문을 남기지 않는다. 이 모듈도 로그에 원문을 쓰지 않는다.
"""
import os
import re
import uuid

from app.logs import log

MASKS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[이메일]"),
    (re.compile(r"\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}"), "[카드번호]"),
    (re.compile(r"(?<!\d)0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)"), "[전화번호]"),
]


def mask_text(text):
    for pattern, label in MASKS:
        text = pattern.sub(label, text)
    return text


def mask(*, data, **kwargs):
    """Langfuse SDK 의 mask 함수. 입력, 출력, metadata 안의 글자를 모두 훑어 가린다."""
    if isinstance(data, str):
        return mask_text(data)
    if isinstance(data, dict):
        return {k: mask(data=v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [mask(data=v) for v in data]
    return data


class _Noop:
    """키가 없을 때 쓰는 대역. 어떤 호출도 받아서 버린다."""
    trace_id = None
    id = None

    def start_observation(self, **kwargs):
        return self

    def update(self, **kwargs):
        return self

    def end(self, **kwargs):
        return None


NOOP = _Noop()
_client = None
_ready = False


def client():
    """처음 쓸 때 한 번 만든다(app.config 가 .env 를 읽은 뒤). 키가 없으면 None."""
    global _client, _ready
    if not _ready:
        _ready = True
        public, secret = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip(), os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
        if public and secret:
            from langfuse import Langfuse

            _client = Langfuse(public_key=public, secret_key=secret,
                               base_url=os.environ.get("LANGFUSE_BASE_URL", "").strip() or None, mask=mask)
            log("INFO", "Langfuse 계측을 켭니다.", base_url=os.environ.get("LANGFUSE_BASE_URL", "(기본)"))
        else:
            log("INFO", "Langfuse 키가 없어 계측을 끕니다.")
    return _client


def start(name, as_type="span", **fields):
    """트레이스의 루트 관찰 단위를 만든다. 루트 이름이 트레이스 이름이 된다."""
    c = client()
    if c is None:
        return NOOP
    return c.start_observation(name=name, as_type=as_type, **fields)


def child(parent, name, as_type="span", **fields):
    return parent.start_observation(name=name, as_type=as_type, **fields)


def record_llm(observation, call):
    """llm.py 가 채운 호출 기록(call)을 generation 에 옮긴다. call 이 비어 있으면(규칙이 정함) 토큰 0."""
    usage = call.get("usage") or {"input": 0, "output": 0}
    fields = {"usage_details": usage}
    if call.get("model"):
        fields["model"] = call["model"]
    if "input" in call:
        fields["input"] = call["input"]
    observation.update(**fields)


def score(trace_id, name, value, comment=None):
    """트레이스에 BOOLEAN 점수를 붙인다. 키가 없거나 트레이스 ID가 없으면 건너뛰고 False.
    점수 ID를 (트레이스, 이름)으로 정해 두어서, 같은 답에 반응을 바꾸면 새 점수가 쌓이지 않고 덮어쓴다."""
    c = client()
    if c is None or not trace_id:
        return False
    c.create_score(trace_id=trace_id, name=name, value=value, data_type="BOOLEAN",
                   score_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{trace_id}/{name}")),
                   comment=mask_text(comment)[:500] if comment else None)
    return True


def flush():
    if _client is not None:
        _client.flush()
