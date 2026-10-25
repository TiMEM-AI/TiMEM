"""R1.1 P1: shared support/relevance/marginal-value selector and five groups.

Groups (all share P0's B, atomic windows and W0 render):
  B       lower result only (fallback anchor)
  H_raw   raw unfolded routes + legacy selection rule
  H_fold  folded routes + the same legacy rule
  L_rel   lower candidates + shared selector
  H_rel   lower + folded upper candidates + shared selector
No model calls; deterministic selector, explicitly labelled as rule-based.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
R11 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_1_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")

sys.path.insert(0, str(GROUNDED))
import grounded as g  # noqa: E402




# ---------------------------------------------------------- query planning ---

RELATION_HINTS = {
    "recommend", "recommended", "suggest", "invite", "invited", "visit", "visited",
    "work", "worked", "adopt", "adopted", "move", "moved", "buy", "bought", "meet",
    "met", "travel", "travelled", "play", "played", "cook", "cooked", "read", "read",
    "start", "started", "finish", "finished", "join", "joined", "make", "made",
    "take", "took", "give", "gave", "plan", "planned", "happen", "happened",
    "crash", "crashed", "return", "returned", "raise", "raised", "share", "shared",
}
SLOT_HINTS = [
    ("time", re.compile(r"\b(when|what date|what day|what month|what year|how long|how often)\b", re.I)),
    ("person", re.compile(r"\b(who|whose|whom)\b", re.I)),
    ("place", re.compile(r"\b(where)\b", re.I)),
    ("reason", re.compile(r"\b(why|what reason)\b", re.I)),
    ("list", re.compile(r"\b(what|which|list|name|kinds?|types?)\b", re.I)),
    ("count", re.compile(r"\b(how many|number of)\b", re.I)),
]
MONTHS = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b", re.I)
YEAR = re.compile(r"\b(19|20)\d{2}\b")
NAME = re.compile(r"\b([A-Z][a-z]{2,})\b")


def parse_query(query: str) -> dict:
    tokens = set(g.words(query))
    entities = {m.group(1) for m in NAME.finditer(query)} - {"The", "What", "Which", "When", "Where", "Who", "Why", "How"}
    relations = tokens & RELATION_HINTS
    slots = {name for name, pattern in SLOT_HINTS if pattern.search(query)}
    return {"entities": entities, "relations": relations, "slot_types": slots,
            "multihop": len(entities) >= 2 or bool(re.search(r"\bafter\b|\bbefore\b|\bthen\b", query, re.I))}


def window_slots(text: str, plan: dict) -> set:
    tokens = set(g.words(text))
    slots = set()
    for entity in plan["entities"]:
        if entity.lower() in tokens or entity in text:
            slots.add(("entity", entity))
    for relation in plan["relations"]:
        if relation in tokens:
            slots.add(("relation", relation))
    if "time" in plan["slot_types"] and (MONTHS.search(text) or YEAR.search(text)):
        slots.add(("type", "time"))
    if "person" in plan["slot_types"] and NAME.search(text):
        slots.add(("type", "person"))
    if "place" in plan["slot_types"] and tokens & {"in", "at", "near", "city", "town", "country"}:
        slots.add(("type", "place"))
    if "reason" in plan["slot_types"] and tokens & {"because", "since", "so", "reason"}:
        slots.add(("type", "reason"))
    return slots


def atomic_window(retriever, fid):
    ids = retriever.window(fid)
    if fid not in ids:
        ids = [fid] + list(ids)
    anchor = retriever.facts[fid]["observed_at"]
    ids = [x for x in ids if retriever.facts[x]["observed_at"] == anchor]
    if fid not in ids:
        ids.insert(0, fid)
    return ids[:7]


def lower_pool(retriever, conv, query, limit=400):
    """Atomic windows centred on lower facts beyond B, capped at 64 slots."""
    hits = retriever.search(conv, query, "fact")[:limit]
    pool = []
    seen = set()
    for hit in hits:
        fid = hit["id"]
        ids = atomic_window(retriever, fid)
        key = tuple(ids)
        if key in seen:
            continue
        seen.add(key)
        pool.append({"center": fid, "fact_ids": ids, "score": hit["score"], "source": "lower"})
    return pool


def upper_pool(retriever, routes, conv, query):
    qt = set(g.words(query))
    pool = []
    for route in routes.values():
        if route["conv"] != conv:
            continue
        alias_tokens = set()
        aliases = route.get("aliases") or [route.get("text", "")]
        for alias in aliases[:3]:
            alias_tokens |= set(g.words(alias))
        overlap = len(qt & alias_tokens)
        if overlap == 0:
            continue
        pool.append({"route": route, "score": overlap, "tokens": qt & alias_tokens})
    pool.sort(key=lambda x: (-x["score"], str(x["route"].get("route_id") or x["route"].get("id") or "")))
    return pool[:200]


def select(retriever, base, lower, upper, query, *, allow_upper: bool, protect_relevant: bool = True):
    """Shared selector: support, relevance, marginal value, replacement loss."""
    selected = [dict(w) for w in base]
    covered = {fid for w in selected for fid in w["fact_ids"]}
    query_tokens = set(g.words(query))
    plan = parse_query(query)
    covered_slots = set()
    for w in selected:
        text = " ".join(retriever.facts[fid]["text"] for fid in w["fact_ids"])
        covered_slots |= window_slots(text, plan)
    covered_tokens = set()
    for w in selected:
        for fid in w["fact_ids"]:
            covered_tokens |= set(g.words(retriever.facts[fid]["text"]))
    audit = {"accepted": [], "rejected": Counter(), "replaced": []}

    def try_add(cand):
        nonlocal covered, covered_tokens, covered_slots
        ids = [x for x in cand["fact_ids"] if x not in covered]
        if cand.get("center") and cand["center"] not in ids:
            ids.insert(0, cand["center"])
        ids = [x for x in ids if x in retriever.facts]
        if not ids:
            audit["rejected"]["no_new_fact"] += 1
            return
        # support: same conversation + same observation time + <=7 facts
        convs = {retriever.facts[x]["conv"] for x in ids}
        times = {retriever.facts[x]["observed_at"] for x in ids}
        if len(convs) != 1 or len(times) != 1 or len(ids) > 7:
            audit["rejected"]["support_invalid"] += 1
            return
        # relevance: at least one informative query token
        window_text = " ".join(retriever.facts[x]["text"] for x in ids)
        window_tokens = set(g.words(window_text))
        if not (query_tokens & window_tokens):
            audit["rejected"]["irrelevant"] += 1
            return
        slots = window_slots(window_text, plan)
        new_slots = slots - covered_slots
        if not new_slots:
            audit["rejected"]["no_new_slot"] += 1
            return
        if len(selected) < g.RULES["max_windows"]:
            selected.append({"center": cand.get("center"), "fact_ids": ids,
                             "channel": cand.get("source", "upper")})
            covered.update(ids)
            covered_tokens |= window_tokens
            covered_slots |= slots
            audit["accepted"].append({"center": cand.get("center"), "ids": ids, "new_slots": sorted(map(str, new_slots))})
            return
        if not new_slots:
            audit["rejected"]["no_new_slot"] += 1
            return
        # replacement: victim must not hold the slot the candidate fills
        for idx in range(len(selected) - 1, g.RULES["protected_flat_windows"] - 1, -1):
            victim = selected[idx]
            others = {x for i, w in enumerate(selected) if i != idx for x in w["fact_ids"]}
            unique = [x for x in victim["fact_ids"] if x not in others]
            unique_text = " ".join(retriever.facts[x]["text"] for x in unique)
            victim_slots = window_slots(unique_text, plan)
            if new_slots & victim_slots:
                continue
            if protect_relevant and (victim_slots & covered_slots):
                continue
            audit["replaced"].append({"removed": victim["fact_ids"], "added": ids, "center": cand.get("center")})
            selected[idx] = {"center": cand.get("center"), "fact_ids": ids, "channel": cand.get("source", "upper")}
            covered = {fid for w in selected for fid in w["fact_ids"]}
            covered_tokens |= window_tokens
            covered_slots |= slots
            audit["accepted"].append({"center": cand.get("center"), "ids": ids, "new_slots": sorted(map(str, new_slots))})
            return
        audit["rejected"]["no_replaceable_tail"] += 1

    for cand in lower:
        try_add(cand)
    if allow_upper:
        for item in upper:
            route = item["route"]
            targets = route.get("target_fact_ids") or [l["fact_id"] for l in (route.get("links") or []) if l.get("fact_id")]
            for fid in targets:
                if fid in covered:
                    continue
                ids = atomic_window(retriever, fid)
                try_add({"center": fid, "fact_ids": ids, "score": item["score"], "source": "upper"})
                break
    return selected, audit


def main() -> int:
    dev = json.loads((R1 / "dev_sample.json").read_text(encoding="utf-8"))["question_ids"]
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    folded = json.loads((R1 / "routes_r1.json").read_text(encoding="utf-8"))["routes"]
    raw = json.loads((GROUNDED / "store.json").read_text(encoding="utf-8"))["routes"]
    retriever = g.Retriever()

    out = R11 / "requests" / "p1"
    stats = {name: Counter() for name in ("B", "H_raw", "H_fold", "L_rel", "H_rel")}
    audit_rows = []

    for qid in dev:
        q = questions[qid]
        conv, query = q["sample_id"], q["question"]
        base, _ = retriever.windows(conv, query, all_levels=False)
        base_evidence, _ = retriever.render(base, query)
        lower = lower_pool(retriever, conv, query)
        upper_fold = upper_pool(retriever, folded, conv, query)
        upper_raw = upper_pool(retriever, raw, conv, query)

        groups = {"B": base}
        groups["H_raw"], audit_raw = select(retriever, base, lower, upper_raw, query, allow_upper=True)
        groups["H_fold"], audit_fold = select(retriever, base, lower, upper_fold, query, allow_upper=True)
        groups["L_rel"], audit_l = select(retriever, base, lower, [], query, allow_upper=False)
        groups["H_rel"], audit_h = select(retriever, base, lower, upper_fold, query, allow_upper=True)

        for name, windows in groups.items():
            evidence, _ = retriever.render(windows, query)
            stats[name]["changed"] += int(evidence != base_evidence)
            stats[name]["windows"] += len(windows)
            (out / name).mkdir(parents=True, exist_ok=True)
            (out / name / f"{qid.replace('#', '_')}.json").write_text(
                json.dumps({"question_id": qid, "group": name, "evidence": evidence,
                            "windows": [w["fact_ids"] for w in windows]}, ensure_ascii=False, indent=2),
                encoding="utf-8")

        for name, audit in (("H_raw", audit_raw), ("H_fold", audit_fold), ("L_rel", audit_l), ("H_rel", audit_h)):
            stats[name]["accepted"] += len(audit["accepted"])
            stats[name]["replaced"] += len(audit["replaced"])
            for reason, count in audit["rejected"].items():
                stats[name][f"rej_{reason}"] += count
            for item in audit["accepted"][:2]:
                audit_rows.append({"question_id": qid, "group": name, "decision": "accepted",
                                   "center": item["center"], "fact_ids": item["ids"],
                                   "new_slots": item["new_slots"], "query": query})
            for reason in audit["rejected"]:
                audit_rows.append({"question_id": qid, "group": name, "decision": "rejected",
                                   "reason": reason, "query": query})

    summary = {
        "schema": "r1.1-p1-selector-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "questions": len(dev),
        "groups": {name: dict(counter) for name, counter in stats.items()},
        "selector": "deterministic: support(same conv/time/<=7 facts) + relevance(query token) + marginal(new slot) + replacement loss",
        "model_calls": 0,
    }
    (R11 / "P1_SELECTOR_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# R1.1 P1 共享选择器与五组审计", "",
             f"- 开发题：{len(dev)}；选择器：确定性规则（无模型调用）",
             "", "| 组 | 证据改变 | 窗口总数 | 接受 | 替换 | 主要拒绝原因 |", "|---|---:|---:|---:|---:|---|"]
    for name, counter in stats.items():
        reasons = {k[4:]: v for k, v in counter.items() if k.startswith("rej_")}
        top = max(reasons.items(), key=lambda x: x[1]) if reasons else ("-", 0)
        lines.append(f"| {name} | {counter.get('changed', 0)} | {counter.get('windows', 0)} | "
                     f"{counter.get('accepted', 0)} | {counter.get('replaced', 0)} | {top[0]}={top[1]} |")
    lines += ["", "## 审核抽样（前 40 组决策）", "", "| qid | 组 | 决策 | 中心/原因 | 新增槽位 |", "|---|---|---|---|---|"]
    for row in audit_rows[:40]:
        if row["decision"] == "accepted":
            lines.append(f"| {row['question_id']} | {row['group']} | 接受 | {row['center']} | {len(row['new_slots'])} |")
        else:
            lines.append(f"| {row['question_id']} | {row['group']} | 拒绝 | {row['reason']} | - |")
    (R11 / "P1_SELECTOR_AUDIT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({name: dict(c) for name, c in stats.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
