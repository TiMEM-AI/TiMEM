"""R1.3 stage A: fill the 20-question source audit and the 40-candidate audit.

Retrieval is question-conditioned (entities + relation, time window for date
questions). Statuses follow the plan's vocabulary and every row cites source_id
or fact_id. Rules are deterministic and stated in the output.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
R12 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024")
R13 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_3_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
SOURCE = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\frozen\aml_sources.sqlite")
sys.path.insert(0, str(GROUNDED))
import grounded as g  # noqa: E402

REL = {"recommend", "suggest", "invite", "visit", "work", "adopt", "move", "buy", "meet",
       "travel", "play", "cook", "read", "start", "finish", "join", "make", "take",
       "give", "plan", "happen", "crash", "return", "raise", "share", "have", "go", "get"}
NAME = re.compile(r"\b([A-Z][a-z]{2,})\b")
YEAR = re.compile(r"\b(19|20)\d{2}\b")


def stem(t: str) -> str:
    t = t.lower()
    for s in ("ing", "ed", "s"):
        if t.endswith(s) and len(t) > len(s) + 3:
            return t[:-len(s)]
    return t


def plan(q: dict) -> dict:
    text = q["question"]
    ents = {m.group(1).lower() for m in NAME.finditer(text)} - {"what", "which", "when", "where", "who", "why", "how"}
    rels = {stem(w) for w in re.findall(r"[a-z']+", text.lower()) if stem(w) in {stem(r) for r in REL}}
    return {"entities": ents, "relations": rels, "temporal": bool(re.search(r"\bwhen\b|what (date|day|month|year)", text, re.I))}


def covers(text: str, p: dict) -> tuple[bool, bool]:
    toks = {stem(w) for w in re.findall(r"[a-z']+", text.lower())}
    return bool(p["entities"] & toks), bool(p["relations"] & toks)


def main() -> int:
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    audit20 = json.loads((R12 / "SUPPORT_AUDIT_20.json").read_text(encoding="utf-8"))["rows"]
    facts = json.loads((GROUNDED / "store.json").read_text(encoding="utf-8"))["facts"]
    folded = json.loads((R1 / "routes_r1.json").read_text(encoding="utf-8"))["routes"]
    retriever = g.Retriever()
    con = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out20 = []

    for row in audit20:
        qid = row["question_id"]
        q = questions[qid]
        conv = q["sample_id"]
        p = plan(q)
        base, _ = retriever.windows(conv, q["question"], all_levels=False)
        b_ids = [f for w in base for f in w["fact_ids"]]
        b_set = set(b_ids)
        msgs = con.execute("SELECT id, observed_at, role, content FROM source_turns WHERE user_id LIKE ?",
                           (f"%:{conv}%",)).fetchall()
        s_hits = []
        for m in msgs:
            e, r = covers(m["content"], p)
            if e and (r or not p["relations"]):
                s_hits.append({"source_id": m["id"], "observed_at": m["observed_at"], "text": m["content"][:180]})
        d_hits = []
        for f in facts.values():
            if f["conv"] != conv:
                continue
            e, r = covers(f["text"], p)
            if e and (r or not p["relations"]):
                d_hits.append({"fact_id": f["id"], "observed_at": f["observed_at"],
                               "in_b": f["id"] in b_set, "text": f["text"][:180]})
        c_l = [f for f in d_hits if f["in_b"]] or d_hits[:3]
        c_h = []
        qt = set(g.words(q["question"]))
        for route in folded.values():
            if route["conv"] != conv:
                continue
            alias = set()
            for a in route.get("aliases", [])[:3]:
                alias |= set(g.words(a))
            if not (qt & alias):
                continue
            for t in route["target_fact_ids"]:
                if t in b_set or t not in facts:
                    continue
                c_h.append({"fact_id": t, "levels": route["provenance_levels"], "text": facts[t]["text"][:160]})
                break
            if len(c_h) >= 3:
                break
        s_state = "complete" if s_hits else "not_found_after_review"
        d_state = "complete" if d_hits else "not_found_after_review"
        b_state = "complete" if any(h["in_b"] for h in d_hits) else "partial"
        uncertain = s_state == "not_found_after_review" or d_state == "not_found_after_review"
        out20.append({
            "question_id": qid, "category": row["category"], "question": q["question"], "gold": q.get("gold"),
            "plan": {"entities": sorted(p["entities"]), "relations": sorted(p["relations"]), "temporal": p["temporal"]},
            "S_source": {"state": s_state, "hits": s_hits[:3]},
            "D_derived": {"state": d_state, "hits": d_hits[:3]},
            "C_L": {"state": "complete" if c_l else "not_found_after_review", "hits": c_l[:3]},
            "C_H": {"state": "complete" if c_h else "not_found_after_review", "hits": c_h},
            "B_evidence": {"state": b_state, "fact_ids": [h["fact_id"] for h in d_hits if h["in_b"]][:5]},
            "answer_judge": "not_run",
            "overall": "uncertain" if uncertain else "complete",
        })
    con.close()
    (R13 / "SEMANTIC_AUDIT_20_COMPLETED.json").write_text(json.dumps({
        "schema": "r1.3-semantic-audit-20-v1",
        "rules": "question entities + relation stem; S/D hit requires both; C_H from top folded routes with B-external target; B from hit.in_b",
        "questions": out20}, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# R1.3 二十题来源支持审计（问题条件检索）", "",
             "规则：以问题实体 + 关系词为检索条件；S/D 需同时命中；C_H 取折叠路由中 B 外目标；B 取命中事实是否在共同 B。", "",
             "| qid | 类别 | S | D | C_L | C_H | B | 结论 |", "|---|---|---|---|---|---|---|---|"]
    for r in out20:
        lines.append(f"| {r['question_id']} | {r['category']} | {r['S_source']['state']} | {r['D_derived']['state']} | "
                     f"{r['C_L']['state']} | {r['C_H']['state']} | {r['B_evidence']['state']} | {r['overall']} |")
    (R13 / "SEMANTIC_AUDIT_20.md").write_text("\n".join(lines), encoding="utf-8")

    # 40 candidate audit
    sheet = json.loads((R12 / "CANDIDATE_AUDIT_40.json").read_text(encoding="utf-8"))["sheet"]
    rows = []
    for item in sheet:
        q = questions[item["question_id"]]
        p = plan(q)
        text = item["candidate_text"]
        e, r = covers(text, p)
        if e and r:
            cat = "related_new"
        elif e:
            cat = "needs_context"
        elif r:
            cat = "binding_uncertain"
        else:
            cat = "unrelated"
        rows.append({**item, "auto_class": cat, "auto_entities": e, "auto_relation": r})
    from collections import Counter
    (R13 / "CANDIDATE_AUDIT_40_COMPLETED.json").write_text(json.dumps({
        "schema": "r1.3-candidate-audit-40-v1",
        "rules": "entity hit + relation hit => related_new; entity only => needs_context; relation only => binding_uncertain; neither => unrelated",
        "distribution": dict(Counter(r["auto_class"] for r in rows)), "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"audit20": dict(Counter(r["overall"] for r in out20)),
                      "audit40": dict(Counter(r["auto_class"] for r in rows))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
