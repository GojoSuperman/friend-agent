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
- 모든 관찰 단위에 version(커밋 SHA 앞 7자리), session.id(화면이 보낸 세션), user.id(가명)를 붙인다.
  고치기 전과 후 트레이스를 버전으로 나누고, 세션 단위로 짧게 끊긴 대화를 세기 위해서다(15강).
  SDK 의 propagate_attributes 는 그 블록 안에서 만든 관찰 단위에만 붙으므로, 자식을 만들 때마다 다시 감싼다.
"""
import hashlib
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

SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def version():
    """배포한 커밋의 앞 7자리. 로컬처럼 COMMIT_SHA 가 없으면 'local'."""
    return os.environ.get("COMMIT_SHA", "").strip()[:7] or "local"


def pseudonym(uid):
    """사용자 ID 를 그대로 보내지 않고, 같은 사람끼리만 묶이는 가명으로 바꾼다."""
    return hashlib.sha256(f"friend-agent:{uid}".encode()).hexdigest()[:16]


def clean_session(value):
    """화면이 보낸 세션 ID. 형식이 맞지 않으면 버린다(None)."""
    value = (value or "").strip()
    return value if SESSION_RE.match(value) else None


def client_options():
    """Langfuse 클라이언트에 줄 environment(ENV)와 release(커밋). Langfuse 는 소문자·숫자·-·_ 만 받고
    'langfuse' 로 시작하는 이름을 막으므로 맞춰 바꾼다."""
    env = re.sub(r"[^a-z0-9_-]+", "-", os.environ.get("ENV", "local").strip().lower()).strip("-") or "local"
    if env.startswith("langfuse"):
        env = "app-" + env
    return {"environment": env[:40], "release": version()}


def _attrs(user_id=None, session_id=None):
    attrs = {"version": version()}
    if user_id:
        attrs["user_id"] = pseudonym(user_id)
    if session_id:
        attrs["session_id"] = session_id
    return attrs


def _create(parent, attrs, **kwargs):
    from langfuse import propagate_attributes

    with propagate_attributes(**attrs):
        obs = parent.start_observation(**kwargs)
    try:
        obs._friend_attrs = attrs  # 자식을 만들 때 같은 속성을 다시 붙이려고 기억해 둔다
    except AttributeError:
        pass
    return obs
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
                               base_url=os.environ.get("LANGFUSE_BASE_URL", "").strip() or None, mask=mask,
                               **client_options())
            log("INFO", "Langfuse 계측을 켭니다.", base_url=os.environ.get("LANGFUSE_BASE_URL", "(기본)"), **client_options())
        else:
            log("INFO", "Langfuse 키가 없어 계측을 끕니다.")
    return _client


def start(name, as_type="span", user_id=None, session_id=None, **fields):
    """트레이스의 루트 관찰 단위를 만든다. 루트 이름이 트레이스 이름이 된다."""
    c = client()
    if c is None:
        return NOOP
    return _create(c, _attrs(user_id, session_id), name=name, as_type=as_type, **fields)


def child(parent, name, as_type="span", **fields):
    if parent is NOOP:
        return NOOP
    attrs = getattr(parent, "_friend_attrs", None) or _attrs()
    return _create(parent, attrs, name=name, as_type=as_type, **fields)


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
    flush()
    return True


def flush():
    """모아 둔 기록을 지금 보낸다. Cloud Run(요청 기반 과금)은 응답이 끝나면 CPU 를 거의 주지 않아서,
    백그라운드 전송에 맡기면 기록이 빠진다(15강 시험 실행에서 9건 중 6건 누락). 그래서 응답을 닫기 전에 부른다.
    전송이 실패해도 사용자 응답은 그대로 끝나야 하므로 오류는 종류만 남기고 삼킨다."""
    if _client is None:
        return
    try:
        _client.flush()
    except Exception as error:
        log("WARNING", "Langfuse 전송 실패", error=type(error).__name__)
