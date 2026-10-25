"""R1.3 stage B: 12-window pools + one joint rerank call per arm (20 balanced questions)."""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
R12 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024")
R13 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_3_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
sys.path.insert(0, str(GROUNDED))
sys.path.insert(0, str(R12))
import grounded as g  # noqa: E402
from screen_client import ScreenClient  # noqa: E402

SEED = 20261027
PROMPT = """You re-rank optional evidence windows for one question.

Question: {question}

Protected lower windows (must be kept):
{protected}

Lower tail windows (may be replaced):
{tail}

Optional windows (each has a real id; centres are fully shown):
{candidates}

Return ONLY a JSON object:
{{"selected_optional_window_ids":[],"removed_base_tail_ids":[],
 "evidence_decisions":[{{"window_id":"","relevance":"direct|bridge|context|unrelated|uncertain",
 "supporting_fact_ids":[],"query_relation":"","answer_value_or_needed_link":"","constraint_mismatch":false}}],
 "lost_query_support":[],"new_query_support":[]}}
"""


def atomic(fid, facts):
    anchor = facts[fid]["observed_at"]
    ids = [fid]
    for other in facts.values():
        if other["conv"] == facts[fid]["conv"] and other["observed_at"] == anchor and other["id"] != fid and len(ids) < 5:
            ids.append(other["id"])
    return ids[:7]


def build_pool(conv, query, base, facts, folded, arm):
    base_facts = {f for w in base for f in w["fact_ids"]}
    qt = set(g.words(query))
    lower, upper, seen = [], [], set()
    for fact in facts.values():
        if fact["conv"] != conv or fact["id"] in base_facts:
            continue
        toks = set(g.words(fact["text"]))
        if not (qt & toks) or fact["id"] in seen:
            continue
        seen.add(fact["id"])
        lower.append({"id": fact["id"], "text": fact["text"], "score": len(qt & toks)})
    lower.sort(key=lambda x: (-x["score"], x["id"]))
    if arm == "H_rel":
        for route in sorted(folded.values(), key=lambda r: r["route_id"]):
            if route["conv"] != conv:
                continue
            alias = set()
            for a in route.get("aliases", [])[:3]:
                alias |= set(g.words(a))
            if not (qt & alias):
                continue
            for t in route["target_fact_ids"]:
                if t in base_facts or t not in facts or t in seen:
                    continue
                seen.add(t)
                upper.append({"id": t, "text": facts[t]["text"]})
                break
            if len(upper) >= 4:
                break
    if arm == "L_rel":
        return lower[:12]
    return (lower[:8] + upper[:4])[:12]


def main() -> int:
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    dev = json.loads((R1 / "dev_sample.json").read_text(encoding="utf-8"))["question_ids"]
    folded = json.loads((R1 / "routes_r1.json").read_text(encoding="utf-8"))["routes"]
    facts = json.loads((GROUNDED / "store.json").read_text(encoding="utf-8"))["facts"]
    retriever = g.Retriever()
    rng = random.Random(SEED)
    picked = []
    for cat in ("2", "4", "1", "3"):
        pool = sorted([q for q in dev if str(questions[q].get("category")) == cat])
        picked += rng.sample(pool, min(5, len(pool)))
    picked = picked[:20]
    client = ScreenClient(R13 / "screen_stageB.jsonl")
    stats = {a: Counter() for a in ("L_rel", "H_rel")}
    rows = []

    for qid in picked:
        q = questions[qid]
        conv, query = q["sample_id"], q["question"]
        base, _ = retriever.windows(conv, query, all_levels=False)
        base_evidence, _ = retriever.render(base, query)
        for arm in ("L_rel", "H_rel"):
            pool = build_pool(conv, query, base, facts, folded, arm)
            if not pool:
                rows.append({"qid": qid, "arm": arm, "status": "no_candidates"})
                continue
            protected = [f"w{i+1}: {w['fact_ids'][0]}" for i, w in enumerate(base[:20])]
            tail = [f"t{i+1}: " + facts[w["fact_ids"][0]]["text"][:160] for i, w in enumerate(base[20:]) if w["fact_ids"][0] in facts]
            cands = [{"id": f"c{i+1}", "centre": item["text"], "context": []} for i, item in enumerate(pool)]
            prompt = PROMPT.format(question=query, protected="\n".join(protected),
                                   tail="\n".join(tail),
                                   candidates="\n".join(f"[{c['id']}] {c['centre']}" for c in cands))
            record = client.screen_raw(prompt, [c["id"] for c in cands])
            stats[arm]["attempted"] += 1
            if record["status"] != "valid":
                stats[arm]["technical"] += 1
                rows.append({"qid": qid, "arm": arm, "status": record["status"]})
                continue
            stats[arm]["valid"] += 1
            verdict = record["parsed"]
            selected_ids = set(verdict.get("selected_optional_window_ids") or [])
            removed = set(verdict.get("removed_base_tail_ids") or [])
            by_id = {f"c{i+1}": item for i, item in enumerate(pool)}
            chosen = [w for w in base]
            for idx in range(len(chosen) - 1, 19, -1):
                if f"t{idx-19}" in removed:
                    chosen.pop(idx)
            for cid in selected_ids:
                item = by_id.get(cid)
                if item:
                    chosen.append({"center": item["id"], "fact_ids": [item["id"]], "channel": "stageB"})
            chosen = chosen[:24]
            evidence, _ = retriever.render(chosen, query)
            changed = evidence != base_evidence
            stats[arm]["changed"] += int(changed)
            stats[arm]["selected"] += len(selected_ids)
            stats[arm]["removed"] += len(removed)
            rows.append({"qid": qid, "arm": arm, "status": "valid", "selected": len(selected_ids),
                         "removed": len(removed), "changed": changed})
            print(f"{qid} {arm}: selected={len(selected_ids)} removed={len(removed)} changed={changed}", flush=True)

    summary = {"schema": "r1.3-stageB-v1", "seed": SEED, "questions": len(picked),
               "picked": picked, "stats": {k: dict(v) for k, v in stats.items()}, "rows": rows}
    (R13 / "STAGE_B_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["stats"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
