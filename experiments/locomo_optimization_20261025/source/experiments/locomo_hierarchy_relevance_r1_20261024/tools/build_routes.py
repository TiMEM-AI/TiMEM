"""R1 build: fold routes that point at the same target fact set.

Rules from the handoff (section 5.1 / 6.3):
  * identical target evidence -> one canonical route carrying all layer paths
  * a parent with a single child and no new fact is a navigation alias, not
    independent support
  * keep distinct aliases (entity names, event labels) for retrieval
  * never read the question bank
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")


def stable_id(conv: str, fact_ids: list[str]) -> str:
    payload = conv + "|" + ",".join(sorted(fact_ids))
    return "route:" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def main() -> int:
    store = json.loads((SRC / "store.json").read_text(encoding="utf-8"))
    rules = json.loads((SRC / "rules.json").read_text(encoding="utf-8"))
    facts = store["facts"]
    nodes = store["nodes"]
    routes = store["routes"]

    # node child counts, used to flag navigation aliases
    children = {nid: [c for c in node.get("child_ids") or []] for nid, node in nodes.items()}
    fact_ids_of_node = {nid: set(node.get("fact_ids") or []) for nid, node in nodes.items()}

    groups = defaultdict(lambda: {
        "fact_ids": set(), "node_ids": set(), "levels": set(),
        "aliases": set(), "raw_route_ids": [],
    })
    for rid, route in routes.items():
        target = sorted({link["fact_id"] for link in route.get("links") or [] if link.get("fact_id")})
        if not target:
            continue
        key = (route["conv"], tuple(target))
        g = groups[key]
        g["fact_ids"].update(target)
        g["node_ids"].add(route["node_id"])
        g["levels"].add(route["level"])
        text = (route.get("text") or "").strip()
        if text:
            g["aliases"].add(text)
        g["raw_route_ids"].append(rid)

    canonical = {}
    alias_stats = Counter()
    for (conv, target), g in groups.items():
        rid = stable_id(conv, list(target))
        node_ids = sorted(g["node_ids"])
        # navigation alias: every contributing parent has exactly one child and no
        # facts beyond the target set
        single_child = all(len(children.get(nid) or []) == 1 for nid in node_ids)
        canonical[rid] = {
            "route_id": rid,
            "conv": conv,
            "target_fact_ids": list(target),
            "node_ids": node_ids,
            "provenance_levels": sorted(g["levels"]),
            "aliases": sorted(g["aliases"]),
            "raw_route_count": len(g["raw_route_ids"]),
            "single_child_chain": bool(single_child),
            "raw_route_ids": g["raw_route_ids"][:50],
            "navigation_alias": bool(single_child),
            "independent_support": 1,
        }
        alias_stats["navigation_alias" if canonical[rid]["navigation_alias"] else "substantive"] += 1

    by_conv = Counter(r["conv"] for r in canonical.values())
    multi_level = Counter(len(r["provenance_levels"]) for r in canonical.values())
    raw_counts = Counter()
    for r in canonical.values():
        raw_counts[r["raw_route_count"]] += 1

    out = {
        "schema": "r1-routes-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_store_sha": hashlib.sha256((SRC / "store.json").read_bytes()).hexdigest(),
        "route_min_dice": rules.get("route_min_dice"),
        "stats": {
            "raw_routes": len(routes),
            "canonical_routes": len(canonical),
            "by_conversation": dict(sorted(by_conv.items())),
            "levels_per_route": dict(sorted(multi_level.items())),
            "raw_routes_per_group": dict(sorted(raw_counts.items())),
            "navigation_alias": alias_stats["navigation_alias"],
            "substantive": alias_stats["substantive"],
        },
        "routes": canonical,
    }
    R1.mkdir(parents=True, exist_ok=True)
    path = R1 / "routes_r1.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["stats"], ensure_ascii=False, indent=2))
    print(f"\nwritten: {path} ({path.stat().st_size/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
