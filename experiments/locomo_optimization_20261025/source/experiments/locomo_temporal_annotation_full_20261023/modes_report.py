"""Report mode: four-arm tables, preset comparisons, McNemar and clustered bootstrap."""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

RUN_ROOT = Path(r"F:\codex数据库memory\locomo_temporal_annotation_full_20261023")
LABELS = {"2": "时间", "1": "多跳", "4": "单跳", "3": "推理"}
ARMS = ("c0", "c1", "c2", "c3")
ARM_LABELS = {"c0": "C0 raw_v1", "c1": "C1 a0_frozen", "c2": "C2 spec_format", "c3": "C3 spec_safe_context"}


def written(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def clustered_bootstrap(pairs, clusters, draws: int = 2000, seed: int = 20261023) -> tuple:
    """pairs: list of (qid, delta); clusters: qid -> conversation id."""
    by_cluster = defaultdict(list)
    for qid, delta in pairs:
        by_cluster[clusters[qid]].append(delta)
    keys = sorted(by_cluster)
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        sample = [rng.choice(keys) for _ in keys]
        values = [d for key in sample for d in by_cluster[key]]
        if values:
            means.append(100.0 * sum(values) / len(values))
    means.sort()
    return (means[int(0.025 * len(means))], means[int(0.975 * len(means))]) if means else (None, None)


def run_report(args) -> int:
    root = Path(RUN_ROOT)
    config = json.loads((root / "experiment.json").read_text(encoding="utf-8"))
    rep_dir = root / "evaluate" / f"rep{args.replicate}"
    score = json.loads((rep_dir / "aml_score.json").read_text(encoding="utf-8"))
    partial = json.loads((rep_dir / "results.partial.json").read_text(encoding="utf-8"))
    questions = {q["question_id"]: q for q in json.loads((root / "frozen/questions.json").read_text(encoding="utf-8"))["questions"]}
    offline = json.loads((root / "offline/summary.json").read_text(encoding="utf-8"))
    requests = json.loads((rep_dir / "requests.json").read_text(encoding="utf-8"))

    results = partial["results"]
    done = [qid for qid in questions if all(f"{qid}:{arm}" in results for arm in ARMS)]
    clusters = {qid: questions[qid].get("sample_id") or qid.split("#")[0] for qid in questions}

    lines = []
    lines.append("# 时间粒度标注：全量 861 题四组成对结果")
    lines.append("")
    lines.append(f"生成时间：{datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append("## 1. 结论摘要")
    lines.append("")
    lines.append(f"- 完成题数：{len(done)}/861（incomplete={score.get('incomplete')}）")
    for arm in ARMS:
        slot = score["arms"][arm]
        c = slot["correct"]
        n = slot["total"] or 1
        lines.append(f"- {ARM_LABELS[arm]}：{c}/{n} = {100*c/n:.2f}%")
    main_cmp = score["comparisons"]["c3-c0"]
    lines.append(f"- 主比较 C3−C0：错转对 {main_cmp['wrong_to_right']}，对转错 {main_cmp['right_to_wrong']}，净 {main_cmp['wrong_to_right']-main_cmp['right_to_wrong']:+d}")
    lines.append("")

    lines.append("## 2. 分类别")
    lines.append("")
    header = "| 类别 | n | " + " | ".join(arm.upper() for arm in ARMS) + " | C3−C0 |"
    lines.append(header)
    lines.append("|---|" + "---:|" * (len(ARMS) + 2))
    for cat in ("2", "1", "4", "3"):
        n = score["arms"]["c0"]["by_category"].get(cat, {}).get("n", 0)
        if not n:
            continue
        cells = []
        for arm in ARMS:
            c = score["arms"][arm]["by_category"].get(cat, {}).get("correct", 0)
            cells.append(f"{c} ({100*c/n:.1f}%)")
        d = score["arms"]["c3"]["by_category"].get(cat, {}).get("correct", 0) - score["arms"]["c0"]["by_category"].get(cat, {}).get("correct", 0)
        lines.append(f"| {LABELS[cat]} | {n} | " + " | ".join(cells) + f" | {d:+d} |")
    total_cells = [f"{score['arms'][a]['correct']} ({100*score['arms'][a]['correct']/max(1,score['arms'][a]['total']):.1f}%)" for a in ARMS]
    lines.append(f"| 总计 | {len(done)} | " + " | ".join(total_cells) + " | — |")
    lines.append("")

    lines.append("## 3. 预定比较")
    lines.append("")
    for name, cmp in score["comparisons"].items():
        b, c = cmp["wrong_to_right"], cmp["right_to_wrong"]
        lines.append(f"- **{name}**：错转对 {b}，对转错 {c}，净 {b-c:+d}，McNemar 精确双侧 p={mcnemar_exact(b, c):.4f}")
    lines.append("")

    pairs = [(qid, int(results[f"{qid}:c3"]["correct"]) - int(results[f"{qid}:c0"]["correct"])) for qid in done]
    lo, hi = clustered_bootstrap(pairs, clusters)
    if lo is not None:
        lines.append(f"- C3−C0 按对话聚类 bootstrap 95% 区间（仅 10 段对话，区间不稳）：[{lo:+.2f}, {hi:+.2f}] pp")
        lines.append("")

    lines.append("## 4. 变更范围与保护")
    lines.append("")
    lines.append(f"- 离线审计通过：{offline.get('passed')}")
    lines.append(f"- 保护题（非时间且非两道已知单跳）：{offline.get('protected_questions')} 道，改动 {offline['failures'].get('protected_changed', 0)} 道")
    lines.append(f"- 各组变更题数：{offline.get('changed_counts')}")
    lines.append(f"- 精度/跨度/预算/跨用户失败：{ {k: v for k, v in offline['failures'].items() if k != 'protected_changed'} }")
    lines.append("")
    lines.append("两道已知受影响单跳（不按标签绕过）：")
    for qid in ("conv-47#q0088", "conv-48#q0080"):
        row = next((q for q in json.loads((root / "offline/input_diff.json").read_text(encoding="utf-8"))["questions"] if q["question_id"] == qid), None)
        if row:
            answers = {arm: results.get(f"{qid}:{arm}", {}).get("correct") for arm in ARMS}
            lines.append(f"- {qid}：changed={row['changed']}，判定={answers}")
    lines.append("")

    lines.append("## 5. 调用与缓存")
    lines.append("")
    lines.append(f"- 逻辑评测数：{requests.get('logical_evaluations')}")
    lines.append(f"- 唯一答题请求：{requests.get('answer_requests')}（命中缓存 {requests.get('answer_hits')}）")
    lines.append(f"- 唯一判分请求：{requests.get('judge_requests')}（命中缓存 {requests.get('judge_hits')}）")
    lines.append("")

    lines.append("## 6. 边界说明")
    lines.append("")
    lines.append("- 四组共享同一份原文快照、同一批 B0 候选、同一 V1 证据顺序；C2/C3 均从同一份未注释 V1 rows 重新构造。")
    lines.append("- 相同完整请求共享一次采样，因此未改动题的持平是受控输入共享的结果，不代表独立重跑方差。")
    lines.append("- 第一轮全量结果即最终结论，不为凑门槛反复挑选。")
    lines.append("")

    report = "\n".join(lines)
    written(root / "REPORT.md", report)
    print(report[:1800])
    return 0
