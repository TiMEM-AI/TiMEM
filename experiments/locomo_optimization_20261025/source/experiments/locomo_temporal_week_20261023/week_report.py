"""Report for W0/W1 runs (screen subset or full 861)."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

RUN_ROOT = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023")
LABELS = {"2": "时间", "1": "多跳", "4": "单跳", "3": "推理"}
ARMS = ("b", "w0", "w1")


def mcnemar(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n))


def run_report(args) -> int:
    subset = args.subset
    rep = RUN_ROOT / "evaluate" / subset / f"rep{args.replicate}"
    score = json.loads((rep / "aml_score.json").read_text(encoding="utf-8"))
    results = json.loads((rep / "results.partial.json").read_text(encoding="utf-8"))["results"]
    questions = {q["question_id"]: q for q in json.loads((RUN_ROOT / "frozen/questions.json").read_text(encoding="utf-8"))["questions"]}
    offline = json.loads((RUN_ROOT / "offline/summary.json").read_text(encoding="utf-8"))
    done = [qid for qid in questions if all(f"{qid}:{arm}" in results for arm in ARMS)]

    lines = []
    title = "变化子集筛选" if subset == "changed" else "全量 861 题"
    lines.append(f"# W0/W1 周表达锚定：{title}结果")
    lines.append("")
    lines.append(f"- 完成题数：{len(done)}（incomplete={score.get('incomplete')}）")
    for arm in ARMS:
        slot = score["arms"][arm]
        lines.append(f"- {arm.upper()}：{slot['correct']}/{slot['total']} = {100*slot['correct']/max(1,slot['total']):.2f}%")
    lines.append("")
    lines.append("## 1. 分类别")
    lines.append("")
    lines.append("| 类别 | n | B | W0 | W1 |")
    lines.append("|---|---:|---:|---:|---:|")
    for cat in ("2", "1", "4", "3"):
        n = score["arms"]["b"]["by_category"].get(cat, {}).get("n", 0)
        if not n:
            continue
        cells = []
        for arm in ARMS:
            c = score["arms"][arm]["by_category"].get(cat, {}).get("correct", 0)
            cells.append(f"{c} ({100*c/n:.1f}%)")
        lines.append(f"| {LABELS[cat]} | {n} | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## 2. 预定比较")
    lines.append("")
    for name, cmp in score["comparisons"].items():
        b, c = cmp["wrong_to_right"], cmp["right_to_wrong"]
        lines.append(f"- **{name}**：错转对 {b}，对转错 {c}，净 {b-c:+d}，McNemar p={mcnemar(b, c):.4f}")
    lines.append("")

    lines.append("## 3. 判定口径")
    lines.append("")
    if subset == "changed":
        lines.append("这是**变化子集筛选**，不是全量成绩：只覆盖离线判定为输入发生变化的题，")
        lines.append("因此百分比不能与 861 题的历史分数直接比较，只能看三组之间的相对差值。")
    else:
        lines.append("这是固定版本上的全量 861 题对照；未改动的题三组输入逐字节相同并共享同一次采样。")
    lines.append("")
    lines.append(f"- 离线变更集：{len(offline['changed_union'])} 题（W0 {offline['changed_counts'].get('w0')} / W1 {offline['changed_counts'].get('w1')}）")
    lines.append(f"- 保护题改动：{offline['protected_changed']}（应为 0）")
    lines.append("")

    lines.append("## 4. 调用与缓存")
    lines.append("")
    counters = score.get("counters", {})
    lines.append(f"- 唯一答题请求 {counters.get('unique_answer_requests')}；判分请求 {counters.get('unique_judge_requests')}")
    lines.append(f"- 逻辑结果 {counters.get('logical_results')}（= 题数 × 3）")
    lines.append("- 缓存只在本次 replicate 内生效；未改动题的三组输入完全相同，因此共享同一答案。")
    lines.append("")

    report = RUN_ROOT / ("REPORT_SCREEN.md" if subset == "changed" else "REPORT_FULL.md")
    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:22]))
    print(f"\nwritten: {report}")
    return 0
