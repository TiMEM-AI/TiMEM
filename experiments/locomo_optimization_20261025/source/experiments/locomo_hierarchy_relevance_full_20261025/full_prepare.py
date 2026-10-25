"""Full 861 prepare: V2 pools, complete JSON prompts, new protocol dir."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
R13B = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_3b_20261024")
FULL = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_full_20261025")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
sys.path.insert(0, str(GROUNDED))
sys.path.insert(0, str(R13B))
import grounded as g  # noqa: E402
from r13b_prepare import (H_LOW, H_UP, L_SLOTS, MAX_WINDOWS, PROTECTED,  # noqa: E402
                          build_pools, serialize_window, sha)

PROMPT_TEMPLATE = """You audit and re-rank optional evidence windows for one question.

Question: {question}

Protected lower windows (must all stay):
{protected}

Tail windows (eligible for removal):
{tail}

Optional windows (each has a window_id and full facts):
{optional}

Rules:
- Select up to 4 optional windows; remove exactly as many tail windows.
- Never remove a protected window; empty selection with removals is invalid.
- Mark constraint_mismatch only for windows whose time/role/state conflicts with the question.
Return ONLY JSON:
{{"selected_optional_window_ids":[],"removed_base_tail_ids":[],
 "evidence_decisions":[{{"window_id":"","relevance":"direct|bridge|context|unrelated|uncertain",
 "supporting_fact_ids":[],"query_relation":"","answer_value_or_needed_link":"","constraint_mismatch":false}}],
 "lost_query_support":[],"new_query_support":[]}}
"""


def build_prompt(payload: dict) -> str:
    return PROMPT_TEMPLATE.format(
        question=payload["question"],
        protected=json.dumps(payload["protected"], ensure_ascii=False),
        tail=json.dumps(payload["tail"], ensure_ascii=False),
        optional=json.dumps(payload["optional"], ensure_ascii=False))


def main() -> int:
    FULL.mkdir(parents=True, exist_ok=True)
    (FULL / "requests").mkdir(exist_ok=True)
    (FULL / "evidence").mkdir(exist_ok=True)
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    folded = json.loads((R1 / "routes_r1.json").read_text(encoding="utf-8"))["routes"]
    facts = json.loads((GROUNDED / "store.json").read_text(encoding="utf-8"))["facts"]
    retriever = g.Retriever()

    tasks, stats = [], Counter()
    for qid, q in questions.items():
        conv, query = q["sample_id"], q["question"]
        base, _ = retriever.windows(conv, query, all_levels=False)
        evidence, _ = retriever.render(base, query)
        guard = not g.compositional_query(query)
        (FULL / "evidence" / "B").mkdir(parents=True, exist_ok=True)
        (FULL / "evidence" / "B" / f"{qid.replace('#','_')}.json").write_text(json.dumps({
            "question_id": qid, "evidence": evidence, "sha": sha(evidence),
            "windows": [{"center": w["center"], "fact_ids": w["fact_ids"]} for w in base]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        L, H, lower, upper = build_pools(conv, query, base, facts, folded, retriever)
        for arm, pool in (("L", L), ("H", H)):
            serialized = [{"window_id": f"c{i+1}", "kind": "optional",
                           "observed_at": item["observed_at"], "center_fact_label": f"c{i+1}",
                           "facts": [{"fact_label": "f1", "actual_fact_id": item["id"], "text": item["text"]}]}
                          for i, item in enumerate(pool)]
            payload = {"question_id": qid, "arm": arm, "question": query, "guard_skip": guard,
                       "protected": [serialize_window({**w, "label": f"p{i+1}", "kind": "protected"}, facts)
                                     for i, w in enumerate(base[:PROTECTED])],
                       "tail": [serialize_window({**w, "label": f"t{i+1}", "kind": "tail"}, facts)
                                for i, w in enumerate(base[PROTECTED:])],
                       "optional": serialized,
                       "pool_ids": [f"c{i+1}" for i in range(len(pool))],
                       "upper_ids": [f"c{i+1}" for i, item in enumerate(pool) if item.get("levels")]}
            payload["prompt"] = build_prompt(payload)
            payload["prompt_sha"] = sha(payload["prompt"])
            payload["prompt_chars"] = len(payload["prompt"])
            (FULL / "requests" / f"{qid.replace('#','_')}_{arm}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            status = "guard_skip" if guard else "planned"
            tasks.append({"qid": qid, "arm": arm, "status": status,
                          "prompt_sha": payload["prompt_sha"], "chars": payload["prompt_chars"],
                          "optional": len(pool), "upper": len(payload["upper_ids"])})
            stats[f"{arm}_{status}"] += 1
        stats["questions"] += 1
    (FULL / "tasks.jsonl").write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in tasks), encoding="utf-8")
    manifest = {"schema": "r1.full-manifest-v1", "questions": len(questions), "tasks": dict(stats),
                "protocol": {"max_windows": MAX_WINDOWS, "protected": PROTECTED, "L_slots": L_SLOTS,
                             "H_low": H_LOW, "H_up": H_UP, "budget_tokens": 24000,
                             "max_retries": 2, "model": "deepseek-v4.1-flash"},
                "code_hashes": {str(p): sha(p.read_text(encoding="utf-8")) for p in
                                [R13B / "r13b_prepare.py", R13B / "r13b_fix.py", Path(__file__)]},
                "prompt_template_sha": sha(PROMPT_TEMPLATE)}
    (FULL / "RUN_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(stats), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
