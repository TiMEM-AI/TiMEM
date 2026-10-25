"""Full 861 screening runner: resume, budget, validation, atomic apply, explicit evidence."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R12 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024")
R13B = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_3b_20261024")
FULL = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_full_20261025")
sys.path.insert(0, str(GROUNDED))
sys.path.insert(0, str(R12))
sys.path.insert(0, str(R13B))
import grounded as g  # noqa: E402
from screen_client import ScreenClient  # noqa: E402
from r13b_fix import apply_plan_fixed  # noqa: E402
from r13b_prepare import PROTECTED, sha  # noqa: E402

BUDGET_TOKENS = 24000
MAX_TRIES = 2


def validate(plan, tail_ids, pool_ids, protected):
    errors = []
    sel = plan.get("selected_optional_window_ids")
    rem = plan.get("removed_base_tail_ids")
    if not isinstance(sel, list) or not isinstance(rem, list):
        return ["bad_shape"]
    if len(set(sel)) != len(sel) or len(set(rem)) != len(rem):
        errors.append("duplicate_ids")
    if not set(sel) <= set(pool_ids):
        errors.append("unknown_optional_id")
    if not set(rem) <= set(tail_ids):
        errors.append("protected_or_unknown_removal")
    if len(sel) > 4 or len(rem) > 4:
        errors.append("over_limit")
    if len(sel) == 0 and len(rem) > 0:
        errors.append("pure_deletion")
    if len(sel) != len(rem) and sel:
        errors.append("swap_count_mismatch")
    selected = set(sel)
    for d in plan.get("evidence_decisions") or []:
        if isinstance(d, dict) and d.get("window_id") in selected and d.get("constraint_mismatch") is True:
            errors.append("selected_constraint_mismatch")
            break
    return errors


def shrink(payload, budget_tokens=BUDGET_TOKENS):
    """Drop whole optional windows until the prompt fits; never truncate text."""
    optional = list(payload["optional"])
    while optional and len(json.dumps(payload["protected"], ensure_ascii=False)) + \
            len(json.dumps(payload["tail"], ensure_ascii=False)) + \
            len(json.dumps(optional, ensure_ascii=False)) + len(payload["question"]) > budget_tokens * 4:
        optional.pop()
    return optional


def main() -> int:
    facts = json.loads((GROUNDED / "store.json").read_text(encoding="utf-8"))["facts"]
    retriever = g.Retriever()
    tasks = [json.loads(l) for l in (FULL / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    state_path = FULL / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"done": {}}
    client = ScreenClient(FULL / "responses.jsonl")
    counters = Counter()
    # guard_skip tasks get explicit evidence equal to B
    for task in tasks:
        if task["status"] != "guard_skip":
            continue
        for arm in (task["arm"],):
            src = FULL / "evidence" / "B" / f"{task['qid'].replace('#','_')}.json"
            dst = FULL / "evidence" / arm / f"{task['qid'].replace('#','_')}.json"
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                data = json.loads(src.read_text(encoding="utf-8"))
                data["arm"] = arm
                dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    import os
    LIMIT = int(os.getenv("FULL_LIMIT", "0"))
    processed = 0

    for task in tasks:
        if task["status"] != "planned":
            continue
        key = f"{task['qid']}:{task['arm']}"
        if key in state["done"]:
            continue
        payload = json.loads((FULL / "requests" / f"{task['qid'].replace('#','_')}_{task['arm']}.json").read_text(encoding="utf-8"))
        optional = shrink(payload)
        dropped = len(payload["optional"]) - len(optional)
        ids = [c["window_id"] for c in optional]
        prompt = payload["prompt"] if dropped == 0 else None
        if prompt is None:
            from full_prepare import build_prompt  # type: ignore
            prompt = build_prompt({**payload, "optional": optional})
        record = None
        for attempt in range(MAX_TRIES):
            record = client.screen_raw(prompt, ids)
            if record["status"] == "valid":
                break
        entry = {"qid": task["qid"], "arm": task["arm"], "status": record["status"],
                 "dropped_optional": dropped, "attempts": attempt + 1,
                 "prompt_sha": sha(prompt), "usage": record.get("usage")}
        plan, errors = (record.get("parsed"), []) if record["status"] == "valid" else ({}, ["technical_failure"])
        if not errors:
            base = ([{"label": f"p{i+1}", "center": w["center_fact_label"],
                      "fact_ids": [f["actual_fact_id"] for f in w["facts"]]}
                     for i, w in enumerate(payload["protected"])]
                    + [{"label": f"t{i+1}", "center": w["center_fact_label"],
                        "fact_ids": [f["actual_fact_id"] for f in w["facts"]]}
                       for i, w in enumerate(payload["tail"])])
            tail_ids = {f"t{i+1}" for i in range(len(payload["tail"]))}
            errors = validate(plan, tail_ids, ids, protected=PROTECTED)
            if not errors:
                pool = {c["window_id"]: {"fact_id": c["facts"][0]["actual_fact_id"]} for c in optional}
                keep, apply_errors = apply_plan_fixed(base, plan, pool, facts)
                errors = apply_errors
                if not errors:
                    evidence, _ = retriever.render(
                        [{"center": w["center"], "fact_ids": w["fact_ids"]} for w in keep], payload["question"])
                    out = FULL / "evidence" / task["arm"]
                    out.mkdir(parents=True, exist_ok=True)
                    (out / f"{task['qid'].replace('#','_')}.json").write_text(json.dumps({
                        "question_id": task["qid"], "arm": task["arm"], "evidence": evidence,
                        "sha": sha(evidence), "selected": plan.get("selected_optional_window_ids"),
                        "removed": plan.get("removed_base_tail_ids"),
                        "windows": [{"center": w["center"], "fact_ids": w["fact_ids"]} for w in keep]},
                        ensure_ascii=False, indent=2), encoding="utf-8")
                    entry.update({"selected": plan.get("selected_optional_window_ids"),
                                  "removed": plan.get("removed_base_tail_ids"), "fallback_B": False})
        if "selected" not in entry:
            entry.update({"fallback_B": True, "errors": errors or record["status"]})
        state["done"][key] = entry
        counters[entry.get("status", "?")] += 1
        counters["fallback" if entry["fallback_B"] else "changed"] += 1
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        processed += 1
        print(f"{key} {entry['status']} fallback={entry['fallback_B']} sel={entry.get('selected')}", flush=True)
        if LIMIT and processed >= LIMIT:
            break

    print(json.dumps({"done": len(state["done"]), **dict(counters)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
