"""접속 코드 잠금. ACCESS_CODE 환경변수가 있을 때만 켜진다.

공개 주소로 누구나 들어와 LLM 비용을 쓰지 않게, 코드를 아는 사람만 화면과 API를 쓰게 한다.
쿠키에는 코드 원문 대신 코드로 만든 서명값만 넣는다. 코드를 바꾸면 기존 쿠키는 모두 무효가 된다.
"""
import hashlib
import hmac
import html
import re
from dataclasses import dataclass, field

from app.llm import VISITOR_PROVIDERS

COOKIE = "access"
MAX_AGE = 30 * 24 * 3600  # 30일
OPEN_PATHS = ("/health", "/access")  # 상태 확인과 코드 입력 화면은 잠그지 않는다
VISITOR_COOKIE = "llm_mode"  # 값 "visitor": 화면(/)만 열어 준다. 비밀값이 아니다
MAX_KEY_LENGTH = 300
VISITOR_ID = re.compile(r"[0-9a-f-]{36}")


@dataclass
class Visitor:
    """접속 코드 없이 자기 LLM 키로 쓰는 방문자. 키는 repr 에 나오지 않게 한다."""
    provider: str
    key: str = field(repr=False)
    id: str


def parse_visitor(headers):
    """방문자 헤더 세 개를 읽는다. 하나도 없으면 None, 있는데 올바르지 않으면 ValueError(키는 문구에 넣지 않는다)."""
    provider = headers.get("x-llm-provider")
    key = headers.get("x-llm-key")
    vid = headers.get("x-visitor-id")
    if provider is None and key is None and vid is None:
        return None
    if provider not in VISITOR_PROVIDERS:
        raise ValueError("지원하지 않는 제공자")
    if not key or len(key) > MAX_KEY_LENGTH or not (key.isascii() and key.isprintable()):
        raise ValueError("키 형식이 올바르지 않음")
    if not vid or not VISITOR_ID.fullmatch(vid):
        raise ValueError("방문자 ID 형식이 올바르지 않음")
    return Visitor(provider, key, vid)


def token(code):
    return hmac.new(code.encode(), b"friend-agent-access", hashlib.sha256).hexdigest()


def is_allowed(code, cookie):
    return bool(cookie) and hmac.compare_digest(cookie, token(code))


def code_matches(code, entered):
    return hmac.compare_digest(entered.strip().encode(), code.encode())


def page(error=""):
    message = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>접속 코드</title>
<style>
:root{{--bg:#f5f3ef;--surface:#fff;--ink:#1f1d1a;--muted:#6b665e;--line:#d9d3c9;--accent:#2f6f62;--err:#b4441a}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151412;--surface:#1f1d1a;--ink:#eeeae4;--muted:#a39d93;--line:#3a3631;--accent:#5fb3a0;--err:#f08a5d}}}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);
font:15px/1.6 "Pretendard Variable",Pretendard,-apple-system,system-ui,sans-serif;padding:16px}}
form{{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:24px;width:100%;max-width:340px;display:grid;gap:12px}}
h1{{font-size:18px;margin:0}} p{{margin:0;color:var(--muted);font-size:14px}}
input{{font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}}
button{{font:inherit;padding:10px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}}
.err{{color:var(--err)}}
</style></head><body>
<form method="post" action="/access">
<h1>접속 코드를 입력하세요</h1>
<p>코드를 아는 사람만 쓸 수 있는 서비스입니다.</p>
{message}
<label for="code">접속 코드</label>
<input id="code" name="code" type="password" autocomplete="current-password" autofocus required>
<button type="submit">들어가기</button>
</form></body></html>"""
