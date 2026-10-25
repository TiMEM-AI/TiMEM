"""P1 fallback: budget-matched model screening shared by both arms (80 dev questions).

One call per question; the same candidate list and prompts are used for L_rel and
H_rel, so the only difference stays "which candidates the arm can reach".
No answer generation, no gold.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

import httpx

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
R11 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_1_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
sys.path.insert(0, str(GROUNDED))
import grounded as g  # noqa: E402

sys.path.insert(0, str(R11 / "tools"))
from p1_groups import atomic_window, lower_pool, upper_pool, select  # noqa: E402

BASE = os.getenv("SCREEN_BASE", "https://kspmas.ksyun.com/v1")
KEY = os.getenv("SCREEN_KEY", "")
MODEL = os.getenv("SCREEN_MODEL", "deepseek-v4.1-flash")
PROMPT = """You are a retrieval auditor. Given a question and candidate evidence windows, decide for each window:
- supported: whether its text is self-contained evidence (no invented facts)
- relevance: direct (answers a slot), bridge (connects two entities/steps), context (explains a reference), unrelated
- covers: short slot names it covers (entities, actions, time, list members)

Question: {question}

Candidates:
{candidates}

Return ONLY a JSON array: [{{"id":"w1","supported":true,"relevance":"direct","covers":["..."]}}]
"""


async def screen(client, sem, question, cands):
    lines = []
    for cid, text in cands:
        lines.append(f'[{cid}] {text[:420]}')
    prompt = PROMPT.format(question=question, candidates="\n".join(lines))
    async with sem:
        for attempt in range(2):
            try:
                r = await client.post(f"{BASE}/chat/completions", timeout=120, json={
                    "model": MODEL, "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}]})
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"]
                start, end = text.find("["), text.rfind("]")
                return json.loads(text[start:end + 1])
            except Exception:
                if attempt == 1:
                    return []
                await asyncio.sleep(2)


async def main() -> int:
    dev = json.loads((R1 / "dev_sample.json").read_text(encoding="utf-8"))["question_ids"]
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    folded = json.loads((R1 / "routes_r1.json").read_text(encoding="utf-8"))["routes"]
    retriever = g.Retriever()
    sem = asyncio.Semaphore(4)
    stats = Counter()
    rows = []

    async with httpx.AsyncClient() as client:
        for qid in dev:
            q = questions[qid]
            conv, query = q["sample_id"], q["question"]
            base, _ = retriever.windows(conv, query, all_levels=False)
            base_facts = {f for w in base for f in w["fact_ids"]}
            lower = lower_pool(retriever, conv, query)[:24]
            upper = upper_pool(retriever, folded, conv, query)[:16]
            cands = []
            meta = {}
            idx = 0
            for pool, kind in ((lower, "lower"), (upper, "upper")):
                for item in pool:
                    if kind == "lower":
                        fid = item["center"]; ids = item["fact_ids"]
                    else:
                        fid = None
                        for target in item["route"]["target_fact_ids"]:
                            if target not in base_facts:
                                fid = target
                                break
                        if fid is None:
                            continue
                        ids = atomic_window(retriever, fid)
                    if not ids or all(x in base_facts for x in ids):
                        continue
                    idx += 1
                    cid = f"w{idx}"
                    meta[cid] = {"kind": kind, "ids": ids, "center": fid}
                    cands.append((cid, " ".join(retriever.facts[x]["text"] for x in ids)))
            if not cands:
                stats["no_candidates"] += 1
                continue
            verdict = await screen(client, sem, query, cands[:48])
            by_id = {v.get("id"): v for v in verdict if isinstance(v, dict)}
            accepted = []
            for cid, _text in cands[:48]:
                v = by_id.get(cid) or {}
                rel = v.get("relevance", "unknown")
                ok = bool(v.get("supported")) and rel in ("direct", "bridge", "context")
                stats[f"verdict_{rel}"] += 1
                if ok:
                    accepted.append(meta[cid])
            stats["accepted_candidates"] += len(accepted)
            rows.append({"question_id": qid, "candidates": len(cands), "accepted": len(accepted),
                         "upper_accepted": sum(1 for a in accepted if a["kind"] == "upper")})
            print(f"{qid}: cand={len(cands)} accepted={len(accepted)}", flush=True)

    summary = {"schema": "r1.1-p1-model-screen-v1", "questions": len(dev), "model": MODEL,
               "stats": dict(stats), "rows": rows}
    (R11 / "P1_MODEL_SCREEN.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(stats), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
