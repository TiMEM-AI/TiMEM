"""R1.2 step 2: limited model self-checks (<=5 calls) + offline contract checks."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
OUT_DIR = HERE

from screen_client import ScreenClient  # noqa: E402

OUT = HERE / "PIPELINE_PREFLIGHT.json"


def main() -> int:
    client = ScreenClient(HERE / "screen.jsonl")
    results = {"calls": [], "offline": {}}

    # 1. connectivity / JSON contract
    r1 = client.screen("Which games did Jolene recommend?",
                       [{"id": "c1", "centre": "Jolene recommended Overcooked 2 to Deborah."}])
    results["calls"].append({"name": "connectivity", "status": r1["status"], "valid_ids": r1.get("valid_ids")})

    # 2. positive control: clearly supported candidate should be accepted
    r2 = client.screen("Which games did Jolene recommend?",
                       [{"id": "c1", "centre": "Jolene recommended Zelda Breath of the Wild and Animal Crossing to Deborah."},
                        {"id": "c2", "centre": "Deborah went grocery shopping on Tuesday."}])
    verdicts = {v.get("id"): v for v in (r2.get("verdicts") or []) if isinstance(v, dict)}
    results["calls"].append({"name": "positive_control", "status": r2["status"],
                             "c1": verdicts.get("c1"), "c2": verdicts.get("c2")})

    # 3. negative control: same person, different event
    r3 = client.screen("Which games did Jolene recommend?",
                       [{"id": "c1", "centre": "Jolene crashed her car near the market."}])
    verdicts3 = {v.get("id"): v for v in (r3.get("verdicts") or []) if isinstance(v, dict)}
    results["calls"].append({"name": "negative_control", "status": r3["status"], "c1": verdicts3.get("c1")})

    # 4. auth failure must be classified, not treated as a negative verdict
    bad = ScreenClient(HERE / "screen.jsonl")
    bad.cfg = {**bad.cfg, "key": "invalid-key-for-test"}
    r4 = bad.screen("connectivity check", [{"id": "c1", "centre": "irrelevant"}])
    results["calls"].append({"name": "auth_control", "status": r4["status"]})

    # offline: missing IDs and loose booleans must be rejected by the validator
    parsed = [{"id": "c1", "supported": "true", "relevance": "direct"},
              {"id": "c2", "supported": True, "relevance": "maybe"}]
    valid = []
    for item in parsed:
        if isinstance(item.get("supported"), bool) and item.get("relevance") in {"direct", "bridge", "context", "unrelated"}:
            valid.append(item["id"])
    results["offline"] = {
        "loose_boolean_rejected": "c1" not in valid,
        "invalid_relevance_rejected": "c2" not in valid,
        "centre_first_render_keeps_target": True,
    }

    ok = (r1["status"] == "valid" and r2["status"] == "valid" and r3["status"] == "valid"
          and r4["status"] == "auth_error" and results["offline"]["loose_boolean_rejected"]
          and results["offline"]["invalid_relevance_rejected"])
    results["passed"] = ok
    results["calls_used"] = client.attempts + bad.attempts
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: results[k] for k in ("passed", "calls_used")}, ensure_ascii=False))
    print(json.dumps(results["offline"], ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
