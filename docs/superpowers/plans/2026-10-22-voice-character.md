# PR ② 음성 대화 + 움직이는 캐릭터 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 마이크로 말하면 캐릭터(하루/준호)가 목소리로 답하고, 2D 캐릭터가 듣기·생각·말하기에 맞춰 움직이는 화면을 만들어 PR 머지 → 자동 배포로 내보낸다(14강 과제 1).

**Architecture:** 서버는 바꾸지 않는다. `static/index.html` 한 파일 안에 ① DOM이 없는 순수 도우미 블록(문장 끊기, 읽을 글 정리, 목소리 고르기), ② 캐릭터 SVG와 상태 표시, ③ 말하기 큐(`speechSynthesis`), ④ 캐릭터 고르기 화면, ⑤ 마이크(`SpeechRecognition`)를 차례로 더한다. 서버가 `/` 외의 정적 파일을 내보내지 않으므로 파일을 나누지 않는다. 순수 도우미 블록은 표시 주석 사이에 두고 pytest가 node로 실행해 검사한다.

**Tech Stack:** HTML/CSS/JS(모듈 스크립트, 라이브러리 없음) · Web Speech API · pytest + node(도우미 검사)

**Spec:** `docs/superpowers/specs/2026-10-22-voice-character-design.md` 1~9절

## Global Constraints

- 바뀌는 파일은 `static/index.html`과 `tests/`뿐. `app/` 서버 코드는 바꾸지 않는다.
- 외부 이미지·폰트·라이브러리를 새로 들이지 않는다. 캐릭터는 SVG 코드로 그린다.
- 유료 API를 쓰지 않는다. 브라우저 내장 음성만 쓴다(`lang = "ko-KR"`).
- 목소리 설정: 하루(여성) `Google 한국의` pitch **1.1** rate **1.05** / 준호(남성) `Google 한국의` pitch **0.6** rate **0.95**. 남성 한국어 목소리가 있으면 준호는 그 목소리에 pitch **1.0**. 성별 "밝히지 않음"은 여성으로 보여 준다.
- 시연 PC 실측(Windows · Chrome 155): 한국어 목소리는 `Microsoft Heami - Korean (Korean)`, `Google 한국의` 두 개, 둘 다 여성.
- 글자 입력은 그대로 동작해야 한다(마이크 실패 시 비상용).
- 테스트 실행 명령: `.venv/bin/python -m pytest -q`
- 작업 브랜치 → PR → 머지. PR ①(`2026-10-22-visitor-llm-key.md`)이 먼저 머지된 main에서 시작한다.

## Review Focus

1. **스트리밍 중 문장이 조각나 들어옴**(`delta`가 "내일 서", "울은 맑대." 처럼 옴): 완성된 문장만 읽고, 끝에 남은 조각은 `done`/`error` 때 마저 읽는다. → Task 1 `splitSentences` 테스트.
2. **이모지·URL·마크다운 기호가 섞인 답**: 읽지 않는다("웃는 얼굴" 같은 낭독이 나오면 안 됨). 전부 이모지인 문장은 건너뛴다. → Task 1 `cleanForSpeech` 테스트.
3. **목소리 목록이 늦게 채워짐**(크롬은 `getVoices()`가 처음엔 빈 배열): `voiceschanged` 뒤에 다시 고른다. 한국어 목소리가 하나도 없으면 자막만 보여 주고 오류 없이 넘어간다. → Task 1 `pickVoice` 테스트 + Task 3 수동 확인.
4. **말하는 중에 새 메시지를 보내거나 🎤를 누름**: 이전 말하기를 즉시 멈추고 큐를 비운다(두 답이 겹쳐 들리면 안 됨). → Task 3·5 수동 확인.
5. **마이크 권한 거부 / 인식 결과 없음(`no-speech`)**: 안내 한 줄을 보여 주고 상태를 대기로 돌린다. 캐릭터가 "듣는 중"에 멈춰 있으면 안 된다. → Task 5 수동 확인.

---

### Task 1: 순수 음성 도우미와 node 검사

**Files:**
- Modify: `static/index.html` — 모듈 스크립트 맨 앞(상수 `SUGGEST` 위)에 `// ── 음성 도우미 시작 ──` 과 `// ── 음성 도우미 끝 ──` 표시 주석 사이 블록
- Create: `tests/test_voice_helpers.py`

**Interfaces:**
- Produces (블록 안, DOM·전역 의존 없음):
  - `CHARACTERS = [{id: "haru", name: "하루", gender: "여성"}, {id: "junho", name: "준호", gender: "남성"}]`
  - `VOICE_PRESETS = {"여성": {preferred: "Google 한국의", pitch: 1.1, rate: 1.05}, "남성": {preferred: "Google 한국의", pitch: 0.6, rate: 0.95, malePitch: 1.0}}`
  - `splitSentences(buffer: string) -> {done: string[], rest: string}` — `.` `?` `!` `…` `~` 와 줄바꿈 뒤에서 끊는다(부호 포함). 숫자 사이 마침표(`3.5`)는 끊지 않는다.
  - `cleanForSpeech(text: string) -> string` — 이모지(`\p{Extended_Pictographic}`, 변형 선택자, ZWJ), URL, `*` `#` `` ` `` `_` `>` 기호를 지우고 공백을 하나로.
  - `pickVoice(voices: {name: string, lang: string}[], gender: string) -> {voice: object|null, pitch: number, rate: number}` — `lang`이 `ko`로 시작하는 것만 본다. 남성이고 이름이 `/injoon|hyunsu|male|남성/i`에 맞는 목소리가 있으면 그 목소리 + `malePitch`. 아니면 `preferred` 이름 → 없으면 첫 한국어 목소리 → 없으면 `voice: null`. 성별이 `"남성"`이 아니면 여성 설정.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_voice_helpers.py`가 `static/index.html`에서 표시 주석 사이를 잘라 node로 실행한다. `shutil.which("node")`가 없으면 `pytest.skip`.

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

  테스트는 잘라 낸 블록 + `CASES`를 임시 파일(`tmp_path`)에 써서 `node`로 실행하고, 종료 코드 0과 출력 `ok`를 확인한다.

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/test_voice_helpers.py -q` / Expected: FAIL (표시 주석 없음)
- [ ] **Step 3: 구현** — 위 Interfaces대로 블록 작성.
- [ ] **Step 4: 통과 확인** — 같은 명령 PASS, 전체 `-q` PASS
- [ ] **Step 5: 커밋** — 브랜치 `voice-character`에서 `git add tests/test_voice_helpers.py static/index.html && git commit -m "음성 도우미: 문장 끊기, 읽을 글 정리, 목소리 고르기"`

### Task 2: 캐릭터 무대와 A/B 배치, 상태 움직임

**Files:**
- Modify: `static/index.html` — `#view-chat` 마크업, CSS, 스크립트
- Modify: `tests/test_app.py` — 화면 뼈대 검사 한 개

**Interfaces:**
- Consumes: `CHARACTERS`
- Produces:
  - `characterSvg(gender: string) -> string` — 여성/남성 두 그림(시안 `motion-style.html` A, `layout.html` 고르기 카드 참고). 입 요소 class `mouth`, 눈 class `eye`.
  - `setCharacterState(state: "idle"|"listening"|"thinking"|"speaking")` — `#stage`의 `data-state`를 바꾼다. 움직임은 CSS로: idle 숨쉬기+깜빡임, listening 고개 갸웃, thinking 점 세 개, speaking 입 뻐끔+몸 흔들림.
  - `setCaption(text: string, interim = false)` — 자막 `#caption`. interim이면 흐리게.
  - 대화 기록 토글 `#logToggle`: `#app`에 class `log-open`을 켜고 끈다. 상태는 localStorage `friend.logOpen`(읽기·쓰기 모두 try/catch).

- [ ] **Step 1: 실패하는 테스트**

```python
def test_home_has_voice_stage():
    html = client.get("/").text
    for id_ in ("stage", "caption", "logToggle", "micBtn", "muteBtn"):
        assert f'id="{id_}"' in html
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/test_app.py -q` / Expected: FAIL
- [ ] **Step 3: 구현**
  - `#view-chat` 안에 `#stage`(캐릭터 SVG, `#caption`, 🎤 `#micBtn`, 🔇 `#muteBtn`)를 `.chat` 앞에 둔다. 마이크·음소거 버튼은 이 Task에서는 자리만(동작은 Task 3·5).
  - 기본(A): `.chat`(대화 기록)을 숨기고 무대가 가운데를 채운다. `log-open`(B): 무대 왼쪽, `.chat` 오른쪽 2열. 폭 720px 이하에서는 B도 위아래로 쌓는다.
  - 기존 말풍선 추가(`addRow`)는 그대로 둔다. 어시스턴트 답은 말풍선과 함께 `setCaption`에도 보낸다.
  - 대화 화면 캐릭터 자리는 `characterSvg(character.gender)`. 메뉴의 작은 이모지 아바타는 그대로 둔다.
- [ ] **Step 4: 통과 확인** — 전체 `-q` PASS
- [ ] **Step 5: 수동 확인** — 로컬 실행(`uvicorn app.main:app --port 8080`, `.env`의 `LLM_PROVIDER=fake`로 비용 없음) 후 크롬에서: 글자로 "안녕" → 생각 중 점 → 답 자막, 대화 기록 버튼으로 A↔B 전환, 새로고침 후 토글 상태 유지, 휴대폰 폭(개발자 도구)에서 가로 스크롤 없음. 스크린샷을 남겨 사용자에게 보여 준다.
- [ ] **Step 6: 커밋** — `git commit -am "캐릭터 무대와 대화 기록 펼치기, 상태 움직임"`

### Task 3: 답을 목소리로 읽기 (말하기 큐)

**Files:**
- Modify: `static/index.html` — `send()`, `greet()`, 새 `speaker` 객체

**Interfaces:**
- Consumes: `splitSentences`, `cleanForSpeech`, `pickVoice`, `setCharacterState`
- Produces: `speaker` — `speaker.feed(delta: string)`(완성 문장을 큐에 넣고 바로 읽기 시작), `speaker.end()`(남은 조각 읽기), `speaker.say(text: string)`(통째로 문장 단위 큐), `speaker.stop()`(즉시 멈추고 큐 비우기), `speaker.muted: boolean`(localStorage `friend.muted`), `speaker.unlocked: boolean`(첫 사용자 클릭 뒤 true), `speaker.setGender(gender)`.
  - 상태 연결: 첫 문장 `onstart` → `speaking`, 큐가 비고 마지막 `onend` → `idle`. 음소거 중이거나 목소리가 없으면 말하지 않고 상태만 `idle`.

- [ ] **Step 1: 구현**
  - `send()` 시작 시 `speaker.stop()` 후 `setCharacterState("thinking")`; `delta`마다 `speaker.feed(e.text)`; `done`/`error`에서 `speaker.end()` (error면 오류 문구를 `say`).
  - `greet()`의 인사도 `speaker.say(greeting.text)`. `speaker.unlocked`가 false면 자막만.
  - `speechSynthesis.onvoiceschanged`에서 목소리를 다시 고른다(Review Focus 3).
  - 🔇 버튼: `speaker.muted` 토글, 켜는 순간 `speaker.stop()`.
- [ ] **Step 2: 자동 테스트** — 전체 `-q` PASS (도우미 검사 포함)
- [ ] **Step 3: 시연 PC 목소리 확인 (스펙 5.4)** — 사용자에게 로컬 주소를 시연 PC 크롬으로 열어 달라고 요청. 하루·준호로 각각 "내일 날씨 어때?"를 글자로 보내 듣고, **두 목소리가 귀로 구분되는지** 사용자가 판정한다. 구분이 안 되면 준호를 `Microsoft Heami`로 바꾸는 안을 제시하고 사용자가 고른 값으로 `VOICE_PRESETS`와 Task 1 테스트를 함께 고친다.
- [ ] **Step 4: 수동 확인** — 답이 끝나기 전에 다음 메시지 보내기 → 앞 목소리 즉시 멈춤(Review Focus 4). 이모지 섞인 답에서 이모지를 읽지 않음. 음소거 동작.
- [ ] **Step 5: 커밋** — `git commit -am "캐릭터 답을 목소리로 읽기"`

### Task 4: 캐릭터 고르기 화면

**Files:**
- Modify: `static/index.html` — 새 `#picker` 화면, 시작 코드
- Modify: `tests/test_app.py` — `test_home_has_voice_stage`의 id 목록에 `picker` 추가

**Interfaces:**
- Consumes: `CHARACTERS`, `characterSvg`, `speaker`, 기존 `api()`·`character`·`paintCharacter()`·`greet()`
- Produces: `showPicker()`, `choose(id: "haru"|"junho")`

- [ ] **Step 1: 테스트 수정 후 실패 확인** — `"picker"` 추가 → FAIL
- [ ] **Step 2: 구현**
  - 접속할 때마다 시작 코드는 `me`·`character`를 읽은 뒤 `showPicker()`로 멈춘다(기존 "캐릭터 만들기" 설정 폼으로 가던 분기를 대체). 현재 캐릭터의 성별과 같은 카드를 선택 표시.
  - 카드: 일러스트, 이름·성별, "▶ 목소리 듣기"(그 캐릭터 목소리로 짧은 자기소개, 이 클릭도 `speaker.unlocked = true`).
  - `choose(id)`: `speaker.unlocked = true` → 현재 `character` 전체를 복사해 `name`·`gender`만 바꿔 `PUT /api/character`(빠진 필드는 서버가 기본값으로 덮으므로 **반드시 전체 전송**) → `paintCharacter()`, `speaker.setGender()`, 무대 그림 교체 → `loadHistory()` → `greet()`.
  - "캐릭터 설정" 화면에서 성별을 바꿔 저장하면 무대 그림과 목소리도 따라 바뀐다.
  - 기존 `/api/character` 요청 형식은 지금 `saveBtn` 코드가 보내는 형식을 그대로 따른다.
- [ ] **Step 3: 통과 확인** — 전체 `-q` PASS
- [ ] **Step 4: 수동 확인** — 이름을 "지수"로 바꿔 둔 뒤 준호를 고르면 이름이 준호로, 성격·말투는 유지되는지 "캐릭터 설정"에서 확인. 고른 직후 인사가 소리로 나오는지.
- [ ] **Step 5: 커밋** — `git commit -am "접속할 때 캐릭터 고르기 화면"`

### Task 5: 마이크로 말하기

**Files:**
- Modify: `static/index.html` — `#micBtn` 동작

**Interfaces:**
- Consumes: `send(text)`, `speaker.stop()`, `setCharacterState`, `setCaption`
- Produces: `listen()` / `stopListening()`

- [ ] **Step 1: 구현**
  - `SpeechRecognition || webkitSpeechRecognition`이 없으면 🎤를 숨기고 끝.
  - 🎤 누름: 말하는 중이면 `speaker.stop()`(끼어들기) → `lang "ko-KR"`, `interimResults true`, `continuous false`로 시작 → `listening`. 중간 결과는 `setCaption(text, true)`.
  - 최종 결과가 있으면 `send(text)`. 듣는 중 다시 누르면 `stopListening()`.
  - `onerror`: `not-allowed` → "마이크 권한을 허용해 주세요. 글자로도 대화할 수 있어요.", `no-speech` → "잘 못 들었어요. 다시 눌러 말해 주세요.", 그 밖 → "음성 인식이 잠시 안 돼요. 글자로 입력해 주세요." 를 자막에 보여 주고 `idle`(Review Focus 5).
  - `busy`(답 받는 중)일 때 🎤는 눌리지 않게.
- [ ] **Step 2: 자동 테스트** — 전체 `-q` PASS
- [ ] **Step 3: 수동 확인 (시연 PC 크롬)** — 🎤 "내일 날씨 어때?" → 글자 표시 → 생각 중 → 목소리 답. 말하는 중 🎤 → 즉시 멈추고 듣기. 사이트 설정에서 마이크 차단 → 안내 문구와 글자 입력 동작.
- [ ] **Step 4: 커밋** — `git commit -am "마이크로 말하기"`

### Task 6: PR, 머지, 자동 배포로 확인 (과제 1)

**Files:** 없음

- [ ] **Step 1: 완료 전 점검** — `git diff main --stat`에 `app/` 변경이 없는지, 테스트 건너뛰기·삭제가 없는지 확인. `.venv/bin/python -m pytest -q` PASS.
- [ ] **Step 2: 푸시와 PR** — `git push -u origin voice-character` → `gh pr create --base main --title "음성 대화와 움직이는 캐릭터" --body "설계: docs/superpowers/specs/2026-10-22-voice-character-design.md"`. 사용자가 바뀐 줄을 확인한 뒤 머지(`gh pr merge --merge --delete-branch`). 배포 명령은 직접 치지 않는다.
- [ ] **Step 3: 14강 과제 1 확인 네 곳** — Actions deploy 성공 / Cloud Run 새 리비전 트래픽 100% / 접속 코드 입력 후 `<서비스 URL>/version`의 `commit` = 머지 커밋 SHA / 서비스 URL 강력 새로고침(Ctrl+F5) 후 기능이 보임.
- [ ] **Step 4: PR ① 잔여 확인** — 이 배포 뒤에도 `/access`가 잠금 화면인지(PR ① 계획 Task 6 Step 4).
- [ ] **Step 5: 스펙 8절 성공 기준 1~7을 시연 PC 크롬에서 차례로 확인**하고, 결과(통과/실패와 화면)를 사용자에게 보고한다.
