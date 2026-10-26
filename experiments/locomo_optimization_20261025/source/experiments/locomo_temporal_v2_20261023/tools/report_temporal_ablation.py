"""Render REPORT.md for the A0 temporal ablation from the run artifacts."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023")
FROZEN = Path(r"F:\codex数据库memory\locomo_smoke300_20261022")

LABELS = {"1": "多跳", "2": "时间", "3": "推理", "4": "单跳"}
ORDER = ["2", "1", "4", "3"]


def load(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def pct(correct: int, total: int) -> str:
    return f"{100.0 * correct / total:.2f}%" if total else "0.00%"


def main() -> int:
    fresh = load(OUT / "fresh/aml_score.json")
    offline = load(OUT / "offline/summary.json")
    preflight = load(OUT / "frozen/preflight.json")
    frozen_summary = load(FROZEN / "summary.json")
    if not fresh:
        raise SystemExit("fresh/aml_score.json missing")

    b0 = {m["category"]: m for m in frozen_summary["metrics"]}
    v1_hist = {m["category"]: m for m in frozen_summary["metrics"]}

    lines = []
    lines.append("# A0 时间证据注释：成对离线审计与当次模型评测")
    lines.append("")
    lines.append(f"生成时间：{fresh['provenance'].get('started_at', '')}")
    lines.append("")
    lines.append("## 1. 结论摘要")
    lines.append("")
    v1, v2 = fresh["v1"], fresh["v2"]
    lines.append(f"- 当次 V1：{v1['correct']}/{v1['total']} = {pct(v1['correct'], v1['total'])}")
    lines.append(f"- 当次 V2：{v2['correct']}/{v2['total']} = {pct(v2['correct'], v2['total'])}")
    lines.append(f"- 错转对 {fresh['wrong_to_right']} 题，对转错 {fresh['right_to_wrong']} 题，净变化 {fresh['net']:+d} 题")
    lines.append(f"- 输入保护：{'通过' if offline and offline.get('passed') else '未通过'}"
                 f"（非时间题改动 {offline.get('changed_non_temporal') if offline else '?'} 题）")
    lines.append("")

    lines.append("## 2. 分类别结果（当次模型，成对）")
    lines.append("")
    lines.append("| 类别 | 题数 | V1 | V2 | 差值(pp) | 历史 B0 |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    total_v1 = total_v2 = total_n = 0
    for cat in ORDER:
        a = v1["by_category"].get(cat)
        b = v2["by_category"].get(cat)
        if not a or not b:
            continue
        n = a["n"]
        total_v1 += a["correct"]; total_v2 += b["correct"]; total_n += n
        hist = b0.get(cat, {})
        delta = 100.0 * b["correct"] / n - 100.0 * a["correct"] / n
        lines.append(
            f"| {LABELS.get(cat, cat)} | {n} | {a['correct']}/{n} ({pct(a['correct'], n)}) "
            f"| {b['correct']}/{n} ({pct(b['correct'], n)}) | {delta:+.2f} "
            f"| {hist.get('baseline_correct', '?')}/{hist.get('n', '?')} |"
        )
    overall_delta = 100.0 * total_v2 / total_n - 100.0 * total_v1 / total_n
    lines.append(
        f"| 总计 | {total_n} | {total_v1}/{total_n} ({pct(total_v1, total_n)}) "
        f"| {total_v2}/{total_n} ({pct(total_v2, total_n)}) | {overall_delta:+.2f} | "
        f"{frozen_summary['metrics'][-1]['baseline_correct']}/300 |"
    )
    lines.append("")

    lines.append("## 3. 输入保护（离线，硬门槛）")
    lines.append("")
    if offline:
        lines.append(f"- 题目重放：{offline['questions']}")
        lines.append(f"- V1 重放失败：{len(offline['replay_failures'])}")
        lines.append(f"- 保护断言失败：{len(offline['protection_failures'])}")
        lines.append(f"- 预算/截断失败：{len(offline['budget_failures'])}")
        lines.append(f"- 时间题发生变更：{offline['changed_temporal']}")
        lines.append(f"- 非时间题发生变更：{offline['changed_non_temporal']}")
    lines.append("")

    lines.append("## 4. 当次条件")
    lines.append("")
    prov = fresh["provenance"]
    lines.append(f"- 答题模型 / 判分模型：{prov.get('answer_model')} / {prov.get('judge_model')}")
    lines.append(f"- 答案端点：{prov.get('answer_base')}")
    lines.append(f"- 判分端点：{prov.get('judge_base')}")
    lines.append(f"- 并发：{prov.get('concurrency')}")
    lines.append(f"- source_evidence sha256：`{prov.get('source_evidence_sha256')}`")
    lines.append(f"- temporal_evidence sha256：`{prov.get('temporal_evidence_sha256')}`")
    if preflight:
        lines.append(f"- 原文快照：{preflight['counts']['source_turns']} turns / {preflight['counts']['source_requests']} requests")
    lines.append("")

    lines.append("## 5. 逐题得失")
    lines.append("")
    lines.append("| qid | 类别 | V1 | V2 |")
    lines.append("|---|---|---|---|")
    rows = load(OUT / "fresh/aml_score.partial.json", {"results": []})["results"]
    changed = [r for r in rows if bool(r["v1"].get("correct")) != bool(r["v2"].get("correct"))]
    for r in sorted(changed, key=lambda x: (x["category"], x["question_id"])):
        mark_v1 = "对" if r["v1"].get("correct") else "错"
        mark_v2 = "对" if r["v2"].get("correct") else "错"
        lines.append(f"| {r['question_id']} | {LABELS.get(r['category'], r['category'])} | {mark_v1} | {mark_v2} |")
    lines.append("")
    lines.append(f"共 {len(changed)} 题发生变化。")
    lines.append("")

    lines.append("## 6. 边界说明")
    lines.append("")
    lines.append("- 历史 B0/V1 数字来自冻结 smoke（同一模型 deepseek-v4.1-flash），本轮两组均为当次新调用，未复用历史答案。")
    lines.append("- 时间类 100 题已参与规则设计，不是独立 holdout；本报告只说明本批题目的成对差异。")
    lines.append("- 非时间题输入逐字节一致（见 offline/input_diff.json），因此其分数变化只能来自模型波动。")
    lines.append("- A0 仅追加注释，不改写原文、不重选证据、不引入题库信息。")
    lines.append("")

    report = "\n".join(lines)
    (OUT / "REPORT.md").write_text(report, encoding="utf-8")
    print(report[:1500])
    print(f"\nwritten: {OUT / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
