"""접속 코드 잠금. ACCESS_CODE 환경변수가 있을 때만 켜진다.

공개 주소로 누구나 들어와 LLM 비용을 쓰지 않게, 코드를 아는 사람만 화면과 API를 쓰게 한다.
쿠키에는 코드 원문 대신 코드로 만든 서명값만 넣는다. 코드를 바꾸면 기존 쿠키는 모두 무효가 된다.
"""
import hashlib
import hmac
import html
import re
from dataclasses import dataclass, field

from app.config import PROVIDERS
from app.llm import VISITOR_PROVIDERS

COOKIE = "access"
MAX_AGE = 30 * 24 * 3600  # 30일
OPEN_PATHS = ("/health", "/access")  # 상태 확인과 코드 입력 화면은 잠그지 않는다
VISITOR_COOKIE = "llm_mode"  # 값 "visitor": 화면(/)만 열어 준다. 비밀값이 아니다
MAX_KEY_LENGTH = 300
VISITOR_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


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
    names = {"openai": "OpenAI", "gemini": "Gemini", "claude": "Claude"}
    options = "".join(
        f'<option value="{p}">{names[p]} ({html.escape(PROVIDERS[p]["model"])})'
        f'{" - 비용이 큰 모델" if p == "claude" else ""}</option>'
        for p in VISITOR_PROVIDERS
    )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>접속 코드</title>
<style>
:root{{--bg:#f5f3ef;--surface:#fff;--ink:#1f1d1a;--muted:#6b665e;--line:#d9d3c9;--accent:#2f6f62;--err:#b4441a}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151412;--surface:#1f1d1a;--ink:#eeeae4;--muted:#a39d93;--line:#3a3631;--accent:#5fb3a0;--err:#f08a5d}}}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);
font:15px/1.6 "Pretendard Variable",Pretendard,-apple-system,system-ui,sans-serif;padding:16px}}
form,.box{{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:24px;width:100%;max-width:340px;display:grid;gap:12px}}
h1{{font-size:18px;margin:0}} p{{margin:0;color:var(--muted);font-size:14px}}
input{{font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}}
button{{font:inherit;padding:10px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}}
.err{{color:var(--err)}}
main{{width:100%;max-width:340px;display:grid;gap:16px}}
form,.box{{max-width:none}} .box{{display:grid;gap:12px}}
select{{font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}}
#visitor-key{{-webkit-text-security:disc}}
button.alt{{background:transparent;color:var(--accent);border:1px solid var(--accent)}}
button:disabled{{opacity:.45;cursor:not-allowed}}
.ok{{color:var(--accent)}}
hr{{border:0;border-top:1px solid var(--line);margin:0}}
</style></head><body><main>
<form method="post" action="/access">
<h1>접속 코드를 입력하세요</h1>
<p>코드를 아는 사람만 쓸 수 있는 서비스입니다.</p>
{message}
<label for="code">접속 코드</label>
<input id="code" name="code" type="password" autocomplete="current-password" autofocus required>
<button type="submit">들어가기</button>
</form>
<div class="box" id="visitor-form">
<h1>내 API 키로 쓰기</h1>
<p>접속 코드가 없다면 본인의 LLM API 키로 써 볼 수 있습니다.</p>
<label for="visitor-provider">제공자</label>
<select id="visitor-provider">{options}</select>
<label for="visitor-key">API 키</label>
<input id="visitor-key" type="text" autocomplete="off" spellcheck="false" autocapitalize="off">
<p>키는 이 탭에만 보관되고 탭을 닫으면 사라집니다. 서버에 저장하지 않습니다.</p>
<p class="err" id="visitor-msg" role="alert"></p>
<button type="button" id="visitor-check">키 검사</button>
<p id="visitor-check-result" role="status"></p>
<button type="button" class="alt" id="visitor-start" disabled>시작</button>
</div>
</main>
<script>
(function () {{
  var providerEl = document.getElementById("visitor-provider");
  var keyEl = document.getElementById("visitor-key");
  var msg = document.getElementById("visitor-msg");
  var result = document.getElementById("visitor-check-result");
  var checkBtn = document.getElementById("visitor-check");
  var startBtn = document.getElementById("visitor-start");
  var newId = null;  // 탭에 저장된 방문자 ID가 없을 때 쓸 ID. [시작]할 때 저장해 검사와 같은 ID를 쓴다
  var checkNo = 0;   // 키를 바꾸면 늘려서, 그 전에 보낸 검사 결과는 버린다

  function visitorId() {{
    try {{ var saved = sessionStorage.getItem("visitor_id"); if (saved) return saved; }} catch (e) {{}}
    if (!newId) newId = crypto.randomUUID();
    return newId;
  }}
  function readKey() {{
    var key = keyEl.value.trim();
    if (!key) {{ msg.textContent = "API 키를 입력해 주세요."; return null; }}
    if (key.length > {MAX_KEY_LENGTH}) {{ msg.textContent = "키가 너무 깁니다. 다시 확인해 주세요."; return null; }}
    if (!/^[ -~]+$/.test(key)) {{ msg.textContent = "키에 쓸 수 없는 문자가 있습니다. 다시 확인해 주세요."; return null; }}
    msg.textContent = "";
    return key;
  }}
  function show(ok, text) {{ result.className = ok ? "ok" : "err"; result.textContent = text; }}
  function reset() {{
    checkNo++;
    result.className = ""; result.textContent = "";
    startBtn.disabled = true; checkBtn.disabled = false;
  }}
  keyEl.addEventListener("input", reset);
  providerEl.addEventListener("change", reset);

  checkBtn.addEventListener("click", function () {{
    var key = readKey();
    if (!key) return;
    var no = ++checkNo;
    checkBtn.disabled = true; startBtn.disabled = true;
    show(true, "검사 중...");
    // 키는 검사 요청 헤더로만 보낸다. 서버는 저장하지 않는다
    fetch("/api/visitor/check", {{method: "POST", headers: {{
      "X-LLM-Provider": providerEl.value, "X-LLM-Key": key, "X-Visitor-Id": visitorId()}}}})
      .then(function (r) {{
        return r.json().catch(function () {{ return {{}}; }}).then(function (body) {{
          if (no !== checkNo) return;
          checkBtn.disabled = false;
          if (r.status === 200) {{ show(true, "✅ 사용할 수 있는 키예요"); startBtn.disabled = false; return; }}
          show(false, "❌ " + (typeof body.detail === "string" ? body.detail : "키를 검사하지 못했어요. 잠시 뒤 다시 시도해 주세요."));
        }});
      }})
      .catch(function () {{
        if (no !== checkNo) return;
        checkBtn.disabled = false;
        show(false, "❌ 서버에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요.");
      }});
  }});

  startBtn.addEventListener("click", function () {{
    var key = readKey();
    if (!key || startBtn.disabled) return;
    try {{
      sessionStorage.setItem("llm_provider", providerEl.value);
      sessionStorage.setItem("llm_key", key);
      if (!sessionStorage.getItem("visitor_id")) sessionStorage.setItem("visitor_id", visitorId());
    }} catch (e) {{ msg.textContent = "이 브라우저에서는 키를 탭에 보관할 수 없습니다."; return; }}
    document.cookie = "{VISITOR_COOKIE}=visitor; path=/; SameSite=Lax" + (location.protocol === "https:" ? "; Secure" : "");
    location.href = "/";
  }});
}})();
</script></body></html>"""
