# PR ② 음성 대화(마이크 + 타이핑 + 목소리 답) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 대화 화면에서 🎤로 말하거나 글자로 입력하면, 캐릭터의 답을 브라우저 내장 음성으로 읽어 준다. PR 머지 → 자동 배포로 내보낸다(14강 과제 1).

**Architecture:** 서버는 바꾸지 않는다. `static/index.html` 한 파일에 ① DOM이 없는 순수 음성 도우미 블록(문장 끊기, 읽을 글 정리, 목소리 고르기), ② 말하기 큐 `speaker`(`speechSynthesis`), ③ 마이크(`SpeechRecognition`)를 더한다. 순수 도우미 블록은 표시 주석 사이에 두고 pytest가 node로 실행해 검사한다.

**Tech Stack:** HTML/JS(모듈 스크립트, 라이브러리 없음) · Web Speech API · pytest + node

**Spec:** `docs/superpowers/specs/2026-10-22-voice-character-design.md` 5.3·5.4절. **범위 축소(2026-10-22 사용자 결정):** 움직이는 2D 캐릭터·캐릭터 고르기·A/B 배치(5.1·5.2절)는 다음 PR로 미룬다. 이 계획은 `2026-10-22-voice-character.md`의 Task 1·3·5·6을 화면 무대 없이 다시 쓴 것이다.

## Global Constraints

- 바뀌는 파일은 `static/index.html`과 `tests/`뿐. `app/` 서버 코드는 바꾸지 않는다.
- 외부 라이브러리·이미지를 새로 들이지 않는다. 유료 API를 쓰지 않는다. 브라우저 내장 음성만(`lang = "ko-KR"`).
- 목소리: 캐릭터 성별 `여성`(또는 `밝히지 않음`) → `Google 한국의` pitch **1.1** rate **1.05** / `남성` → `Google 한국의` pitch **0.6** rate **0.95**. 남성 한국어 목소리(이름이 `/injoon|hyunsu|male|남성/i`)가 있으면 남성은 그 목소리에 pitch **1.0**.
- 시연 PC 실측(Windows · Chrome 155): 한국어 목소리는 `Microsoft Heami - Korean (Korean)`, `Google 한국의` 두 개, 둘 다 여성.
- **글자 입력은 지금처럼 그대로** 동작한다(Enter 전송, 추천 문장 버튼, 사진 OCR).
- PR ①의 방문자 헤더 처리(`api()`, `visitorHeaders()`)를 건드리지 않는다.
- 테스트 실행 명령: `.venv/bin/python -m pytest -q` (기준선 main `44 passed`)
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. 작업 브랜치 `voice-chat` → PR → 머지.

## Review Focus

1. **스트리밍 중 문장이 조각나 들어옴**(`delta`가 "내일 서", "울은 맑대." 처럼): 완성된 문장만 읽고, 남은 조각은 `done`/`error` 때 마저 읽는다. → Task 1 `splitSentences` 테스트.
2. **이모지·URL·마크다운 기호**: 읽지 않는다. 전부 이모지인 문장은 건너뛴다. → Task 1 `cleanForSpeech` 테스트.
3. **목소리 목록이 늦게 채워짐**(크롬 `getVoices()`가 처음엔 빈 배열): `voiceschanged` 뒤 다시 고른다. 한국어 목소리가 없으면 소리 없이 넘어간다(오류 없음). → Task 1 `pickVoice` + Task 2 수동.
4. **말하는 중 새 메시지를 보내거나 🎤를 누름**: 이전 말하기를 즉시 멈추고 큐를 비운다(두 답이 겹치면 안 됨). → Task 2·3 수동.
5. **마이크 권한 거부 / `no-speech`**: 안내 한 줄을 보여 주고 🎤를 원래 상태로 돌린다. "듣는 중"에 멈춰 있으면 안 된다. → Task 3 수동.

---

### Task 1: 순수 음성 도우미와 node 검사

**Files:**
- Modify: `static/index.html` — 모듈 스크립트 맨 앞(상수 `SUGGEST` 위)에 `// ── 음성 도우미 시작 ──` 과 `// ── 음성 도우미 끝 ──` 표시 주석 사이 블록
- Create: `tests/test_voice_helpers.py`

**Interfaces:**
- Produces (블록 안, DOM·전역 의존 없음):
  - `VOICE_PRESETS = {"여성": {preferred: "Google 한국의", pitch: 1.1, rate: 1.05}, "남성": {preferred: "Google 한국의", pitch: 0.6, rate: 0.95, malePitch: 1.0}}`
  - `splitSentences(buffer: string) -> {done: string[], rest: string}` — `.` `?` `!` `…` `~` 와 줄바꿈 뒤에서 끊는다(부호 포함). 숫자 사이 마침표(`3.5`)는 끊지 않는다.
  - `cleanForSpeech(text: string) -> string` — 이모지(`\p{Extended_Pictographic}`, 변형 선택자, ZWJ), URL, `*` `#` `` ` `` `_` `>` 기호를 지우고 공백을 하나로, 앞뒤 공백 제거.
  - `pickVoice(voices: {name: string, lang: string}[], gender: string) -> {voice: object|null, pitch: number, rate: number}` — `lang`이 `ko`로 시작하는 것만. 성별이 `"남성"`이고 남성 이름 목소리가 있으면 그 목소리 + `malePitch`. 아니면 `preferred` 이름 → 없으면 첫 한국어 목소리 → 없으면 `voice: null`. `"남성"`이 아니면 여성 설정.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_voice_helpers.py`가 `static/index.html`에서 표시 주석 사이를 잘라 `CASES`와 함께 임시 파일(`tmp_path`)에 써서 `node`로 실행, 종료 코드 0과 출력 `ok` 확인. `shutil.which("node")`가 없으면 `pytest.skip`.

```python
CASES = r"""
const assert = require("assert");
let r = splitSentences("내일 서울은 맑대! 최고 18도");
assert.deepStrictEqual(r, {done: ["내일 서울은 맑대!"], rest: " 최고 18도"});
assert.deepStrictEqual(splitSentences("기온은 3.5도야.").done, ["기온은 3.5도야."]);
assert.deepStrictEqual(splitSentences("첫 줄\n둘째").done, ["첫 줄\n"]);
assert.strictEqual(cleanForSpeech("좋아 😊 https://a.b/c 알려 줄게 **꼭**"), "좋아 알려 줄게 꼭");
assert.strictEqual(cleanForSpeech("👍👍"), "");
const V = [{name: "Microsoft Heami - Korean (Korean)", lang: "ko-KR"}, {name: "Google 한국의", lang: "ko-KR"}, {name: "Google US English", lang: "en-US"}];
assert.deepStrictEqual([pickVoice(V, "여성").voice.name, pickVoice(V, "여성").pitch], ["Google 한국의", 1.1]);
assert.deepStrictEqual([pickVoice(V, "남성").voice.name, pickVoice(V, "남성").pitch, pickVoice(V, "남성").rate], ["Google 한국의", 0.6, 0.95]);
assert.strictEqual(pickVoice([...V, {name: "Microsoft InJoon Online (Natural) - Korean", lang: "ko-KR"}], "남성").pitch, 1.0);
assert.strictEqual(pickVoice(V, "밝히지 않음").pitch, 1.1);
assert.strictEqual(pickVoice([V[2]], "여성").voice, null);
console.log("ok");
"""
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_voice_helpers.py -q` → FAIL(표시 주석 없음)
- [ ] **Step 3: 구현** — Interfaces대로.
- [ ] **Step 4: 통과 확인** — 같은 명령 PASS, 전체 `-q` PASS
- [ ] **Step 5: 커밋** — `음성 도우미: 문장 끊기, 읽을 글 정리, 목소리 고르기`

### Task 2: 답을 목소리로 읽기 (말하기 큐 + 음소거)

**Files:**
- Modify: `static/index.html` — `send()`, `greet()`, topbar(🔇 버튼), 새 `speaker` 객체
- Modify: `tests/test_app.py` — 화면 뼈대 검사 한 개

**Interfaces:**
- Consumes: `splitSentences`, `cleanForSpeech`, `pickVoice`, 기존 `character.gender`
- Produces: `speaker` — `feed(delta: string)`(완성 문장을 큐에 넣고 바로 읽기 시작), `end()`(남은 조각 읽기), `say(text: string)`(문장 단위로 큐), `stop()`(즉시 멈추고 큐 비우기), `muted: boolean`(localStorage `friend.muted`, 읽기·쓰기 try/catch), `setGender(gender: string)`. 말하는 동안 `#app`에 class `speaking`(🔇 버튼이 살짝 강조되는 정도의 CSS).
  - 🔇 버튼 `#muteBtn`(topbar `ai-badge` 옆): 누르면 `muted` 토글, 켜는 순간 `stop()`. 표시 🔊/🔇.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_app.py`에

```python
def test_home_has_voice_controls():
    html = client.get("/").text
    for id_ in ("micBtn", "muteBtn"):
        assert f'id="{id_}"' in html
```

  (`micBtn`은 이 Task에서 자리만 — 글자 입력창 `#form` 안, 보내기 버튼 왼쪽. 동작은 Task 3)
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
  - `send()` 시작 시 `speaker.stop()`; `delta`마다 `speaker.feed(e.text)`; `done`에서 `speaker.end()`; `error`면 그 문구를 `speaker.say`.
  - `greet()`의 인사도 `speaker.say(greeting.text)`. 브라우저 자동 재생 제한으로 사용자 클릭 전에는 소리가 안 날 수 있다 — 그때는 조용히 넘어간다(오류 없음).
  - 시작 시와 "캐릭터 설정" 저장 뒤 `speaker.setGender(character.gender)`.
  - `speechSynthesis.onvoiceschanged`에서 목소리를 다시 고른다. `speechSynthesis`가 없으면 `speaker`의 모든 메서드는 아무것도 하지 않고 🔇 버튼을 숨긴다.
- [ ] **Step 4: 통과 확인** — 전체 `-q` PASS, 모듈 스크립트 `node --check`
- [ ] **Step 5: 커밋** — `캐릭터 답을 목소리로 읽기`
- [ ] **Step 6: 수동 확인(사용자, 시연 PC 크롬)** — 글자로 "내일 날씨 어때?" → 답을 목소리로. 이모지를 읽지 않음. 답 도중 다음 메시지 → 앞 목소리 즉시 멈춤. 🔇 동작. 캐릭터 설정에서 성별 남성 → 목소리가 낮아짐.

### Task 3: 마이크로 말하기

**Files:**
- Modify: `static/index.html` — `#micBtn` 동작

**Interfaces:**
- Consumes: `send(text)`, `speaker.stop()`, `busy`
- Produces: `listen()` / `stopListening()`

- [ ] **Step 1: 구현**
  - `SpeechRecognition || webkitSpeechRecognition`이 없으면 🎤를 숨기고 끝(글자 입력은 그대로).
  - 🎤 누름: 말하는 중이면 `speaker.stop()` → `lang "ko-KR"`, `interimResults true`, `continuous false`로 시작 → 🎤에 class `listening`(빨간 점 깜빡임), 입력창 placeholder "듣는 중…".
  - 중간 결과는 입력창(`#input`)에 흐리게 보여 준다. 최종 결과가 있으면 입력창을 비우고 `send(text)`. 듣는 중 다시 누르면 `stopListening()`.
  - `onerror`: `not-allowed` → "마이크 권한을 허용해 주세요. 글자로도 대화할 수 있어요.", `no-speech` → "잘 못 들었어요. 다시 눌러 말해 주세요.", 그 밖 → "음성 인식이 잠시 안 돼요. 글자로 입력해 주세요." 를 입력창 위 안내 한 줄로 몇 초 보여 준다. 끝나면 🎤·placeholder 원래대로.
  - `busy`(답 받는 중)일 때 🎤는 누를 수 없게.
- [ ] **Step 2: 자동 테스트** — 전체 `-q` PASS, `node --check`
- [ ] **Step 3: 커밋** — `마이크로 말하기`
- [ ] **Step 4: 수동 확인(사용자, 시연 PC 크롬)** — 🎤 "내일 날씨 어때?" → 글자 표시 → 목소리 답. 말하는 중 🎤 → 즉시 멈추고 듣기. 마이크 차단 시 안내와 글자 입력 동작.

### Task 4: PR, 머지, 자동 배포로 확인 (과제 1)

- [ ] **Step 1: 완료 전 점검** — `git diff main --stat`에 `app/` 변경 없음, 테스트 삭제·skip 없음, `pytest -q` PASS.
- [ ] **Step 2: 푸시와 PR** — 사용자가 바뀐 줄 확인 후 머지. 배포 명령은 직접 치지 않는다.
- [ ] **Step 3: 14강 과제 1 확인 네 곳** — Actions deploy 성공 / Cloud Run 새 리비전 트래픽 100% / 접속 코드 입력 후 `/version`의 `commit` = 머지 커밋 / 서비스 URL 강력 새로고침(Ctrl+F5) 후 🎤·🔇 보임과 목소리 답.
