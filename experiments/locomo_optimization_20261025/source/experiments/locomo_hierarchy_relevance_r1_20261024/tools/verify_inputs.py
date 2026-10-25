"""R1 step 1: verify inputs, freeze hashes and counts, write manifest.json."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen\questions.json")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    R1.mkdir(parents=True, exist_ok=True)
    store = json.loads((SRC / "store.json").read_text(encoding="utf-8"))
    rules = json.loads((SRC / "rules.json").read_text(encoding="utf-8"))
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]
    qids = {q["question_id"] for q in questions}
    cats = Counter(str(q.get("category")) for q in questions)

    facts = store["facts"]
    nodes = store["nodes"]
    routes = store["routes"]
    levels = Counter(n["level"] for n in nodes.values())
    route_levels = Counter(r["level"] for r in routes.values())

    problems = []
    if len(facts) != 7157:
        problems.append(f"facts {len(facts)} != 7157")
    if len(questions) != 861 or len(qids) != 861:
        problems.append("questions not 861 unique")
    if dict(cats) != {"2": 167, "1": 77, "4": 581, "3": 36}:
        problems.append(f"categories {dict(cats)}")
    convs = {f["conv"] for f in facts.values()}
    if len(convs) != 10:
        problems.append(f"convs {len(convs)}")

    manifest = {
        "schema": "r1-manifest-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(SRC),
        "questions": str(QUESTIONS),
        "hashes": {
            "store.json": sha(SRC / "store.json"),
            "rules.json": sha(SRC / "rules.json"),
            "index.sqlite": sha(SRC / "index.sqlite"),
            "questions.json": sha(QUESTIONS),
        },
        "counts": {
            "facts": len(facts),
            "nodes": len(nodes),
            "nodes_by_level": dict(sorted(levels.items())),
            "routes": len(routes),
            "routes_by_level": dict(sorted(route_levels.items())),
            "conversations": len(convs),
            "questions": len(questions),
            "categories": dict(cats),
        },
        "facts_context_groups": len(store.get("context_groups", {})),
        "rules": {k: v for k, v in rules.items() if k != "schema"},
        "protocol": {
            "arms": ["L", "H"],
            "dev_ablation_arm": "H_fold",
            "max_windows": 24,
            "protected_flat_windows": 20,
            "upper_extra_windows": 4,
            "atomic_slots": 64,
            "protected_low_slots": 48,
            "upper_slots": 16,
            "route_return_limit": 200,
            "fact_return_limit": 400,
            "facts_per_window": 7,
            "dev_seed": 20261025,
            "dev_size": 80,
        },
        "problems": problems,
    }
    (R1 / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"problems": problems, "counts": manifest["counts"]}, ensure_ascii=False, indent=2))
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
