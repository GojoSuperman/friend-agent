"""AI 친구 에이전트 서비스.

화면(/)과 API를 FastAPI 하나가 함께 제공한다.
- /health, /version: 서비스 상태와 배포 버전
- /api/config: 화면이 쓰는 설정(로그인 방식, 개발용 패널 표시 여부)
- /api/character, /api/settings: 캐릭터와 사용자 설정
- /api/chat: 메시지 한 턴. 결과를 이벤트(JSON 한 줄씩)로 스트리밍한다
- /api/greeting: 앱을 열 때 먼저 말 걸기
- /api/ocr: 사진 속 글자 읽기(OCR_ENABLED=true 일 때)
- /api/pdf: PDF 공지문 글자 읽기(OCR_ENABLED=true 일 때)
- /api/memories, /api/reminders, /api/data: 기억, 약속, 내 데이터 관리
"""
import html
import json
import os
import time

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from app import access, agent, ocr, pdf, when
from app.auth import Auth, User
from app.config import ROOT, load_settings
from app.llm import is_key_error, make_llm, make_visitor_llm
from app.logs import log
from app.persona import DEFAULT_CHARACTER, OPTIONS, build_persona, clean_character
from app.store.base import make_store
from app.tools import CITIES

settings = load_settings()  # 꼭 필요한 값이 없으면 여기서 종료한다
llm = make_llm(settings)
store = make_store(settings)
current_user = Auth(settings)

app = FastAPI(title="AI 친구 에이전트")

# 시작 지연 스위치(배포 실습용). 포트는 바로 열지만 정한 시간 동안은 준비 중이라 모든 요청에 503을 돌려준다.
# AI 서비스가 시작할 때 모델 설정이나 색인을 불러오느라 늦게 준비되는 상황을 흉내 낸다. 기본값 0(끔).
SLOW_START_SECONDS = float(os.environ.get("SLOW_START_SECONDS", "0") or 0)
READY_AT = time.time() + SLOW_START_SECONDS
if SLOW_START_SECONDS:
    log("WARNING", f"시작 지연 스위치가 켜져 있습니다. {SLOW_START_SECONDS:.0f}초 동안 준비 중으로 응답합니다.")


@app.middleware("http")
async def not_ready_yet(request, call_next):
    if time.time() < READY_AT:
        return JSONResponse({"status": "starting"}, status_code=503)
    return await call_next(request)


@app.middleware("http")
async def access_lock(request, call_next):
    # ACCESS_CODE 가 있으면 코드를 입력한 브라우저만 화면과 API를 쓸 수 있다. LLM 비용을 지키는 잠금이다.
    # 코드가 없는 방문자는 자기 LLM 키를 헤더로 보내 API를 쓸 수 있다. 잠금이 꺼져 있으면 방문자 헤더는 읽지 않는다.
    code = settings.access_code
    path = request.url.path
    if not code or path in access.OPEN_PATHS:
        return await call_next(request)
    if path.startswith("/api/"):  # 방문자 헤더를 접속 쿠키보다 먼저 본다(둘 다 있으면 방문자)
        try:
            visitor = access.parse_visitor(request.headers)
        except ValueError:
            return JSONResponse({"detail": "LLM 키 정보가 올바르지 않습니다", "reason": "visitor_header"}, status_code=400)
        if visitor:
            request.state.visitor = visitor
            return await call_next(request)
    if access.is_allowed(code, request.cookies.get(access.COOKIE)):
        return await call_next(request)
    if path == "/" and request.cookies.get(access.VISITOR_COOKIE) == "visitor":
        return await call_next(request)  # 방문자 모드는 화면만 연다. API는 키 헤더가 있어야 한다
    if path.startswith("/api/"):
        return JSONResponse({"detail": "접속 코드가 필요합니다", "reason": "locked"}, status_code=401)
    return RedirectResponse("/access", status_code=303)


@app.get("/access", response_class=HTMLResponse)
def access_page():
    if not settings.access_code:
        return RedirectResponse("/", status_code=303)
    return access.page()


@app.post("/access")
def access_submit(request: Request, code: str = Form("")):
    if not settings.access_code:
        return RedirectResponse("/", status_code=303)
    if not access.code_matches(settings.access_code, code):
        time.sleep(1)  # 무작위로 맞혀 보는 시도를 늦춘다
        log("WARNING", "접속 코드 틀림")  # 입력한 값은 남기지 않는다
        return HTMLResponse(access.page("코드가 맞지 않아요."), status_code=401)
    response = RedirectResponse("/", status_code=303)
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(access.COOKIE, access.token(settings.access_code), max_age=access.MAX_AGE,
                        httponly=True, secure=https, samesite="lax")
    return response


KEY_ERROR_MESSAGE = "API 키를 확인해 주세요. 키가 맞는지, 잔액이나 사용 한도가 남아 있는지 확인해 주세요."


def visitor_of(request):
    return getattr(request.state, "visitor", None)


def get_llm(request: Request):
    """방문자 요청이면 방문자 키로 만든 LLM, 아니면 서버 LLM. 테스트에서는 가짜 LLM을 돌려주는 함수로 바꿔 끼운다."""
    visitor = visitor_of(request)
    if visitor:
        log("INFO", "방문자 키로 요청", provider=visitor.provider)  # 키는 남기지 않는다
        return make_visitor_llm(visitor.provider, visitor.key)
    return llm


def close_visitor_llm(request, model):
    # 방문자 LLM은 요청마다 새로 만들므로 끝나면 연결을 닫는다. 서버 LLM은 닫지 않는다.
    close = getattr(model, "close", None)
    if visitor_of(request) and close:
        close()


@app.post("/api/visitor/check")
def check_visitor_key(request: Request, model=Depends(get_llm)):
    """잠금 화면의 [키 검사]. 방문자 키로 모델 목록을 한 번 조회해 본다.
    current_user를 쓰지 않으므로 visitor:<ID> 데이터가 생기지 않는다. 키는 저장하지도, 로그에 남기지도 않는다."""
    visitor = visitor_of(request)
    if not visitor:  # 잠금이 꺼져 있거나 헤더가 없으면 검사할 방문자 키가 없다
        return JSONResponse({"detail": "방문자 키 정보가 필요합니다", "reason": "visitor_header"}, status_code=400)
    try:
        model.verify_key()
        log("INFO", "방문자 키 검사", provider=visitor.provider, result="ok")
        return {"ok": True}
    except Exception as error:  # 오류 문구에 키가 들어 있을 수 있어 종류 이름만 남긴다
        result = "key" if is_key_error(error) else "provider"
        log("INFO", "방문자 키 검사", provider=visitor.provider, result=result, error=type(error).__name__)
        if result == "key":
            return JSONResponse({"detail": KEY_ERROR_MESSAGE, "reason": "key"}, status_code=401)
        return JSONResponse({"detail": "제공자 서버에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요.",
                             "reason": "provider"}, status_code=502)
    finally:
        close_visitor_llm(request, model)


class ChatRequest(BaseModel):
    message: str


class SettingsRequest(BaseModel):
    location: str | None = None
    timezone: str | None = None


class ReportRequest(BaseModel):
    message_id: int
    reason: str = ""


@app.get("/", response_class=HTMLResponse)
def home():
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    banner = "" if settings.env in ("production", "local") else f'<div class="banner">테스트 환경 ({settings.env})</div>'
    if os.environ.get("BANNER"):  # 이미지를 새로 만들지 않고 환경변수만 바꿔 새 리비전을 만들 때 쓰는 화면 문구
        banner += f'<div class="banner">{html.escape(os.environ["BANNER"][:80])}</div>'
    return page.replace("<!--ENV_BANNER-->", banner)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {"commit": settings.commit, "revision": settings.revision, "env": settings.env,
            "llm": settings.provider, "model": settings.model, "storage": settings.storage, "auth": settings.auth,
            "banner": os.environ.get("BANNER", "")}


@app.get("/api/config")
def config():
    # publishable key 는 브라우저에 공개해도 되는 키다. secret key 는 절대 여기에 넣지 않는다.
    return {"env": settings.env, "auth": settings.auth, "storage": settings.storage,
            "supabase_url": settings.supabase_url if settings.auth == "supabase" else "",
            "supabase_publishable_key": settings.supabase_publishable_key if settings.auth == "supabase" else "",
            "google_login": settings.google_login, "show_turn_panel": settings.show_turn_panel,
            "ocr_enabled": settings.ocr_enabled,
            "options": OPTIONS, "cities": list(CITIES)}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "anonymous": user.anonymous, "email": user.email,
            "has_character": store.get_character(user.id) is not None}


# ── 캐릭터와 설정 ──────────────────────────────────────────────────

@app.get("/api/character")
def get_character(user: User = Depends(current_user)):
    c = store.get_character(user.id)
    return {"character": c or DEFAULT_CHARACTER, "saved": c is not None}


@app.put("/api/character")
def put_character(data: dict, user: User = Depends(current_user)):
    c = clean_character(data)
    store.save_character(user.id, c)
    log("INFO", "캐릭터 저장")
    return {"character": c}


@app.post("/api/character/preview")
def preview_character(data: dict):
    return {"prompt": build_persona(clean_character(data))}


@app.get("/api/settings")
def get_settings(user: User = Depends(current_user)):
    return store.get_settings(user.id)


@app.put("/api/settings")
def put_settings(req: SettingsRequest, user: User = Depends(current_user)):
    changes = {}
    if req.location in CITIES:
        changes["location"] = req.location
    if req.timezone:
        changes["timezone"] = req.timezone if req.timezone == "Asia/Seoul" else "Asia/Seoul"  # 지금은 한국 시간만
    store.save_settings(user.id, changes)
    return store.get_settings(user.id)


# ── 대화 ───────────────────────────────────────────────────────────

@app.get("/api/messages")
def messages(user: User = Depends(current_user)):
    show = settings.show_turn_panel
    return [{"id": m["id"], "role": m["role"], "content": m["content"],
             "meta": m["meta"] if show else {}, "created_at": m["created_at"].isoformat()}
            for m in store.recent_messages(user.id, 50)]


@app.post("/api/chat")
def chat(req: ChatRequest, request: Request, user: User = Depends(current_user), model=Depends(get_llm)):
    message = req.message.strip()[:1000]
    if not message:
        close_visitor_llm(request, model)
        raise HTTPException(400, "메시지가 비어 있습니다")

    def events():
        try:
            for event in agent.run_turn(message, user.id, model, store, settings.show_turn_panel):
                yield json.dumps(event, ensure_ascii=False, default=str) + "\n"
        except Exception as error:
            log("ERROR", "대화 처리 실패", error=type(error).__name__)  # 오류 문구에 키가 들어 있을 수 있어 종류만 남긴다
            text = KEY_ERROR_MESSAGE if visitor_of(request) and is_key_error(error) else "답을 만들지 못했어요. 잠시 뒤 다시 보내 주세요."
            yield json.dumps({"type": "error", "message": text}, ensure_ascii=False) + "\n"
        finally:
            close_visitor_llm(request, model)

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.post("/api/greeting")
def greeting(request: Request, user: User = Depends(current_user), model=Depends(get_llm)):
    # 모든 예외를 여기서 잡는다. 그대로 올라가면 키가 든 오류 문구가 서버 오류 출력에 찍힐 수 있다.
    try:
        return {"greeting": agent.greet(user.id, model, store)}
    except Exception as error:
        if visitor_of(request) and is_key_error(error):
            return JSONResponse({"detail": KEY_ERROR_MESSAGE, "reason": "key"}, status_code=401)
        log("ERROR", "먼저 말 걸기 실패", error=type(error).__name__)
        return JSONResponse({"detail": "인사를 만들지 못했어요"}, status_code=500)
    finally:
        close_visitor_llm(request, model)


@app.post("/api/ocr")
async def read_photo(photo: UploadFile, request: Request, user: User = Depends(current_user)):
    if visitor_of(request):
        raise HTTPException(403, "방문자 모드에서는 사진 글자 읽기를 쓸 수 없습니다")
    if not settings.ocr_enabled:
        raise HTTPException(404, "사진 글자 읽기가 꺼져 있습니다")
    try:
        return {"text": ocr.read_text(await photo.read())}
    except ocr.OcrError as error:
        raise HTTPException(500, str(error)) from error


@app.post("/api/pdf")
async def read_pdf(document: UploadFile, request: Request, user: User = Depends(current_user)):
    if visitor_of(request):
        raise HTTPException(403, "방문자 모드에서는 PDF 글자 읽기를 쓸 수 없습니다")
    if not settings.ocr_enabled:
        raise HTTPException(404, "글자 읽기가 꺼져 있습니다")
    try:
        return {"text": pdf.read_text(await document.read())}
    except pdf.PdfError as error:
        raise HTTPException(500, str(error)) from error


@app.post("/api/report")
def report(req: ReportRequest, user: User = Depends(current_user)):
    # 지금은 로그로만 남긴다. 관측 도구를 붙이면 그 도구의 점수로 보낸다.
    log("WARNING", "부적절한 답변 신고", message_id=req.message_id, reason_length=len(req.reason))
    return {"ok": True}


# ── 기억, 약속, 내 데이터 ───────────────────────────────────────────

@app.get("/api/memories")
def list_memories(user: User = Depends(current_user)):
    return [{"id": m["id"], "kind": m["kind"], "text": m["text"]} for m in store.list_memories(user.id)]


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: int, user: User = Depends(current_user)):
    store.delete_memory(user.id, memory_id)
    return {"ok": True}


@app.delete("/api/memories")
def clear_memories(user: User = Depends(current_user)):
    store.clear_memories(user.id)
    return {"ok": True}


@app.get("/api/reminders")
def list_reminders(user: User = Depends(current_user)):
    tz = when.now_in(store.get_settings(user.id)["timezone"]).tzinfo
    return [{"id": r["id"], "text": r["text"], "status": r["status"],
             "due_label": when.label(r["due_at"].astimezone(tz))} for r in store.list_reminders(user.id)]


@app.delete("/api/reminders/{reminder_id}")
def cancel_reminder(reminder_id: int, user: User = Depends(current_user)):
    store.cancel_reminder(user.id, reminder_id)
    return {"ok": True}


@app.delete("/api/data")
def delete_data(user: User = Depends(current_user)):
    store.delete_all(user.id)
    log("INFO", "사용자 데이터 전체 삭제")
    return {"ok": True}
