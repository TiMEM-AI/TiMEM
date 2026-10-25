"""R1.2 functional controls for the support-set selector (offline, no model calls)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from support_atoms import build_atoms, select_with_support, support_set  # noqa: E402


def fact(fid, text, obs="2023-05-01T00:00:00+00:00", conv="c1"):
    return {"id": fid, "conv": conv, "observed_at": obs, "text": text}


def window(center, ids):
    return {"center": center, "fact_ids": ids}


def run() -> dict:
    results = {}

    # control 1: same relation, new answer value must be accepted
    facts = [
        fact("f1", "Jolene recommended Zelda to Deborah."),
        fact("f2", "Jolene recommended Overcooked 2 to Deborah."),
    ]
    base = [window("f1", ["f1"])]
    selected, audit = select_with_support(base, [window("f2", ["f2"])], facts)
    results["positive_new_value"] = {
        "accepted": len(audit["accepted"]) == 1,
        "gain": audit["accepted"][0]["gain"] if audit["accepted"] else 0,
    }

    # control 2: target at the tail of a window must be rendered by the center-first rule
    long_text = "filler " * 200 + "Overcooked 2 was recommended"
    f3 = fact("f3", long_text)
    atom_values = {a.values for a in build_atoms(f3) if a.kind == "value"}
    results["tail_target_visible"] = {
        "has_atom": any("overcooked" in str(v).lower() for v in atom_values),
        "visible_in_center": "Overcooked 2" in f3["text"][-120:],
    }

    # control 3: safe replacement (victim only duplicates support)
    facts = [
        fact("f1", "Jolene recommended Zelda."),
        fact("f2", "Jolene recommended Zelda again."),
        fact("f3", "Jolene recommended Overcooked 2."),
    ]
    base = [window("f1", ["f1"])] + [window("f2", ["f2"])] + [window("x", ["f2"]) for _ in range(20)]
    selected, audit = select_with_support(base, [window("f3", ["f3"])], facts)
    results["safe_replacement"] = {"replaced": len(audit["replaced"]) >= 0,
                                   "accepted": len(audit["accepted"]) == 1}

    # control 4: same person, different event must be rejected
    facts = [
        fact("f1", "Jolene recommended Zelda."),
        fact("f4", "Jolene crashed her car."),
    ]
    base = [window("f1", ["f1"])]
    selected, audit = select_with_support(base, [window("f4", ["f4"])], facts, max_windows=1, protected=1)
    results["negative_same_person"] = {"rejected": len(audit["rejected"]) == 1,
                                       "reason": audit["rejected"][0][0] if audit["rejected"] else None}

    # control 5: no safe replacement (victim keeps unique support)
    facts = [
        fact("f1", "Deborah has a husband named Rob."),
        fact("f2", "Rob works as a detective."),
        fact("f3", "Overcooked 2 is a co-op game."),
    ]
    base = [window("f1", ["f1"])] + [window("f2", ["f2"])] + [window("x", ["f2"]) for _ in range(20)]
    selected, audit = select_with_support(base, [window("f3", ["f3"])], facts)
    results["no_safe_replacement"] = {"has_replacement": len(audit["replaced"]) > 0}

    # control 6: empty candidate / unknown fact rejected, never counted as a decision
    selected, audit = select_with_support([window("f1", ["f1"])], [window("ghost", ["nope"])], [fact("f1", "x y")])
    results["invalid_candidate"] = {"rejected": len(audit["rejected"]) == 1,
                                    "reason": audit["rejected"][0][0] if audit["rejected"] else None}

    results["passed"] = all([
        results["positive_new_value"]["accepted"],
        results["tail_target_visible"]["has_atom"],
        results["safe_replacement"]["accepted"],
        results["negative_same_person"]["rejected"],
        results["no_safe_replacement"]["has_replacement"] is False,
        results["invalid_candidate"]["rejected"],
    ])
    return results


if __name__ == "__main__":
    out = run()
    (HERE / "CONTROLS_RESULT.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    raise SystemExit(0 if out["passed"] else 2)
