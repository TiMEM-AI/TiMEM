"""R1.2 step 1: freeze manifests and hashes."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

R12 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024")
R11 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_1_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen\questions.json")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    R12.mkdir(parents=True, exist_ok=True)
    files = {
        "store.json": GROUNDED / "store.json",
        "index.sqlite": GROUNDED / "index.sqlite",
        "routes_r1.json": R1 / "routes_r1.json",
        "events_r1.json": R1 / "events_r1.json",
        "questions.json": QUESTIONS,
        "dev_sample.json": R1 / "dev_sample.json",
        "p0_audit.py": R11 / "tools/p0_audit.py",
        "p1_groups.py": R11 / "tools/p1_groups.py",
        "p1_model_screen.py": R11 / "tools/p1_model_screen.py",
        "support_atoms.py": R12 / "support_atoms.py",
        "controls.py": R12 / "controls.py",
    }
    missing = [name for name, path in files.items() if not path.exists()]
    hashes = {name: sha(path) for name, path in files.items() if path.exists()}
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]
    dev = json.loads((R1 / "dev_sample.json").read_text(encoding="utf-8"))
    manifest = {
        "schema": "r1.2-run-manifest-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "hashes": hashes,
        "missing": missing,
        "counts": {
            "questions": len(questions),
            "dev_questions": len(dev["question_ids"]),
            "dev_by_category": dev["by_category"],
        },
        "notes": [
            "early token-slot selector snapshot: not preserved, marked not reproducible",
            "P1_MODEL_SCREEN.json was never written: historical 51 calls are treated as unverified",
        ],
        "artifacts": {
            "p0_report": str(R11 / "P0_EVIDENCE_AUDIT.json"),
            "p1_audit": str(R11 / "P1_SELECTOR_AUDIT.md"),
            "fact_loss": str(R11 / "P1_FACT_LOSS.json"),
        },
    }
    (R12 / "RUN_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"missing": missing, "hashed": len(hashes)}, ensure_ascii=False))
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
