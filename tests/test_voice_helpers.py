"""음성 도우미(static/index.html의 표시 주석 사이 순수 블록)를 node로 검사."""
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parent.parent / "static" / "index.html"
START = "// ── 음성 도우미 시작 ──"
END = "// ── 음성 도우미 끝 ──"

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


def test_voice_helpers(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node 없음")
    html = INDEX.read_text(encoding="utf-8")
    assert START in html and END in html, "음성 도우미 표시 주석이 없음"
    block = html.split(START, 1)[1].split(END, 1)[0]
    f = tmp_path / "voice.js"
    f.write_text(block + "\n" + CASES, encoding="utf-8")
    r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"
