"""R1 step 3: structural audit. No model calls, no score dependency."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")


def main() -> int:
    manifest = json.loads((R1 / "manifest.json").read_text(encoding="utf-8"))
    routes = json.loads((R1 / "routes_r1.json").read_text(encoding="utf-8"))
    events = json.loads((R1 / "events_r1.json").read_text(encoding="utf-8"))

    facts = set()  # populated from routes/events cross-check below
    fact_conv = {}
    for mention in events["mentions"].values():
        fact_conv[mention["fact_id"]] = mention["conv"]
    facts = set(fact_conv)
    bundles = events["bundles"]
    route_map = routes["routes"]

    problems = []

    # route targets valid and same-conversation
    for rid, route in route_map.items():
        for fid in route["target_fact_ids"]:
            if fid not in facts:
                problems.append(f"{rid}: unknown fact {fid}")
            elif fact_conv.get(fid) != route["conv"]:
                problems.append(f"{rid}: cross-conv fact {fid}")
        if not route.get("aliases"):
            problems.append(f"{rid}: no alias")
        if not route.get("node_ids"):
            problems.append(f"{rid}: no node_id")

    # bundles valid and same-conversation
    for bid, bundle in bundles.items():
        if not bundle["fact_ids"]:
            problems.append(f"{bid}: empty bundle")
        for fid in bundle["fact_ids"]:
            if fid not in facts:
                problems.append(f"{bid}: unknown fact {fid}")
            elif fact_conv.get(fid) != bundle["conv"]:
                problems.append(f"{bid}: cross-conv fact {fid}")

    # mentions all point at a real fact and carry source refs
    for mid, mention in events["mentions"].items():
        if mention["fact_id"] not in facts:
            problems.append(f"{mid}: unknown fact")
        if not mention.get("source_refs"):
            problems.append(f"{mid}: no source refs")
        if mention.get("support_basis") != "derived_fact":
            problems.append(f"{mid}: wrong support basis")

    # folding invariants
    targets = [tuple(sorted(r["target_fact_ids"])) for r in route_map.values()]
    duplicate_targets = [t for t, n in Counter(targets).items() if n > 1]
    if duplicate_targets:
        problems.append(f"duplicate canonical targets: {len(duplicate_targets)}")

    # every fact covered by at least one bundle
    covered = {fid for b in bundles.values() for fid in b["fact_ids"]}
    uncovered = facts - covered
    if uncovered:
        problems.append(f"uncovered facts: {len(uncovered)}")

    # no gold / qid leakage in build artifacts
    blob = (R1 / "routes_r1.json").read_text(encoding="utf-8") + (R1 / "events_r1.json").read_text(encoding="utf-8")
    for marker in ("question_id", "\"gold\"", "conv-26#q0"):
        if marker in blob:
            problems.append(f"question/gold marker leaked: {marker}")

    stats = {
        "facts_in_events": len(facts),
        "canonical_routes": len(route_map),
        "navigation_aliases": sum(1 for r in route_map.values() if r.get("navigation_alias")),
        "substantive_routes": sum(1 for r in route_map.values() if not r.get("navigation_alias")),
        "levels_per_route": dict(sorted(Counter(len(r["provenance_levels"]) for r in route_map.values()).items())),
        "raw_routes_folded": routes["stats"]["raw_routes"],
        "compression": round(routes["stats"]["raw_routes"] / max(1, len(route_map)), 2),
        "bundles": len(bundles),
        "mentions": len(events["mentions"]),
        "entities": len(events["entities"]),
        "mentions_partial": sum(1 for m in events["mentions"].values() if m["status"] == "partial"),
    }
    report = {
        "schema": "r1-structure-audit-v1",
        "manifest_counts": manifest["counts"],
        "stats": stats,
        "problems": problems[:50],
        "problem_count": len(problems),
        "passed": not problems,
    }
    path = R1 / "structure_audit.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:2500])
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
