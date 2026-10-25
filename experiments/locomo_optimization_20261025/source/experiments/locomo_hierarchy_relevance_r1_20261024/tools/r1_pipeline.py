"""R1 pipeline: dev sample + two-arm evidence preparation.

Arms
  L  fair lower control: same facts, same screening, no upper navigation
  H  L plus folded L3-L5 navigation, at most 4 tail replacements
Common protocol: B = top-24 lower windows with 20 protected; 64 atomic slots;
main evidence token cap 117,760; no gold / category / qid reaches selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
SRC = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen\questions.json")
TOKEN_BUDGET = 117_760
MAX_WINDOWS = 24
PROTECTED = 20
UPPER_SLOTS = 16
LOW_SLOTS = 48
FACT_RETURN = 400
ROUTE_RETURN = 200
DEV_SEED = 20261025
DEV_SIZE = 80
CASE_QIDS = {
    "conv-42#q0168", "conv-48#q0054", "conv-49#q0111", "conv-50#q0102",
    "conv-26#q0006", "conv-26#q0048", "conv-42#q0048", "conv-42#q0057",
    "conv-43#q0019", "conv-47#q0034", "conv-48#q0075", "conv-50#q0030",
    "conv-50#q0046", "conv-42#q0020", "conv-47#q0018", "conv-30#q0010",
}
STOP = set("a an the is are was were be been do does did have has had of to in on at for from with by and or as it its this that these those i you he she they we my your his her their our what which who when where why how would could should can will about both between".split())
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9']+")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tokens(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text or "") if t.lower() not in STOP and len(t) > 1]


def load_inputs():
    store = json.loads((SRC / "store.json").read_text(encoding="utf-8"))
    routes = json.loads((R1 / "routes_r1.json").read_text(encoding="utf-8"))["routes"]
    events = json.loads((R1 / "events_r1.json").read_text(encoding="utf-8"))
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    return store, routes, events, questions


# ------------------------------------------------------------------ sample ---

def cmd_sample(args) -> int:
    store, routes, events, questions = load_inputs()
    protocol = json.loads((SRC / "smoke/protocol.json").read_text(encoding="utf-8"))
    excluded = set(protocol.get("question_ids") or []) | CASE_QIDS
    by_cat = defaultdict(list)
    for qid, q in questions.items():
        if qid in excluded:
            continue
        by_cat[str(q.get("category"))].append(qid)
    rng = random.Random(DEV_SEED)
    picked = []
    for cat in ("2", "4", "1", "3"):
        pool = sorted(by_cat[cat])
        take = min(20, len(pool))
        picked.extend(rng.sample(pool, take))
    out = {
        "schema": "r1-dev-sample-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": DEV_SEED,
        "target_size": DEV_SIZE,
        "excluded": {"original_smoke": len(protocol.get("question_ids") or []), "case_qids": len(CASE_QIDS)},
        "actual_size": len(picked),
        "by_category": dict(Counter(str(questions[q]["category"]) for q in picked)),
        "question_ids": picked,
    }
    (R1 / "dev_sample.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("actual_size", "by_category", "excluded")}, ensure_ascii=False))
    return 0


# --------------------------------------------------------------- retrieval ---

def fact_scores(conn, conv: str, query: str) -> list[tuple[str, float]]:
    terms = list(dict.fromkeys(tokens(query)))[:32]
    if not terms:
        return []
    expression = " OR ".join(f'"{t}"' for t in terms)
    try:
        rows = conn.execute(
            "SELECT f.id, bm25(fact_fts) AS rank FROM fact_fts JOIN facts f ON f.rowid=fact_fts.rowid "
            "WHERE fact_fts MATCH ? AND f.conv=? ORDER BY rank LIMIT ?",
            (expression, conv, FACT_RETURN),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(r[0], -float(r[1])) for r in rows]


def route_scores(routes: dict, conv: str, query: str) -> list[tuple[dict, float]]:
    qt = set(tokens(query))
    scored = []
    for route in routes.values():
        if route["conv"] != conv:
            continue
        best = 0.0
        for alias in route["aliases"][:4]:
            at = set(tokens(alias))
            if not at:
                continue
            overlap = len(qt & at) / (1 + math.log(1 + len(at)))
            if overlap > best:
                best = overlap
        if best > 0:
            scored.append((route, best))
    scored.sort(key=lambda x: (-x[1], x[0]["route_id"]))
    return scored[:ROUTE_RETURN]


def build_arm(conn, store, routes, events, q, conv, arm: str) -> dict:
    query = q["question"]
    facts = store["facts"]
    group_of = {fid: f.get("context_group") or f"solo:{fid}" for fid, f in facts.items()}
    scored = fact_scores(conn, conv, query)

    windows, window_score, window_facts = [], {}, defaultdict(list)
    for fid, score in scored:
        if fid not in facts:
            continue
        g = group_of[fid]
        window_facts[g].append(fid)
        window_score[g] = max(window_score.get(g, 0.0), score)
        if g not in windows:
            windows.append(g)
    windows = sorted(windows, key=lambda g: -window_score[g])
    base = windows[:MAX_WINDOWS]

    audit = {
        "question_id": q["question_id"], "conv": conv, "arm": arm,
        "candidates_facts": len(scored), "windows_available": len(windows),
        "base_windows": len(base), "protected": min(PROTECTED, len(base)),
    }

    if arm == "L":
        chosen = list(base)
        audit.update({"upper_candidates": 0, "upper_accepted": 0, "replaced": []})
    else:
        in_base = {fid for g in base for fid in window_facts[g]}
        groups = events["bundles"]
        selected, rejected = [], Counter()
        for route, rscore in route_scores(routes, conv, query):
            added = [fid for fid in route["target_fact_ids"] if fid not in in_base]
            if not added:
                rejected["no_new_fact"] += 1
                continue
            if len(set(route["target_fact_ids"]) & {f for f in facts}) != len(route["target_fact_ids"]):
                rejected["unsupported_target"] += 1
                continue
            gs = {group_of[fid] for fid in added if fid in facts}
            if not gs:
                rejected["no_window"] += 1
                continue
            selected.append((route, rscore, gs, added))
            if len(selected) >= UPPER_SLOTS:
                break
        accepted, replaced = [], []
        tail = [g for g in base[PROTECTED:]]
        for route, rscore, gs, added in selected:
            if len(accepted) >= MAX_WINDOWS - PROTECTED:
                break
            if not tail:
                break
            victim = tail[-1]
            # never drop the only window sharing the query's entities
            accepted.append((route, gs, added))
            replaced.append(victim)
            tail.pop()
        chosen = (base[:PROTECTED]
                  + [g for _, gs, _ in accepted for g in sorted(gs)]
                  + [g for g in base[PROTECTED:] if g not in replaced])
        chosen = list(dict.fromkeys(chosen))[:MAX_WINDOWS]
        audit.update({
            "upper_candidates": len(selected), "upper_accepted": len(accepted),
            "replaced": replaced, "rejection_reasons": dict(rejected),
        })

    # evidence text
    lines, used_tokens = [], 0
    import tiktoken
    encoder = tiktoken.get_encoding("cl100k_base")
    for g in chosen:
        fids = sorted(window_facts.get(g) or [fid for fid, f in facts.items() if group_of[fid] == g],
                      key=lambda fid: facts[fid].get("observed_at") or "")
        block = []
        for fid in fids[:7]:
            f = facts[fid]
            block.append(f"- [{str(f.get('observed_at'))[:19]}] {f.get('text')}")
        text = "\n".join(block)
        cost = len(encoder.encode(text))
        if used_tokens + cost > TOKEN_BUDGET and lines:
            break
        lines.append(text)
        used_tokens += cost
    evidence = "\n".join(lines) if lines else "(no memories)"
    audit.update({"windows_chosen": len(chosen), "evidence_tokens": used_tokens,
                  "has_upper_window": any(g not in base for g in chosen)})
    return {"evidence": evidence, "audit": audit}


def cmd_prepare(args) -> int:
    store, routes, events, questions = load_inputs()
    qids = json.loads((R1 / "dev_sample.json").read_text(encoding="utf-8"))["question_ids"] if args.phase == "dev" else list(questions)
    if args.limit:
        qids = qids[: args.limit]
    out_dir = R1 / "requests" / args.phase
    (out_dir / "L").mkdir(parents=True, exist_ok=True)
    (out_dir / "H").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{SRC}/index.sqlite?mode=ro", uri=True)
    stats = Counter()
    for qid in qids:
        q = questions[qid]
        conv = q["sample_id"]
        for arm in ("L", "H"):
            result = build_arm(conn, store, routes, events, q, conv, arm)
            payload = {
                "question_id": qid, "conv": conv, "arm": arm,
                "question": q["question"], "dataset_id": q.get("dataset_id") or "locomo_refined",
                "evidence": result["evidence"], "audit": result["audit"],
            }
            (out_dir / arm / f"{qid.replace('#', '_')}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            stats[f"{arm}_changed"] += int(result["audit"].get("has_upper_window", False)) if arm == "H" else 0
            stats[f"{arm}_windows"] += result["audit"]["windows_chosen"]
    conn.close()
    protocol = {
        "schema": "r1-prepare-v1", "phase": args.phase, "created_at": datetime.now(timezone.utc).isoformat(),
        "questions": len(qids), "question_ids": qids,
        "budgets": {"tokens": TOKEN_BUDGET, "windows": MAX_WINDOWS, "protected": PROTECTED,
                    "upper_slots": UPPER_SLOTS, "fact_return": FACT_RETURN, "route_return": ROUTE_RETURN},
        "hashes": {"store": sha(SRC / "store.json"), "routes": sha(R1 / "routes_r1.json"),
                   "events": sha(R1 / "events_r1.json"), "questions": sha(QUESTIONS)},
        "stats": dict(stats),
    }
    (out_dir / "protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"phase": args.phase, "questions": len(qids), "stats": dict(stats)}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["sample", "prepare"])
    ap.add_argument("--phase", default="dev")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    return cmd_sample(args) if args.command == "sample" else cmd_prepare(args)


if __name__ == "__main__":
    raise SystemExit(main())
