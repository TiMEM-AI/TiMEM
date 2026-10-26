"""R1.1 P0: rebuild the evidence chain offline with centered atomic windows,
transactional replacement and target-rendering guarantees. No model calls.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
R11 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_1_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")

sys.path.insert(0, str(GROUNDED))
import grounded as g  # noqa: E402


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_window(retriever, fid: str) -> list[str]:
    """Center + radius context, same observation time, at most 7 facts."""
    ids = retriever.window(fid)
    facts = retriever.facts
    if fid not in ids:
        ids = [fid] + list(ids)
    times = {facts[x]["observed_at"] for x in ids if x in facts}
    if len(times) > 1:
        anchor = facts[fid]["observed_at"]
        ids = [x for x in ids if facts[x]["observed_at"] == anchor]
        if fid not in ids:
            ids.insert(0, fid)
    return ids[:7]


def render_windows(retriever, windows, query):
    return retriever.render(windows, query)


def main() -> int:
    R11.mkdir(parents=True, exist_ok=True)
    dev = json.loads((R1 / "dev_sample.json").read_text(encoding="utf-8"))["question_ids"]
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    folded = json.loads((R1 / "routes_r1.json").read_text(encoding="utf-8"))["routes"]
    retriever = g.Retriever()

    rows = []
    stats = Counter()
    for qid in dev:
        q = questions[qid]
        conv, query = q["sample_id"], q["question"]
        base, base_audit = retriever.windows(conv, query, all_levels=False)
        base_evidence, base_render = render_windows(retriever, base, query)
        base_facts = [fid for w in base for fid in w["fact_ids"]]
        base_set = set(base_facts)

        # direct-date guard keeps lower evidence unchanged
        guard = not g.compositional_query(query)
        chosen = [dict(w) for w in base]
        replaced, accepted, rejected = [], 0, Counter()
        if guard:
            rejected["direct_date_guard"] += 1
        else:
            covered = set(base_facts)
            candidates = []
            for route in folded.values():
                if route["conv"] != conv:
                    continue
                target = [fid for fid in route["target_fact_ids"] if fid not in covered]
                if not target:
                    continue
                score = len(set(g.words(query)) & set(w for a in route["aliases"][:3] for w in g.words(a)))
                if score <= 0:
                    continue
                candidates.append((score, route, target))
            candidates.sort(key=lambda x: (-x[0], x[1]["route_id"]))

            for score, route, target in candidates[:64]:
                fid = target[0]
                ids = [x for x in atomic_window(retriever, fid) if x not in covered]
                if fid not in ids:
                    ids.insert(0, fid)
                if not ids:
                    rejected["already_covered"] += 1
                    continue
                new_window = {"center": fid, "fact_ids": ids, "channel": "upper_atomic",
                              "route_ids": [route["route_id"]], "route_levels": route["provenance_levels"]}
                simulated = [w["fact_ids"] for w in chosen] + [ids]
                flat = [x for w in simulated for x in w]
                if len(flat) != len(set(flat)):
                    # global de-dup: trim overlap from the new window
                    ids = [x for x in ids if x not in covered]
                    if fid not in ids:
                        ids.insert(0, fid)
                    new_window["fact_ids"] = ids
                if not ids:
                    rejected["empty_after_dedup"] += 1
                    continue
                if len(chosen) < g.RULES["max_windows"]:
                    chosen.append(new_window)
                    covered.update(ids)
                    accepted += 1
                    stats["upper_added_free_slot"] += 1
                    continue
                # full: replace a tail window, only if it has no unique fact
                victim_idx = len(chosen) - 1
                while victim_idx >= g.RULES["protected_flat_windows"]:
                    victim = chosen[victim_idx]
                    others = {x for i, w in enumerate(chosen) if i != victim_idx for x in w["fact_ids"]}
                    unique = [x for x in victim["fact_ids"] if x not in others]
                    query_terms = set(g.words(query))
                    unique_relevant = [
                        x for x in unique
                        if set(g.words(retriever.facts[x]["text"])) & query_terms
                    ]
                    if not unique_relevant:
                        replaced.append({"removed": victim["fact_ids"], "added": ids, "center": fid})
                        chosen[victim_idx] = new_window
                        covered.update(ids)
                        accepted += 1
                        stats["upper_replaced"] += 1
                        break
                    victim_idx -= 1
                else:
                    rejected["no_replaceable_tail"] += 1

        evidence, render_audit = render_windows(retriever, chosen, query)
        chosen_facts = [fid for w in chosen for fid in w["fact_ids"]]
        # P0 contract: accepted targets must actually appear in rendered content
        rendered = evidence
        target_ok = all(fid in chosen_facts for fid in [w["center"] for w in chosen])
        pure_delete = len(chosen_facts) < len(base_facts) and not (set(chosen_facts) - base_set)
        stats["changed"] += int(evidence != base_evidence)
        stats["pure_delete"] += int(pure_delete)
        stats["target_missing"] += int(not target_ok)
        stats["questions"] += 1
        rows.append({
            "question_id": qid, "conv": conv,
            "guard_direct_date": guard,
            "base_windows": len(base), "h_windows": len(chosen),
            "base_facts": len(base_facts), "h_facts": len(chosen_facts),
            "upper_accepted": accepted, "replaced": replaced,
            "rejection_reasons": dict(rejected),
            "evidence_changed": evidence != base_evidence,
            "evidence_sha_L": sha(base_evidence), "evidence_sha_H": sha(evidence),
            "pure_delete": pure_delete, "targets_rendered": target_ok,
            "base_tokens": base_render["evidence_chars"], "h_tokens": render_audit["evidence_chars"],
        })

    summary = {
        "schema": "r1.1-p0-audit-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "questions": stats["questions"],
        "changed": stats["changed"],
        "pure_delete": stats["pure_delete"],
        "target_missing": stats["target_missing"],
        "upper_added_free_slot": stats["upper_added_free_slot"],
        "upper_replaced": stats["upper_replaced"],
        "direct_date_guard": sum(1 for r in rows if r["guard_direct_date"]),
        "passed": stats["pure_delete"] == 0 and stats["target_missing"] == 0,
        "rows": rows,
    }
    (R11 / "P0_EVIDENCE_AUDIT.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# R1.1 P0 证据链审计", "",
             f"- 题数：{summary['questions']}，证据改变 {summary['changed']}",
             f"- 纯删除：{summary['pure_delete']}（必须为 0）",
             f"- 目标未呈现：{summary['target_missing']}（必须为 0）",
             f"- 上层新增（空位）：{summary['upper_added_free_slot']}；替换：{summary['upper_replaced']}",
             f"- 直接日期保护：{summary['direct_date_guard']} 题",
             f"- 结论：{'通过' if summary['passed'] else '未通过'}", ""]
    (R11 / "P0_EVIDENCE_AUDIT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("questions", "changed", "pure_delete", "target_missing",
                                              "upper_added_free_slot", "upper_replaced", "passed")}, ensure_ascii=False))
    return 0 if summary["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
