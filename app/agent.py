"""메시지 하나를 처리하는 흐름(한 턴).

① 안전장치: 위기 표현이면 정해진 안전 응답으로 끝낸다(코드 규칙).
② 도구 선택: 모델이 도구를 0~2개 고른다. 확신이 낮으면 되묻는다(router.py).
③ 도구 실행: 날씨, 미세먼지, 공휴일, 약속(tools.py).
④ 답 생성: 캐릭터 지시문 + 기억 + 도구 결과 + 최근 대화로 답을 스트리밍한다.
⑤ 기억 정리: 답을 다 보낸 뒤 같은 요청 안에서 기억 메모와 대화 요약을 갱신한다(memory.py).
   Cloud Run 기본 설정(요청 기반 과금)에서는 응답이 끝난 뒤 CPU가 거의 주어지지 않아서,
   응답 뒤 백그라운드로 돌리면 기억 정리가 멈추거나 늦어진다. 그래서 응답을 닫기 전에 처리한다.

결과는 이벤트(dict)로 하나씩 내보낸다. show_panel 이 꺼져 있으면(production) 개발용 이벤트는 아예 만들지 않는다.
"""
from datetime import datetime, timedelta, timezone

from app import memory, tracing, when
from app.logs import log
from app.persona import DEFAULT_CHARACTER, build_persona
from app.router import choose
from app.safety import CRISIS_REPLY, is_crisis
from app.tools import TOOLS, Context, run_tool

GREETING_GAP = timedelta(hours=6)  # 마지막 대화 뒤 이만큼 지나면 앱을 열 때 먼저 말을 건다


def system_prompt(character, now, location, memories, summary, tool_lines=(), clarify=False):
    parts = [build_persona(character),
             f"[지금] {when.label(now)} ({now.tzinfo}), 사용자 지역: {location}\n[달력]\n{when.calendar_hint(now)}"]
    block = memory.memory_block(memories, summary)
    if block:
        parts.append(block)
    if tool_lines:
        parts.append("[도구 결과]\n" + "\n".join(f"- {line}" for line in tool_lines))
    if clarify:
        parts.append("[되묻기]\n사용자가 무엇을 원하는지 확실하지 않다. 짧게 되물어서 확인한다.")
    return "\n\n".join(parts)


def run_turn(message, uid, llm, store, show_panel):
    """한 턴을 처리한다. Langfuse 계측(chat-turn 트레이스)은 app/tracing.py 를 거친다(키가 없으면 아무것도 안 함)."""
    root = tracing.start("chat-turn", input=message)
    try:
        yield from _run_turn(message, uid, llm, store, show_panel, root)
    finally:
        root.end()


def _run_turn(message, uid, llm, store, show_panel, root):
    settings = store.get_settings(uid)
    character = store.get_character(uid) or DEFAULT_CHARACTER
    now = when.now_in(settings["timezone"])
    memories, summary, recent = memory.build_context(store, uid)
    history = [{"role": m["role"], "content": m["content"]} for m in recent]
    store.add_message(uid, "user", message)

    # ① 위기 표현
    crisis = is_crisis(message)
    tracing.child(root, "safety", input=message).update(output={"crisis": crisis}).end()
    if crisis:
        guards = ["위기 표현이 있어 캐릭터 설정과 상관없이 정해진 안전 응답을 보냈습니다."]
        if show_panel:
            yield {"type": "turn", "tools": [], "confidence": None, "reason": "", "decided_by": "rule",
                   "action": "safety", "guards": guards}
        yield {"type": "delta", "text": CRISIS_REPLY}
        saved = store.add_message(uid, "assistant", CRISIS_REPLY, {"action": "safety", "guards": guards,
                                                                   "trace_id": root.trace_id})
        log("WARNING", "위기 표현 안전 응답")
        root.update(output=CRISIS_REPLY, metadata={"message_id": saved["id"], "action": "safety"})
        yield {"type": "done", "message_id": saved["id"]}
        return

    # ② 도구 선택
    obs = tracing.child(root, "choose-tools", "generation")
    call = {}
    choice = choose(message, history, now, settings["location"], llm, trace=call)
    tracing.record_llm(obs, call)  # 규칙이 정했으면 call 이 비어 토큰 0
    obs.update(output={"tools": choice.tools, "confidence": choice.confidence, "reason": choice.reason,
                       "action": choice.action, "guards": choice.guards},
               metadata={"decided_by": choice.decided_by, "confidence": choice.confidence}).end()
    if show_panel:
        yield {"type": "turn", "tools": [{"name": t["name"], "label": TOOLS[t["name"]]["label"], "args": t["args"]}
                                         for t in choice.tools],
               "confidence": choice.confidence, "reason": choice.reason, "decided_by": choice.decided_by,
               "action": choice.action, "guards": choice.guards}

    # ③ 도구 실행 (도구마다 관찰 단위 하나)
    ctx = Context(store, uid, settings["location"], settings["timezone"])
    results = []
    for t in choice.tools:
        span = tracing.child(root, f"tool:{t['name']}", input=t["args"])
        r = run_tool(t["name"], t["args"], ctx)
        span.update(output={"ok": r.ok, "summary": r.summary}, metadata={"ok": r.ok},
                    level="DEFAULT" if r.ok else "WARNING").end()
        results.append(r)
    for r in results:
        if show_panel:
            yield {"type": "tool", "name": r.name, "label": TOOLS[r.name]["label"], "ok": r.ok,
                   "summary": r.summary, "data": r.data}
        if r.ok and r.name in ("create_reminder", "cancel_reminder"):
            yield {"type": "reminder", "action": r.name, "summary": r.summary}

    # ④ 답 생성
    system = system_prompt(character, now, settings["location"], memories, summary,
                           [r.summary for r in results], clarify=choice.action == "clarify")
    if show_panel:
        yield {"type": "context", "memories": [m["text"] for m in memories],
               "summary": summary["text"] if summary else None, "recent_messages": len(history)}
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": message}]
    obs = tracing.child(root, "reply", "generation", input=messages, model=getattr(llm, "model", None))
    call = {}
    reply = ""
    for chunk in llm.chat_stream(messages, trace=call):
        if not reply:
            obs.update(completion_start_time=datetime.now(timezone.utc))  # 사용자가 첫 글자를 본 시각
        reply += chunk
        yield {"type": "delta", "text": chunk}
    tracing.record_llm(obs, call)
    obs.update(output=reply).end()
    meta = {"tools": [t["name"] for t in choice.tools], "confidence": choice.confidence,
            "action": choice.action, "guards": choice.guards,
            "trace_id": root.trace_id}  # 화면 반응(점수)을 이 트레이스에 붙이려고 저장한다. 키가 없으면 None
    saved = store.add_message(uid, "assistant", reply, meta)
    root.update(output=reply, metadata={"message_id": saved["id"]})

    # ⑤ 기억 정리
    yield {"type": "memory_start"}
    obs = tracing.child(root, "memory-update", "generation")
    call = {} if getattr(llm, "can_reason", False) else {"model": getattr(llm, "model", None)}
    changes = memory.update_memories(store, uid, message, reply, now, llm, trace=call)
    tracing.record_llm(obs, call)
    obs.update(output={k: [m["text"] for m in v] for k, v in changes.items()}).end()
    memory.maybe_summarize(store, uid, llm, parent=root)
    yield {"type": "memory", "added": [m["text"] for m in changes["added"]],
           "updated": [m["text"] for m in changes["updated"]], "removed": [m["text"] for m in changes["removed"]]}
    yield {"type": "done", "message_id": saved["id"]}


def greet(uid, llm, store):
    """앱을 열 때 먼저 말을 건다. 때가 된 약속이 있거나, 대화가 처음이거나, 오래 쉬었을 때만.

    약속은 '전달됨'으로 바꾸는 데 성공한 것만 전한다. 창을 두 개 열어도 같은 약속을 두 번 전하지 않는다.
    """
    settings = store.get_settings(uid)
    character = store.get_character(uid) or DEFAULT_CHARACTER
    now = when.now_in(settings["timezone"])
    due = store.claim_due_reminders(uid, now)
    last = store.recent_messages(uid, 1)
    if not due and last and now - last[-1]["created_at"] < GREETING_GAP:
        return None

    memories, summary, _ = memory.build_context(store, uid)
    lines = [f"약속 알림: '{r['text']}' ({when.label(r['due_at'].astimezone(now.tzinfo))} 예정이었음)" for r in due]
    system = system_prompt(character, now, settings["location"], memories, summary, lines)
    guide = ["사용자가 방금 앱을 열었다. 네가 먼저 1~2문장으로 말을 건다."]
    guide.append("[도구 결과]의 약속 알림을 꼭 전한다." if due else "전할 약속은 없다. 약속이나 알림 이야기를 꺼내지 않는다.")
    guide.append("기억 메모에 오늘이거나 이미 지난 일정이 있으면 어땠는지 물어본다.")
    if not last:
        guide.append("처음 만난 사이다. 이름을 넣어 짧게 자기소개를 한다.")
    system += "\n\n[먼저 말 걸기]\n" + " ".join(guide)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "(앱을 열었다. 먼저 말을 걸어 줘)"}]
    obs = tracing.start("greeting", "generation", metadata={"reminders": len(due)})  # 모델 호출 하나라 루트가 곧 generation
    call = {}
    try:
        text = llm.complete_text(messages, trace=call)
        tracing.record_llm(obs, call)
        obs.update(output=text)
    finally:
        obs.end()
    saved = store.add_message(uid, "assistant", text, {"action": "greeting", "reminders": [r["id"] for r in due],
                                                       "trace_id": obs.trace_id})
    log("INFO", "먼저 말 걸기", reminders=len(due))
    return {"id": saved["id"], "text": text, "reminders": [r["text"] for r in due]}
