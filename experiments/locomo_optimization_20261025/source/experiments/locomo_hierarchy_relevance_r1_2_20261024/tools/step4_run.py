"""R1.2 step 4: frozen two-arm screening run on the 80 dev questions.

L_rel: lower candidates only. H_rel: at least 48 lower slots + up to 16 upper
windows found through folded routes. One screening call per arm per question.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
R12 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
sys.path.insert(0, str(GROUNDED))
sys.path.insert(0, str(R12))
import grounded as g  # noqa: E402
from support_atoms import select_with_support  # noqa: E402
from screen_client import ScreenClient  # noqa: E402

MAX_CANDIDATES = 40
LOWER_SLOTS = 48
UPPER_SLOTS = 16


def atomic(fid: str, facts: dict) -> list[str]:
    anchor = facts[fid]["observed_at"]
    ids = [fid]
    for other in facts.values():
        if other["conv"] != facts[fid]["conv"] or other["observed_at"] != anchor:
            continue
        if other["id"] != fid and len(ids) < 5:
            ids.append(other["id"])
    return ids[:7]


def build_pool(conv, query, base, facts, folded, arm: str):
    base_facts = {f for w in base for f in w["fact_ids"]}
    qt = set(g.words(query))
    lower, upper = [], []
    for fact in facts.values():
        if fact["conv"] != conv or fact["id"] in base_facts:
            continue
        tokens = set(g.words(fact["text"]))
        if not (qt & tokens):
            continue
        lower.append({"id": fact["id"], "text": fact["text"], "score": len(qt & tokens)})
    lower.sort(key=lambda x: -x["score"])
    for route in folded.values():
        if route["conv"] != conv:
            continue
        alias_tokens = set()
        for alias in route.get("aliases", [])[:3]:
            alias_tokens |= set(g.words(alias))
        if not (qt & alias_tokens):
            continue
        for target in route["target_fact_ids"]:
            if target in base_facts or target not in facts:
                continue
            if any(u["id"] == target for u in upper):
                continue
            upper.append({"id": target, "text": facts[target]["text"],
                          "score": len(qt & alias_tokens), "levels": route["provenance_levels"]})
            break
        if len(upper) >= UPPER_SLOTS:
            break
    if arm == "L_rel":
        return lower[:MAX_CANDIDATES]
    pool = lower[: max(0, MAX_CANDIDATES - len(upper))] + upper[:UPPER_SLOTS]
    return pool[:MAX_CANDIDATES]


def to_candidates(pool, facts):
    out = []
    for i, item in enumerate(pool):
        out.append({"id": f"c{i+1}", "centre": item["text"], "context": [],
                    "fact_id": item["id"], "_levels": item.get("levels")})
    return out


def main() -> int:
    dev = json.loads((R1 / "dev_sample.json").read_text(encoding="utf-8"))["question_ids"]
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    folded = json.loads((R1 / "routes_r1.json").read_text(encoding="utf-8"))["routes"]
    facts = json.loads((GROUNDED / "store.json").read_text(encoding="utf-8"))["facts"]
    retriever = g.Retriever()
    client = ScreenClient(R12 / "screen_run.jsonl")
    out_dir = R12 / "requests" / "step4"
    stats = {"L_rel": {"changed": 0, "accepted": 0, "replaced": 0, "valid": 0, "technical": 0},
             "H_rel": {"changed": 0, "accepted": 0, "replaced": 0, "valid": 0, "technical": 0}}
    rows = []

    for qid in dev:
        q = questions[qid]
        conv, query = q["sample_id"], q["question"]
        base, _ = retriever.windows(conv, query, all_levels=False)
        base_evidence, _ = retriever.render(base, query)
        for arm in ("L_rel", "H_rel"):
            pool = build_pool(conv, query, base, facts, folded, arm)
            cands = to_candidates(pool, facts)
            if not cands:
                stats[arm]["valid" if False else "technical"] += 0
                rows.append({"qid": qid, "arm": arm, "status": "no_candidates"})
                continue
            record = client.screen(query, cands)
            if record["status"] != "valid":
                stats[arm]["technical"] += 1
                rows.append({"qid": qid, "arm": arm, "status": record["status"]})
                continue
            stats[arm]["valid"] += 1
            verdicts = {v["id"]: v for v in record.get("verdicts") or [] if isinstance(v, dict)}
            accepted = []
            for c in cands:
                v = verdicts.get(c["id"]) or {}
                if v.get("supported") is True and v.get("relevance") in ("direct", "bridge"):
                    accepted.append({"center": c["fact_id"], "fact_ids": [c["fact_id"]],
                                     "channel": "screen", "_gain": True})
            selected, audit = select_with_support(base, accepted, list(facts.values()))
            evidence, _ = retriever.render(selected, query)
            stats[arm]["accepted"] += len(audit["accepted"])
            stats[arm]["replaced"] += len(audit["replaced"])
            stats[arm]["changed"] += int(evidence != base_evidence)
            (out_dir / arm).mkdir(parents=True, exist_ok=True)
            (out_dir / arm / f"{qid.replace('#', '_')}.json").write_text(json.dumps({
                "question_id": qid, "arm": arm, "candidates": len(cands),
                "accepted_model": len(accepted), "selector_accepted": len(audit["accepted"]),
                "replaced": len(audit["replaced"]), "evidence_sha": hashlib.sha256(evidence.encode()).hexdigest(),
                "changed": evidence != base_evidence,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            rows.append({"qid": qid, "arm": arm, "status": "valid",
                         "candidates": len(cands), "accepted": len(audit["accepted"]),
                         "replaced": len(audit["replaced"]), "changed": evidence != base_evidence})
            print(f"{qid} {arm}: cand={len(cands)} accepted={len(audit['accepted'])} changed={evidence != base_evidence}", flush=True)

    summary = {"schema": "r1.2-step4-run-v1", "questions": len(dev), "stats": stats,
               "screen_attempts": client.attempts, "rows": rows}
    (R12 / "STEP4_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
