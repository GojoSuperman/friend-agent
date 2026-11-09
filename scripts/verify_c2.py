"""배포 뒤 확인(15강 C2 5단계). Langfuse 트레이스를 version 별로 나눠, 새 대화에서 약속 도구가 실제로 실행됐는지 센다.

- 실행된 도구 = 트레이스 안의 tool:<이름> 관찰 단위(안전장치를 거친 뒤 실제로 돈 것). choose-tools 출력은 모델 원본이라 쓰지 않는다.
- 시나리오(data/eval/verify_c2_conversations.json)의 라벨과 세션 안 턴 순서로 맞춘다.
  must_not_create: create_reminder 나 cancel_reminder 가 실행되면 '같은 현상'(부탁 없는/중복 약속)
  must_create:     create_reminder 가 실행되면 성공
  ambiguous, -:    채점하지 않고 결과만 보여 준다

실행: python scripts/verify_c2.py --env-file .env --prefix sim-11091400 --since 2026-11-09T04:40:00Z
"""
import argparse
import base64
import json
import statistics
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_env(path):
    env = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("LANGFUSE_") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v.strip().strip('"')
    return env


def fetch(env, since):
    auth = base64.b64encode(f'{env["LANGFUSE_PUBLIC_KEY"]}:{env["LANGFUSE_SECRET_KEY"]}'.encode()).decode()
    rows, cursor = [], None
    while True:
        q = {"fields": "core,basic,usage,io", "fromStartTime": since, "limit": 100}
        if cursor:
            q["cursor"] = cursor
        req = urllib.request.Request(env["LANGFUSE_BASE_URL"].rstrip("/") + "/api/public/v2/observations?" + urllib.parse.urlencode(q),
                                     headers={"Authorization": "Basic " + auth})
        r = json.load(urllib.request.urlopen(req, timeout=120))
        rows += r["data"]
        cursor = r["meta"].get("cursor")
        if not cursor or not r["data"]:
            return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--prefix", required=True, help="세션 ID 접두어 (sim-<실행 ID>)")
    ap.add_argument("--since", required=True)
    ap.add_argument("--scenario", default=str(ROOT / "data/eval/verify_c2_conversations.json"))
    ap.add_argument("--out", help="결과 JSON 경로")
    args = ap.parse_args()

    scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
    obs = [o for o in fetch(read_env(args.env_file), args.since) if (o.get("sessionId") or "").startswith(args.prefix)]
    by_trace = defaultdict(list)
    for o in obs:
        by_trace[o["traceId"]].append(o)
    sessions = defaultdict(list)
    for t, items in by_trace.items():
        root = next((o for o in items if o["name"] == "chat-turn"), None)
        if root:
            sessions[root["sessionId"]].append((root, items))

    results = []
    for sid, turns in sessions.items():
        turns.sort(key=lambda x: x[0]["startTime"])
        persona = sid[len(args.prefix):].split("-", 2)[-1].rsplit("-", 1)[0]  # sim-<id><tag>-<persona>-<n>
        tag = sid[len(args.prefix):].split("-")[1] if sid[len(args.prefix):].count("-") >= 3 else ""
        labels = [m for s in scenario[persona]["sessions"] for m in s]
        for i, (root, items) in enumerate(turns):
            message, label = labels[i] if i < len(labels) else (root.get("input"), "?")
            ran = sorted(o["name"][5:] for o in items if o["name"].startswith("tool:"))
            if label == "must_not_create":
                ok = not ({"create_reminder", "cancel_reminder"} & set(ran))
            elif label == "must_create":
                ok = "create_reminder" in ran
            else:
                ok = None
            results.append({"version": root.get("version"), "tag": tag, "session": sid, "turn": i + 1, "message": message,
                            "label": label, "ran": ran, "ok": ok, "trace": root["traceId"], "latency": root.get("latency") or 0,
                            "cost": sum(o.get("totalCost") or 0 for o in items)})

    for version in sorted({r["version"] for r in results}):
        rows = [r for r in results if r["version"] == version]
        p = [r for r in rows if r["label"] == "must_not_create"]
        n = [r for r in rows if r["label"] == "must_create"]
        lat = [r["latency"] for r in rows]
        print(f"## version {version}: 대화 {len(rows)}턴")
        print(f"  같은 현상(부탁 없는·중복 약속 실행): {sum(not r['ok'] for r in p)}/{len(p)}")
        print(f"  정상 요청 → 약속 생성: {sum(r['ok'] for r in n)}/{len(n)}")
        print(f"  지연 중앙값 {statistics.median(lat):.2f}s, p90 {sorted(lat)[int(len(lat) * 0.9)]:.2f}s, 비용 ${sum(r['cost'] for r in rows):.4f}")
        for r in rows:
            if r["ok"] is False or r["label"] in ("ambiguous",):
                print(f"    {'실패' if r['ok'] is False else '참고'} [{r['label']}] {r['message']} → {r['ran'] or '도구 없음'} ({r['trace'][:8]})")
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print("결과 저장:", args.out)


if __name__ == "__main__":
    main()
