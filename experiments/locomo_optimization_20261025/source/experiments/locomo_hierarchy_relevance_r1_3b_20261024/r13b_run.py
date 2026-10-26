"""R1.3b runner: one screening call per planned task, validated and applied."""
from __future__ import annotations

import json
import sys
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R12 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024")
R13B = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_3b_20261024")
sys.path.insert(0, str(GROUNDED))
sys.path.insert(0, str(R12))
import grounded as g  # noqa: E402
from screen_client import ScreenClient  # noqa: E402

sys.path.insert(0, str(R13B))
from r13b_prepare import apply_plan, sha, validate_plan  # noqa: E402

PROMPT = """You audit and re-rank optional evidence windows for one question.

Question: {question}

Protected lower windows (must all stay):
{protected}

Tail windows (eligible for removal):
{tail}

Optional windows (each has a window_id and full facts):
{optional}

Rules:
- You may select up to 4 optional windows, and you must remove exactly as many tail windows.
- Never remove a protected window. Empty selection with removals is invalid.
- Flag constraint_mismatch when a window's time/role/state conflicts with the question.
Return ONLY JSON:
{{"selected_optional_window_ids":[],"removed_base_tail_ids":[],
 "evidence_decisions":[{{"window_id":"","relevance":"direct|bridge|context|unrelated|uncertain",
 "supporting_fact_ids":[],"query_relation":"","answer_value_or_needed_link":"","constraint_mismatch":false}}],
 "lost_query_support":[],"new_query_support":[]}}
"""


def main() -> int:
    facts = json.loads((GROUNDED / "store.json").read_text(encoding="utf-8"))["facts"]
    retriever = g.Retriever()
    tasks_path = R13B / "tasks.jsonl"
    tasks = [json.loads(l) for l in tasks_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    client = ScreenClient(R13B / "responses.jsonl")
    state_path = R13B / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"done": {}}

    for task in tasks:
        key = f"{task['qid']}:{task['arm']}"
        if task["status"] != "planned" or key in state["done"]:
            continue
        payload = json.loads((R13B / "requests" / f"{task['qid'].replace('#','_')}_{task['arm']}.json").read_text(encoding="utf-8"))
        prompt = PROMPT.format(
            question=payload["question"],
            protected=json.dumps(payload["protected"], ensure_ascii=False)[:9000],
            tail=json.dumps(payload["tail"], ensure_ascii=False)[:6000],
            optional=json.dumps(payload["optional"], ensure_ascii=False)[:12000],
        )
        record = client.screen_raw(prompt, payload["pool_ids"])
        entry = {"qid": task["qid"], "arm": task["arm"], "status": record["status"],
                 "prompt_sha": payload["prompt_sha"], "optional": payload["pool_ids"]}
        if record["status"] == "valid":
            plan = record["parsed"]
            errors = validate_plan(plan, 24, 20, payload["pool_ids"])
            if errors:
                entry.update({"fallback": "B", "errors": errors})
                state["done"][key] = entry
            else:
                base = [{"center": w["center_fact_label"], "fact_ids": [f["actual_fact_id"] for f in w["facts"]]}
                        for w in payload["protected"] + payload["tail"]]
                pool = [{"id": c["window_id"], "fact_id": c["facts"][0]["actual_fact_id"],
                         "text": c["facts"][0]["text"]} for c in payload["optional"]]
                chosen = apply_plan(base, plan, {"L": pool}, facts, "L")
                evidence, _ = retriever.render(chosen, payload["question"])
                (R13B / "evidence" / task["arm"]).mkdir(parents=True, exist_ok=True)
                (R13B / "evidence" / task["arm"] / f"{task['qid'].replace('#','_')}.json").write_text(
                    json.dumps({"question_id": task["qid"], "arm": task["arm"], "evidence": evidence,
                                "sha": sha(evidence), "selected": plan.get("selected_optional_window_ids"),
                                "removed": plan.get("removed_base_tail_ids"),
                                "windows": [{"center": w["center"], "fact_ids": w["fact_ids"]} for w in chosen]},
                               ensure_ascii=False, indent=2), encoding="utf-8")
                entry.update({"selected": plan.get("selected_optional_window_ids"),
                              "removed": plan.get("removed_base_tail_ids")})
                state["done"][key] = entry
        else:
            entry.update({"fallback": "B_after_retry"})
            state["done"][key] = entry
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{key}: {entry.get('status')} selected={entry.get('selected')}", flush=True)

    print(json.dumps({"done": len(state["done"]), "planned": sum(1 for t in tasks if t['status']=='planned')}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
