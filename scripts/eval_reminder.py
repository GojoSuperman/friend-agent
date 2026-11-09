"""약속 만들기 판단 평가(15강 C2). 도구 선택기(app.router.choose)만 돌려서 약속을 만들어야 할 때만 만드는지 센다.

- data/eval/reminder_intent.jsonl: must_not_create(문제 항목) / must_create(정상 항목)
- --regression: 기존 data/eval/tool_selection.jsonl 41개도 같은 choose() 로 돌려 도구 정답 수를 센다(다른 도구 선택이 망가지지 않았는지)
- 모델 온도가 기본값이라 결과가 흔들린다. --runs 번 반복해서 평균과 최저를 본다.

통과 조건(항목마다):
  must_not_create: 고른 도구에 create_reminder, cancel_reminder 가 없다 (list_reminders, 날씨 등은 상관없음)
  must_create:     고른 도구에 create_reminder 가 있다 (날짜·시각은 C1 범위라 여기서 채점하지 않는다)

실행: python scripts/eval_reminder.py --label before --runs 3 --regression   (.env 의 LLM 사용, 비용 발생)
결과: data/eval/results/reminder-<label>-<시각>.json
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.llm import make_llm  # noqa: E402
from app.router import choose  # noqa: E402

NOW = datetime.fromisoformat("2026-11-09T13:00:00+09:00")         # 데이터셋 문장의 '오늘' (월요일)
REGRESSION_NOW = datetime.fromisoformat("2026-10-12T14:00:00+09:00")  # tool_selection.jsonl 의 '오늘'
LOCATION = "서울"


def load(path):
    return [json.loads(line) for line in (ROOT / path).read_text(encoding="utf-8").splitlines() if line.strip()]


def judge_reminder(row, picked):
    if row["kind"] == "must_not_create":
        return not ({"create_reminder", "cancel_reminder"} & picked)
    return "create_reminder" in picked


def judge_regression(row, picked):
    required, optional = set(row["expected_tools"]), set(row["optional_tools"])
    return required <= picked <= required | optional


def run_once(rows, now, judge, llm, workers):
    def one(row):
        c = choose(row["message"], row["history"], now, LOCATION, llm)
        picked = {t["name"] for t in c.tools}
        return {"id": row["id"], "kind": row.get("kind", "regression"), "ok": judge(row, picked),
                "picked": sorted(picked), "action": c.action, "guards": c.guards}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, rows))


def rate(results, kind=None):
    rows = [r for r in results if kind is None or r["kind"] == kind]
    return sum(r["ok"] for r in rows), len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="before / after-v1 처럼 이번 실행의 이름")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--regression", action="store_true", help="tool_selection.jsonl 41개도 돌린다")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    llm = make_llm(load_settings())
    if not getattr(llm, "can_reason", False):
        sys.exit("LLM_PROVIDER=fake 에서는 평가할 수 없습니다")
    data = load("data/eval/reminder_intent.jsonl")
    regression = load("data/eval/tool_selection.jsonl") if args.regression else []
    report = {"label": args.label, "model": llm.model, "runs": []}
    for i in range(args.runs):
        res = run_once(data, NOW, judge_reminder, llm, args.workers)
        reg = run_once(regression, REGRESSION_NOW, judge_regression, llm, args.workers) if regression else []
        p, n, g = rate(res, "must_not_create"), rate(res, "must_create"), rate(reg)
        print(f"[{args.label} {i + 1}/{args.runs}] 문제 항목 {p[0]}/{p[1]}  정상 항목 {n[0]}/{n[1]}"
              + (f"  기존 도구 평가 {g[0]}/{g[1]}" if regression else ""))
        for r in res:
            if not r["ok"]:
                print(f"    실패 {r['id']} → {r['picked'] or '도구 없음'} ({r['action']})")
        report["runs"].append({"reminder": res, "regression": reg,
                               "must_not_create": p, "must_create": n, "regression_ok": g})
    pm = [r["must_not_create"][0] / r["must_not_create"][1] for r in report["runs"]]
    nm = [r["must_create"][0] / r["must_create"][1] for r in report["runs"]]
    report["summary"] = {"must_not_create_mean": sum(pm) / len(pm), "must_not_create_min": min(pm),
                         "must_create_mean": sum(nm) / len(nm), "must_create_min": min(nm)}
    if regression:
        gm = [r["regression_ok"][0] for r in report["runs"]]
        report["summary"]["regression_mean"] = sum(gm) / len(gm)
    s = report["summary"]
    print(f"\n요약[{args.label}] 문제 항목 평균 {s['must_not_create_mean']:.1%}(최저 {s['must_not_create_min']:.1%}), "
          f"정상 항목 평균 {s['must_create_mean']:.1%}(최저 {s['must_create_min']:.1%})"
          + (f", 기존 도구 평가 평균 {s['regression_mean']:.1f}/{len(regression)}" if regression else ""))
    out = ROOT / "data/eval/results"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"reminder-{args.label}-{datetime.now().strftime('%m%d%H%M')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"결과 저장: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
