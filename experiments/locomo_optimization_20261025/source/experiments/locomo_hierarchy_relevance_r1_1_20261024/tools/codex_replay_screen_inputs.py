"""Read-only replay of current screen input preparation; makes no network/model call.

The JSON output describes rebuilt inputs for all 80 development questions. It is
not a log of the reported 51 historical screening requests or their responses.
Run with the same local Python environment as p1_groups.py.
"""
from __future__ import annotations

import collections
import hashlib
import json
import statistics
from pathlib import Path

import p1_groups as p


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    retriever = p.g.Retriever()
    folded = json.loads((p.R1 / "routes_r1.json").read_text(encoding="utf-8"))["routes"]
    questions = {
        q["question_id"]: q
        for q in json.loads(p.QUESTIONS.read_text(encoding="utf-8"))["questions"]
    }
    dev = json.loads((p.R1 / "dev_sample.json").read_text(encoding="utf-8"))["question_ids"]
    counts = collections.Counter()
    rows = []
    for qid in dev:
        question = questions[qid]
        base = json.loads(
            (root / "requests" / "p1" / "B" / (qid.replace("#", "_") + ".json")).read_text(encoding="utf-8")
        )
        base_facts = {fid for window in base["windows"] for fid in window}
        lower = p.lower_pool(retriever, question["sample_id"], question["question"])[:24]
        upper = p.upper_pool(retriever, folded, question["sample_id"], question["question"])[:16]
        rebuilt = []
        for pool, kind in ((lower, "lower"), (upper, "upper")):
            for item in pool:
                if kind == "lower":
                    fid, ids = item["center"], item["fact_ids"]
                else:
                    fid = next((x for x in item["route"]["target_fact_ids"] if x not in base_facts), None)
                    if fid is None:
                        continue
                    ids = p.atomic_window(retriever, fid)
                if not ids or all(x in base_facts for x in ids):
                    continue
                text = " ".join(retriever.facts[x]["text"] for x in ids)
                center_text = retriever.facts[fid]["text"]
                offset = text.index(center_text)
                fully_visible = center_text in text[:420]
                starts_visible = offset < 420
                counts[f"candidate_{kind}"] += 1
                counts[f"center_full_present_{kind}"] += int(fully_visible)
                counts[f"center_any_present_{kind}"] += int(starts_visible)
                counts[f"center_beyond_420_{kind}"] += int(not starts_visible)
                counts[f"window_truncated_{kind}"] += int(len(text) > 420)
                rebuilt.append({
                    "center": fid,
                    "kind": kind,
                    "fact_ids": ids,
                    "window_chars": len(text),
                    "center_chars": len(center_text),
                    "center_start_offset": offset,
                    "center_fully_visible_in_420": fully_visible,
                    "center_starts_within_420": starts_visible,
                    "full_window_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "visible_prefix_sha256": hashlib.sha256(text[:420].encode("utf-8")).hexdigest(),
                })
        rows.append({"question_id": qid, "base_windows": len(base["windows"]), "candidates": len(rebuilt), "items": rebuilt})
    sizes = [r["candidates"] for r in rows]
    sources = [root / "tools" / "p1_model_screen.py", root / "tools" / "p1_groups.py", p.GROUNDED / "grounded.py"]
    result = {
        "scope": "current_code_offline_rebuild_all_80_not_historical_51_response_log",
        "model_calls": 0,
        "network_calls": 0,
        "questions": len(rows),
        "candidate_count_min": min(sizes),
        "candidate_count_median": statistics.median(sizes),
        "candidate_count_max": max(sizes),
        "candidate_count_distribution": dict(collections.Counter(sizes)),
        "stats": dict(counts),
        "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
        "rows": rows,
    }
    target = root / "CODEX_SCREEN_INPUT_REPLAY_20261024.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: value for k, value in result.items() if k != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
