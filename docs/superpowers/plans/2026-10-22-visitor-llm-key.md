# PR ① 접속 잠금 + 방문자 LLM 키 등록 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 공개 시연 URL에서 접속 코드를 아는 사람은 서버 키로, 모르는 방문자는 자기 LLM API 키를 등록해 **자기만의 데이터 공간에서** 대화하게 한다.

**Architecture:** 작업 트리에 있는 접속 코드 잠금(`app/access.py` + `access_lock` 미들웨어)을 먼저 커밋한다. 방문자는 브라우저 sessionStorage에 제공자·키·무작위 방문자 ID를 두고 요청마다 `X-LLM-Provider`/`X-LLM-Key`/`X-Visitor-Id` 헤더로 보낸다. **잠금이 켜져 있을 때만** 미들웨어가 이 헤더를 한 번 해석해 `request.state.visitor`에 넣고, `Auth`는 그 방문자를 `visitor:<id>` 사용자로, `get_llm`은 그 키로 만든 요청 전용 LLM으로 바꾼다. 방문자는 발표자(`local-user`) 데이터에 닿지 못한다.

**Tech Stack:** Python 3 · FastAPI · pytest(TestClient) · OpenAI/Anthropic SDK · 순수 HTML/JS(`static/index.html`)

**Spec:** `docs/superpowers/specs/2026-10-22-voice-character-design.md` 11절 (작업 순서는 10절)

## Global Constraints

- 저장소 규칙 `AGENTS.md`를 따른다: 비밀값을 코드·로그·화면·커밋에 남기지 않는다, 액션 버전을 바꾸지 않는다, `app/persona.py`·`app/safety.py`는 건드리지 않는다, 권한은 시크릿 하나·서비스 계정 하나에만, 권한 부여 전 확인.
- 방문자 키는 **브라우저 sessionStorage에만** 둔다. 서버·DB·쿠키·비밀번호 관리자에 저장되지 않게 한다.
- 키를 로그(stdout·stderr 모두)·오류 메시지·화면·`/api/config` 응답 어디에도 남기지 않는다. 로그에는 방문자 키 사용 여부와 제공자 이름만. 예외는 **종류 이름만** 로그에 남긴다.
- 지원 제공자: `openai`, `gemini`, `claude`. 모델은 `PROVIDERS[provider]["model"]`(`app/config.py`).
- `ACCESS_CODE`가 비어 있으면 방문자 헤더를 **읽지 않는다** — 지금과 완전히 같은 동작.
- 방문자는 `visitor:<방문자 ID>` 사용자로만 저장소를 쓴다. `/api/ocr`은 방문자에게 403.
- `/health`는 열어 둔다. `/version`은 잠금 대상이다.
- 테스트 실행 명령: `.venv/bin/python -m pytest -q` (기준선: 미커밋 잠금 변경 포함 **14 passed**, 2026-10-22 측정). 로컬 수동 실행은 `.env`를 고치지 않고 `ACCESS_CODE=local-test .venv/bin/uvicorn app.main:app --port 8080`.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. 커밋 전 스테이지에 `.env`·키 패턴이 없는지 확인.
- 모든 변경은 작업 브랜치 → PR → 머지. main에 직접 푸시하지 않는다.

## Review Focus

1. **가짜 키로 남의 데이터 보기/지우기**: 아무 문자열을 키로 넣어도 LLM 외 API(`/api/messages`, `DELETE /api/data` 등)는 열리지만, 그 사용자는 `visitor:<id>`라서 발표자 데이터가 보이지 않고 지워지지 않아야 한다. → Task 3 `test_visitor_cannot_see_or_delete_presenter_data`.
2. **틀린 키 / 잔액 없는 키**: OpenAI 401, Gemini 400(`API key not valid`), 429 `insufficient_quota` 모두 "API 키를 확인해 주세요"로 안내하고 서버는 산다. 서버 키 오류는 방문자 안내로 바꾸지 않는다. → Task 2·3 테스트 + Task 5 실측.
3. **화면 무한 이동**: 화면은 서버가 "잠금 때문"이라고 표시한 401(`reason: "locked"`)에서만 `/access`로 간다. Supabase 로그인 만료 401, 키 오류 401에서는 이동하지 않고 안내만. → Task 3 테스트 + Task 5 수동.
4. **반쪽 상태**(표시 쿠키는 있는데 sessionStorage가 비었거나 일부만 있음, 새 탭): 잠금 401 또는 헤더 형식 400 → 화면이 방문자 정보를 지우고 `/access`로. 빈 화면으로 멈추지 않는다. → Task 5 수동.
5. **키가 로그로 새는 경로**: 처리 안 된 예외의 트레이스백(stderr)에 SDK 오류 원문(키 일부 포함 가능)이 찍히지 않게, `chat`과 `greeting` 모두 예외를 잡고 종류 이름만 남긴다. → Task 3 `test_key_never_logged`(stdout·stderr).

---

### Task 1: 작업 브랜치와 문서 커밋

> 2026-10-22 갱신: 접속 코드 잠금(`app/access.py`, `tests/test_access.py`, `tests/conftest.py`의 `ACCESS_CODE: ""`)은 **PR #4로 이미 main에 들어갔다.** 기준선은 main에서 `14 passed`.

**Files:**
- Commit: `docs/superpowers/` (설계 + 계획 2개, 지금 작업 트리에 미커밋 상태로 있음)

**Interfaces:**
- Produces (main에 이미 있음): `access.COOKIE`, `access.OPEN_PATHS`, `access.is_allowed(code, cookie)`, `access.page(error="")`, `settings.access_code`

- [ ] **Step 1: 브랜치** — `git switch main && git pull && git switch -c visitor-llm-key` (미커밋 `docs/superpowers/`는 따라온다)
- [ ] **Step 2: 테스트** — Run: `.venv/bin/python -m pytest -q` / Expected: `14 passed`
- [ ] **Step 3: 커밋** — `git add docs/superpowers/ && git commit` ("음성 캐릭터 설계와 구현 계획 문서")

### Task 2: 방문자 키로 LLM 만들기와 키 오류 판별

**Files:**
- Modify: `app/llm.py`
- Create: `tests/test_visitor_key.py`

**Interfaces:**
- Produces:
  - `VISITOR_PROVIDERS = ("openai", "gemini", "claude")`
  - `make_visitor_llm(provider: str, api_key: str)` → `ClaudeLLM` 또는 `OpenAICompatibleLLM`. 허용 밖 제공자·빈 키면 `ValueError`. OpenAI 계열은 `organization=None, project=None`과 명시한 `base_url`(openai는 `"https://api.openai.com/v1"`)로 만들어 서버 환경변수(`OPENAI_BASE_URL`, `OPENAI_ORG_ID`, `OPENAI_PROJECT`)가 섞이지 않게 한다.
  - 두 LLM 클래스에 `close()` 추가(내부 SDK 클라이언트 `close()` 호출).
  - `is_key_error(error: Exception) -> bool` — True 조건: 클래스 이름이 `AuthenticationError`/`PermissionDeniedError` · `status_code`가 401/403 · `status_code` 400이고 `str(error)`에 `API key` 또는 `API_KEY_INVALID` · `status_code` 429이고 `insufficient_quota` 또는 `quota`. `str(error)`는 판정에만 쓰고 어디에도 남기지 않는다.

- [ ] **Step 1: 실패하는 테스트**

```python
import pytest
from app import llm
from app.config import PROVIDERS

def test_visitor_llm_uses_provider_defaults(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://evil.example/v1")
    m = llm.make_visitor_llm("openai", "k-123")
    assert "api.openai.com" in str(m.client.base_url) and m.model == PROVIDERS["openai"]["model"]
    g = llm.make_visitor_llm("gemini", "k-123")
    assert g.model == PROVIDERS["gemini"]["model"] and "generativelanguage" in str(g.client.base_url)
    assert isinstance(llm.make_visitor_llm("claude", "k-123"), llm.ClaudeLLM)

@pytest.mark.parametrize("provider,key", [("fake", "k"), ("openai", ""), ("unknown", "k")])
def test_visitor_llm_rejects_bad_input(provider, key):
    with pytest.raises(ValueError):
        llm.make_visitor_llm(provider, key)

def err(status, text=""):
    e = Exception(text); e.status_code = status; return e

def test_is_key_error():
    class AuthenticationError(Exception): pass
    assert llm.is_key_error(AuthenticationError())
    assert llm.is_key_error(err(401)) and llm.is_key_error(err(403))
    assert llm.is_key_error(err(400, "API key not valid. Please pass a valid API key."))
    assert llm.is_key_error(err(429, "You exceeded your current quota (insufficient_quota)"))
    assert not llm.is_key_error(err(400, "messages: too long"))
    assert not llm.is_key_error(err(429, "rate limit")) and not llm.is_key_error(TimeoutError())
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/test_visitor_key.py -q` / Expected: FAIL (`make_visitor_llm` 없음)
- [ ] **Step 3: 구현** — Interfaces대로.
- [ ] **Step 4: 통과 확인** — 같은 명령 PASS, 전체 `-q` PASS
- [ ] **Step 5: 커밋** — `git add app/llm.py tests/test_visitor_key.py && git commit` ("방문자 키로 LLM 만들기와 키 오류 판별")

### Task 3: 잠금·사용자·LLM을 방문자 헤더로 나누기

**Files:**
- Modify: `app/access.py`, `app/auth.py`, `app/main.py` (`access_lock`, `get_llm`, `chat`, `greeting`, `read_photo`)
- Test: `tests/test_visitor_key.py`

**Interfaces:**
- Consumes: Task 2의 `VISITOR_PROVIDERS`, `make_visitor_llm`, `is_key_error`, `close()`
- Produces:
  - `access.VISITOR_COOKIE = "llm_mode"` (값 `"visitor"`, 비밀 아님), `access.MAX_KEY_LENGTH = 300`
  - `access.Visitor` dataclass `(provider: str, key: str, id: str)` — `__repr__`에 키를 넣지 않는다(`field(repr=False)`).
  - `access.parse_visitor(headers) -> Visitor | None` — 세 헤더가 모두 없으면 None. 하나라도 있는데 제공자가 허용 밖, 키가 비었거나 300자 초과·출력 가능 ASCII 아님, 방문자 ID가 `^[0-9a-f-]{36}$` 아님이면 `ValueError`.
  - 미들웨어(잠금 켜짐일 때만): 통과 순서 — `OPEN_PATHS` → 유효한 접속 쿠키 → `/api/*`이고 `parse_visitor`가 Visitor면 `request.state.visitor = v` 후 통과(ValueError면 `400 {"detail": "LLM 키 정보가 올바르지 않습니다", "reason": "visitor_header"}`) → 경로가 `/`이고 쿠키 `llm_mode == "visitor"`면 통과 → 나머지는 지금처럼 `/api/*` 401 / 페이지 303. **잠금 401 본문에 `"reason": "locked"`를 더한다.** 접속 쿠키와 방문자 헤더가 둘 다 있으면 방문자로 본다(헤더 검사를 쿠키보다 먼저).
  - `Auth.__call__`: `getattr(request.state, "visitor", None)`이 있으면 `AUTH=none`에서 `User(f"visitor:{v.id}", anonymous=True)`. `AUTH=supabase`면 지금처럼 토큰 사용자.
  - `main.get_llm(request: Request)`: `request.state.visitor`가 있으면 `make_visitor_llm(v.provider, v.key)`(`log("INFO", "방문자 키로 요청", provider=v.provider)`), 없으면 서버 `llm`.
  - `KEY_ERROR_MESSAGE = "API 키를 확인해 주세요. 키가 맞는지, 잔액이나 사용 한도가 남아 있는지 확인해 주세요."`
  - `chat`: `except`에서 방문자 요청이고 `is_key_error`면 `KEY_ERROR_MESSAGE`, 그 밖은 지금 문구. 로그는 `error=type(error).__name__`만. `events()`의 `finally`에서 방문자 LLM이면 `close()`.
  - `greeting`: 모든 예외를 잡는다. 방문자 요청이고 `is_key_error`면 `401 {"detail": KEY_ERROR_MESSAGE, "reason": "key"}`, 그 밖은 로그(종류 이름만) 후 `500 {"detail": "인사를 만들지 못했어요"}`. 끝나면 방문자 LLM `close()`.
  - `read_photo`: 방문자면 `HTTPException(403, "방문자 모드에서는 사진 글자 읽기를 쓸 수 없습니다")`.

- [ ] **Step 1: 실패하는 테스트** (`locked(monkeypatch)`는 `tests/test_access.py`와 같은 방식으로 이 파일에도 둔다)

```python
from app import main
from app.llm import FakeLLM
VID = "0b6f7c1e-1a2b-4c3d-8e9f-0123456789ab"
H = {"X-LLM-Provider": "openai", "X-LLM-Key": "sk-visitor-test-key", "X-Visitor-Id": VID}

def fake_visitor(monkeypatch, cls=FakeLLM, seen=None):
    monkeypatch.setattr(main, "make_visitor_llm", lambda p, k: (seen.append(p) if seen is not None else None) or cls(delay=0))

def test_no_lock_ignores_visitor_headers(monkeypatch):
    seen = []; fake_visitor(monkeypatch, seen=seen)
    from fastapi.testclient import TestClient
    assert TestClient(main.app).post("/api/chat", json={"message": "안녕"}, headers=H).status_code == 200
    assert seen == []

def test_visitor_header_uses_visitor_llm(monkeypatch):
    client = locked(monkeypatch); seen = []; fake_visitor(monkeypatch, seen=seen)
    r = client.post("/api/chat", json={"message": "안녕"}, headers=H)
    assert r.status_code == 200 and '"type": "done"' in r.text and seen == ["openai"]

def test_visitor_cannot_see_or_delete_presenter_data(monkeypatch):
    client = locked(monkeypatch); fake_visitor(monkeypatch)
    client.post("/access", data={"code": "test-code"})
    client.post("/api/chat", json={"message": "발표자 비밀 메모"})
    client.cookies.clear()
    assert "발표자 비밀 메모" not in client.get("/api/messages", headers=H).text
    client.delete("/api/data", headers=H)
    client.post("/access", data={"code": "test-code"})
    assert "발표자 비밀 메모" in client.get("/api/messages").text

def test_visitor_cookie_opens_page_only(monkeypatch):
    client = locked(monkeypatch); client.cookies.set("llm_mode", "visitor")
    assert client.get("/", follow_redirects=False).status_code == 200
    assert client.get("/version", follow_redirects=False).status_code == 303
    r = client.post("/api/chat", json={"message": "안녕"})
    assert r.status_code == 401 and r.json()["reason"] == "locked"

@pytest.mark.parametrize("bad", [{"X-LLM-Provider": "fake"}, {"X-LLM-Key": ""}, {"X-LLM-Key": "k" * 301},
                                 {"X-Visitor-Id": "../etc"}, {"X-LLM-Provider": None}])
def test_bad_visitor_headers_are_400(monkeypatch, bad):
    h = {k: v for k, v in {**H, **bad}.items() if v is not None}
    r = locked(monkeypatch).post("/api/chat", json={"message": "안녕"}, headers=h)
    assert r.status_code == 400 and r.json()["reason"] == "visitor_header"

def test_visitor_key_wins_over_access_cookie(monkeypatch):
    client = locked(monkeypatch); client.post("/access", data={"code": "test-code"})
    seen = []; fake_visitor(monkeypatch, seen=seen)
    client.post("/api/chat", json={"message": "안녕"}, headers=H)
    assert seen == ["openai"]

class KeyFail(FakeLLM):
    def chat_stream(self, messages): raise _auth_error()
    def complete_text(self, messages): raise _auth_error()

def _auth_error():
    class AuthenticationError(Exception): pass
    return AuthenticationError("Incorrect API key provided: sk-visitor-test-key")

def test_wrong_key_chat_is_friendly(monkeypatch):
    client = locked(monkeypatch); fake_visitor(monkeypatch, KeyFail)
    r = client.post("/api/chat", json={"message": "안녕"}, headers=H)
    assert "API 키를 확인해 주세요" in r.text and "sk-visitor" not in r.text

def test_wrong_key_greeting_is_401_key(monkeypatch):
    client = locked(monkeypatch); fake_visitor(monkeypatch, KeyFail)
    client.delete("/api/data", headers=H)  # 최근 대화가 없어야 인사가 LLM을 부른다
    g = client.post("/api/greeting", headers=H)
    assert g.status_code == 401 and g.json()["reason"] == "key" and "sk-visitor" not in g.text

def test_server_key_error_is_not_reported_as_visitor_key(monkeypatch):
    client = locked(monkeypatch); client.post("/access", data={"code": "test-code"})
    main.app.dependency_overrides[main.get_llm] = lambda: KeyFail(delay=0)
    try:
        r = client.post("/api/chat", json={"message": "안녕"})
        assert "API 키를 확인해 주세요" not in r.text
    finally:
        main.app.dependency_overrides.clear()

def test_visitor_cannot_use_ocr(monkeypatch):
    monkeypatch.setattr(main.settings, "ocr_enabled", True)
    r = locked(monkeypatch).post("/api/ocr", headers=H, files={"photo": ("a.png", b"x", "image/png")})
    assert r.status_code == 403

def test_key_never_logged(monkeypatch, capsys):
    client = locked(monkeypatch); fake_visitor(monkeypatch, KeyFail)
    client.post("/api/chat", json={"message": "안녕"}, headers=H)
    client.delete("/api/data", headers=H); client.post("/api/greeting", headers=H)
    out = capsys.readouterr()
    assert "sk-visitor-test-key" not in out.out + out.err
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/test_visitor_key.py -q` / Expected: Task 3 테스트들이 FAIL (`test_no_lock_ignores_visitor_headers`는 지금도 통과할 수 있음 — 회귀 방지용)
- [ ] **Step 3: 구현** — Interfaces대로. `main`은 `from app.llm import make_llm, make_visitor_llm, is_key_error`로 가져와 테스트가 `main.make_visitor_llm`을 바꿔 끼울 수 있게 한다.
- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/python -m pytest -q` / Expected: 전체 PASS (기존 14개 포함)
- [ ] **Step 5: 커밋** — ("방문자 헤더로 사용자·LLM 분리, 키 오류 안내")

### Task 4: 잠금 화면에 "내 API 키로 쓰기" 추가

**Files:**
- Modify: `app/access.py` — `page(error="")`의 HTML (f-string, 중괄호는 `{{ }}`)
- Test: `tests/test_access.py`

**Interfaces:**
- Consumes: `access.VISITOR_COOKIE`, `VISITOR_PROVIDERS`, `PROVIDERS`(모델 이름 표시)
- Produces: sessionStorage 키 이름 `llm_provider`, `llm_key`, `visitor_id`. 쿠키 `llm_mode=visitor; path=/; SameSite=Lax` (+ HTTPS면 `Secure`).

- [ ] **Step 1: 실패하는 테스트**

```python
def test_access_page_offers_visitor_key(monkeypatch):
    html = locked(monkeypatch).get("/access").text
    assert 'id="visitor-key"' in html and 'type="text"' in html and 'autocomplete="off"' in html
    for p in ("openai", "gemini", "claude"):
        assert f'value="{p}"' in html
    assert "sessionStorage" in html and "llm_mode=visitor" in html and "randomUUID" in html
    assert 'id="visitor-form"' not in html or 'method="post"' not in html.split('id="visitor-form"')[1].split(">")[0]
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/test_access.py -q` / Expected: FAIL
- [ ] **Step 3: 구현** — 접속 코드 폼 아래 구분선 + 방문자 영역(`<div id="visitor-form">`, 서버로 전송하는 `<form>`이 아님):
  - 제공자 `<select>` — 표시 "OpenAI (gpt-4o-mini)" 처럼 제공자와 `PROVIDERS` 모델 이름. Claude 옆에 "비용이 큰 모델" 안내.
  - 키 입력 `<input id="visitor-key" type="text" autocomplete="off" spellcheck="false">` + CSS `-webkit-text-security: disc` (비밀번호 관리자 저장 제안 방지).
  - 설명 "키는 이 탭에만 보관되고 탭을 닫으면 사라집니다. 서버에 저장하지 않습니다."
  - "시작" 버튼 클릭 처리: trim → 비었거나 300자 초과·ASCII 밖이면 그 자리에 안내하고 멈춤 → sessionStorage에 `llm_provider`, `llm_key`, `visitor_id`(`crypto.randomUUID()`, 이미 있으면 유지) → 쿠키 설정 → `location.href = "/"`.
- [ ] **Step 4: 통과 확인** — 전체 `-q` PASS
- [ ] **Step 5: 커밋** — ("잠금 화면에 내 API 키로 쓰기 추가")

### Task 5: 대화 화면이 방문자 정보를 보내고 상태를 표시

**Files:**
- Modify: `static/index.html` — `api()`, `/api/config`·`/api/ocr` fetch, topbar, 시작 코드, `greet()`

**Interfaces:**
- Consumes: Task 4의 sessionStorage 키 이름·쿠키, 서버의 `reason` 값(`locked`, `visitor_header`, `key`)
- Produces: `visitorHeaders() -> object`(세 값이 모두 있을 때만 세 헤더, 아니면 `{}`), `clearVisitor()`(세 값 삭제, 쿠키 만료), `isVisitor() -> bool`

- [ ] **Step 1: 구현**
  - `api()`, 시작 시 `/api/config` fetch, `/api/ocr` fetch 모두 `visitorHeaders()`를 합친다.
  - `api()` 응답 처리: 401이고 본문 `reason === "locked"` 또는 400이고 `reason === "visitor_header"` → `clearVisitor()` 후 `location.href = "/access"`. 그 밖의 401(로그인 만료, `reason: "key"`)은 이동하지 않는다(Review Focus 3).
  - `greet()` 실패(`reason: "key"`)면 대화 화면에 "API 키를 확인해 주세요…" 안내 행을 보여 주고 시작은 계속. 그 밖의 인사 실패는 조용히 넘어간다(인사가 없어도 대화는 됨).
  - 방문자면 OCR 버튼을 숨긴다.
  - topbar `ai-badge` 옆 `<span id="keyMode">`: 방문자면 "내 키 사용 중 (OpenAI) · 지우기"(지우기 = `clearVisitor()` 후 `/access`), 아니면 비움. 키 문자열은 어디에도 출력하지 않는다.
- [ ] **Step 2: 자동 테스트** — 전체 `-q` PASS
- [ ] **Step 3: 로컬 수동 확인 (실제 키 사용 — 실행 전 사용자 동의, 호출 몇 번·비용 수 원 이하)** — `ACCESS_CODE=local-test .venv/bin/uvicorn app.main:app --port 8080 2>&1 | tee <스크래치>/server.log`. 크롬 시크릿 창에서:
  1. `/` → `/access`, 두 영역이 보인다. 키 칸에 입력해도 비밀번호 저장 제안이 뜨지 않는다.
  2. 접속 코드로 들어가 "발표자 메모야"라고 보낸 뒤 다른 시크릿 창에서 **사용자 본인 OpenAI 키**로 들어간다 → 답이 오고, 대화 기록에 발표자 메모가 보이지 않는다, topbar "내 키 사용 중 (OpenAI)".
  3. 같은 방문자 창에서 새 탭으로 `/` → `/access`로 돌아간다(반쪽 상태).
  4. 틀린 키 `sk-wrong` → 말풍선에 "API 키를 확인해 주세요…". 화면이 튕기지 않는다.
  5. **Gemini 틀린 키**(`AIzaWrong`)도 같은 안내가 나오는지 — 안 나오면 서버 로그의 예외 **종류 이름**과 상태 코드를 근거로 `is_key_error`를 고치고 Task 2 테스트에 그 경우를 더한다.
  6. `grep -c "sk-\|AIza" <스크래치>/server.log` → 0 (입력한 키 문자열이 로그에 없음).
- [ ] **Step 4: 커밋** — ("대화 화면이 방문자 정보를 보내고 상태 표시")

### Task 6: PR, 머지, 자동 배포 확인과 ACCESS_CODE 연결

**Files:** 없음 (GitHub·Google Cloud 작업)

- [ ] **Step 1: 푸시와 PR** — `git push -u origin visitor-llm-key` → `gh pr create --base main --title "접속 잠금과 방문자 LLM 키 등록" --body "설계: docs/superpowers/specs/2026-10-22-voice-character-design.md 11절"` (본문 끝에 `🤖 Generated with [Claude Code](https://claude.com/claude-code)`). 사용자가 바뀐 줄을 확인한 뒤 머지.
- [ ] **Step 2: 배포 확인** — `gh run watch` → deploy 성공, Cloud Run 새 리비전 트래픽 100%.
- [x] **Step 3: ACCESS_CODE 연결** — 이미 되어 있음(시크릿 `access-code`, `friend-agent-runtime`에 그 시크릿 하나 읽기 권한, 2026-10-22).
- [x] **Step 4: 자동 배포가 연결을 지우지 않는지** — 2026-10-22 PR #4~#8 자동 배포 뒤에도 `ACCESS_CODE` 연결 유지, `/` → `/access` 303 실측.
- [ ] **Step 5: 성공 기준 확인 (스펙 11절)** — 접속 코드로 서버 키 대화 / 다른 브라우저에서 내 키로 대화(제공자 사용량 화면에서 호출 확인) / 키·코드 없이 `/api/chat` 401 / 가짜 키로 `/api/messages`에 발표자 대화 없음 / Cloud Run 로그 탐색기에서 키 문자열 검색 0건 / 틀린 키 안내 / `pytest` 통과.

## 받아들인 한계 (이번에 고치지 않음)

- 요청 수 제한이 없어 이 서버로 남의 키가 유효한지 확인해 볼 수 있다. 시연 기간 한정 공개라 받아들인다.
- 방문자 데이터는 서버 메모리(`STORAGE=memory`)에 방문자 ID별로 쌓이고 재배포 때 사라진다. 방문자가 많으면 메모리가 늘어난다.
- 같은 방문자 ID를 아는 사람(같은 탭)은 같은 데이터를 본다. 탭마다 ID가 새로 생긴다.

### Task 7: 잠금 화면 [키 검사] — 통과해야 시작 (2026-10-22 사용자 요청 추가)

**Files:**
- Modify: `app/llm.py` — 두 LLM 클래스에 `verify_key()`
- Modify: `app/main.py` — `POST /api/visitor/check`
- Modify: `app/access.py` — 잠금 화면 버튼·JS
- Test: `tests/test_visitor_key.py`, `tests/test_access.py`

**Interfaces:**
- Consumes: `request.state.visitor`(Task 3), `get_llm`, `is_key_error`, `KEY_ERROR_MESSAGE`, `close()`
- Produces:
  - `OpenAICompatibleLLM.verify_key()` / `ClaudeLLM.verify_key()` → None. 토큰을 쓰지 않는 모델 목록 조회 한 번(`client.models.list()`, Claude는 `limit=1`). 실패하면 SDK 예외를 그대로 올린다. `FakeLLM.verify_key()`는 아무것도 하지 않는다.
  - `POST /api/visitor/check` (인증 의존성 `current_user`를 쓰지 않음 — 사용자 데이터를 만들지 않는다)
    - 방문자 헤더 없음(잠금 꺼짐 포함) → `400 {"detail": "방문자 키 정보가 필요합니다", "reason": "visitor_header"}`
    - 성공 → `200 {"ok": true}`
    - `is_key_error` → `401 {"detail": KEY_ERROR_MESSAGE, "reason": "key"}`
    - 그 밖 → `502 {"detail": "제공자 서버에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요.", "reason": "provider"}`
    - 로그는 `log("INFO", "방문자 키 검사", provider=..., result="ok"|"key"|"provider")`와 실패 시 예외 종류 이름만. 끝나면 방문자 LLM `close()`.
  - 잠금 화면: 키 칸 아래 `<button id="visitor-check">키 검사</button>`, 결과 `<p id="visitor-check-result">`, `[시작]`(`id="visitor-start"`)은 처음에 `disabled`.
    - 검사: 형식 검사(기존과 같음) → 방문자 ID는 sessionStorage에 있으면 그것, 없으면 `crypto.randomUUID()`를 메모리에만 → 세 헤더로 `fetch("/api/visitor/check", {method: "POST"})` → 결과 문구 표시(✅ "사용할 수 있는 키예요" / 서버 `detail`) → 200일 때만 `[시작]` 활성화.
    - 키·제공자를 바꾸면 결과를 지우고 `[시작]`을 다시 잠근다. 검사 중에는 두 버튼을 잠근다.
    - `[시작]`은 지금과 같이 sessionStorage 저장·쿠키·이동. 키를 서버에 저장하거나 폼으로 보내지 않는다.

- [ ] **Step 1: 실패하는 테스트** — `test_check_requires_visitor`(잠금 + 헤더 없음 → 400 `visitor_header`), `test_check_ok`(가짜 LLM verify_key 성공 → 200 `{"ok": true}`), `test_check_wrong_key`(AuthenticationError → 401 `key`, 응답·로그(stdout+stderr)에 키 없음), `test_check_provider_error`(TimeoutError → 502 `provider`), `test_check_does_not_create_user_data`(검사 뒤 `main.store`에 `visitor:<VID>` 사용자 데이터가 생기지 않음), 잠금 화면 테스트에 `id="visitor-check"`, `id="visitor-start"`와 `disabled` 단언 추가.
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_visitor_key.py tests/test_access.py -q`
- [ ] **Step 3: 구현** — 위 Interfaces대로. 렌더된 잠금 화면 script는 `node --check`.
- [ ] **Step 4: 통과 확인** — 전체 `-q` PASS
- [ ] **Step 5: 커밋**
- [ ] **Step 6: 수동 확인(사용자)** — 로컬 `ACCESS_CODE=local-test`로 본인 키 ✅ → 시작 활성, `sk-wrong` ❌ → 시작 잠김, 키 수정 시 다시 잠김. Gemini·Claude 키가 있으면 각 1회.
