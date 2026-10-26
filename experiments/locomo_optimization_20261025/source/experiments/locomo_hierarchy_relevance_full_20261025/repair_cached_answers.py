"""Repair only cross-question cache collisions; preserve all other experiment data."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from full_answer import FULL, QUESTIONS, question_item, score_cache_key, score_request, summarize

REPAIR = FULL / "cache_repair_20261025"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def find_collisions(questions: dict, endpoints: dict):
    seen, collisions, identities = {}, [], set()
    for qid, q in questions.items():
        texts = {read(FULL / "evidence" / arm / f"{qid.replace('#','_')}.json")["evidence"]
                 for arm in ("B", "L", "H")}
        for evidence in texts:
            identities.add(score_cache_key(question_item(q), evidence, endpoints))
            if evidence in seen and seen[evidence] != qid:
                collisions.append({"question_id": qid, "reused_question_id": seen[evidence]})
            else:
                seen[evidence] = qid
    return collisions, len(identities)


async def main(execute: bool):
    import runners.aml_run as runner

    questions = {q["question_id"]: q for q in read(QUESTIONS)["questions"]}
    endpoints = runner._score_endpoints()
    if endpoints["answer_model"] != "deepseek-v4.1-flash" or endpoints["judge_model"] != "deepseek-v4.1-flash":
        raise ValueError("Repair must retain the original answer and judge models")
    collisions, unique_requests = find_collisions(questions, endpoints)
    targets = sorted({c["question_id"] for c in collisions})
    expected = ["conv-26#q0026", "conv-47#q0066"]
    if targets != expected:
        raise ValueError(f"Collision scope changed: {targets}")
    preflight = {"collisions": collisions, "unique_question_requests": unique_requests,
                 "answer_model": endpoints["answer_model"], "judge_model": endpoints["judge_model"]}
    print(json.dumps(preflight, ensure_ascii=False), flush=True)
    if not execute:
        return

    if (REPAIR / "COMPLETED.json").exists():
        print("Repair already completed; no repeated model calls.", flush=True)
        return
    pending = {}
    original_chat = runner.complete_chat
    for qid in targets:
        stem = qid.replace("#", "_")
        all_evidence = [read(FULL / "evidence" / arm / f"{stem}.json")["evidence"] for arm in ("B", "L", "H")]
        if len(set(all_evidence)) != 1:
            raise ValueError(f"Repair needs separate arm scoring: {qid}")
        item = question_item(questions[qid])
        evidence = all_evidence[0]
        identity = score_cache_key(item, evidence, endpoints)
        result_path = REPAIR / "results" / f"{stem}.json"
        write(REPAIR / "requests" / f"{stem}.json", score_request(item, evidence, endpoints))
        if result_path.exists():
            saved = read(result_path)
            if saved["request_sha"] != identity:
                raise ValueError(f"Repair resume request changed: {qid}")
            result = saved["result"]
        else:
            async def traced_chat(**kwargs):
                public = {k: v for k, v in kwargs.items() if k != "api_key"}
                public["request_sha"] = hashlib.sha256(json.dumps(public, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                response = await original_chat(**kwargs)
                with (REPAIR / "model_calls.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"question_id": qid, "request": public, "response": response}, ensure_ascii=False) + "\n")
                return response

            runner.complete_chat = traced_chat
            print(f"Scoring {qid}", flush=True)
            try:
                result = await runner._score_question_row(item, evidence, **endpoints)
            finally:
                runner.complete_chat = original_chat
            if result.get("method") == "judge_parse_error" or not isinstance(result.get("correct"), bool):
                raise ValueError(f"Invalid judge result: {qid}")
            write(result_path, {"request_sha": identity, "result": result})
        old = read(FULL / "answers" / f"{stem}.json")
        pending[qid] = {"question_id": qid, "category": old["category"],
                        "arms": {arm: {"correct": result["correct"], "generated": result["generated"],
                                       "request_sha": identity} for arm in ("B", "L", "H")},
                        "cache_repair": {"protocol": "locomo-question-cache-v2", "shared_arms": ["B", "L", "H"]}}
        print(json.dumps({"qid": qid, "correct": result["correct"], "generated": result["generated"]}, ensure_ascii=False), flush=True)
    for qid, record in pending.items():
        write(FULL / "answers" / f"{qid.replace('#','_')}.json", record)
    summary = summarize(questions, unique_scored=unique_requests, endpoints=endpoints)
    summary["repair"] = {"questions": targets, "new_answer_requests": 2, "new_judge_requests": 2,
                         "original_summary": "cache_repair_20261025/before/RESULTS_FULL.json"}
    write(FULL / "RESULTS_FULL.json", summary)
    write(REPAIR / "COMPLETED.json", {"completed_at": datetime.now(timezone.utc).isoformat(),
          **preflight, "repaired": targets, "stats": summary["stats"]})
    print(json.dumps(summary["stats"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Make two answer/judge requests and apply the repair")
    asyncio.run(main(parser.parse_args().execute))
