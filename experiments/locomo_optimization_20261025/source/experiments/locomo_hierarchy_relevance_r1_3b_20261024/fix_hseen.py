"""Fix h_seen scope and BM25 ordering in r13b_prepare.build_pools, then re-prepare."""
import io
import re
from pathlib import Path

p = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_3b_20261024\r13b_prepare.py")
s = p.read_text(encoding="utf-8")

start = s.index("def build_pools(")
end = s.index("def serialize_window(")
new_fn = '''def build_pools(conv, query, base, facts, folded, retriever):
    """L: BM25-ordered lower windows. H: 8 lower + up to 4 upper reached beyond L's
    selected windows; seen only covers B and the lower windows H actually selected."""
    base_facts = {f for w in base for f in w["fact_ids"]}
    qt = set(g.words(query))
    lower, seen = [], set(base_facts)
    for hit in retriever.search(conv, query, "fact"):
        fid = hit["id"]
        if fid in seen or fid not in facts:
            continue
        if not (qt & set(g.words(facts[fid]["text"]))):
            continue
        seen.add(fid)
        lower.append({"id": fid, "text": facts[fid]["text"], "observed_at": facts[fid]["observed_at"]})
    L = lower[:L_SLOTS]

    h_low = L[:H_LOW]
    h_seen = set(base_facts) | {item["id"] for item in h_low}
    scored_routes = []
    for route in folded.values():
        if route["conv"] != conv:
            continue
        alias = set()
        for a in route.get("aliases", [])[:3]:
            alias |= set(g.words(a))
        score = len(qt & alias)
        if score:
            scored_routes.append((score, route))
    scored_routes.sort(key=lambda x: (-x[0], x[1]["route_id"]))
    upper = []
    for score, route in scored_routes:
        for target in route["target_fact_ids"]:
            if target in h_seen or target not in facts:
                continue
            h_seen.add(target)
            upper.append({"id": target, "text": facts[target]["text"],
                          "observed_at": facts[target]["observed_at"],
                          "levels": route["provenance_levels"], "route_score": score})
            break
        if len(upper) >= H_UP:
            break

    H = h_low + upper[:H_UP]
    if len(H) < L_SLOTS:
        used = {item["id"] for item in H}
        H = H + [item for item in lower if item["id"] not in used][: L_SLOTS - len(H)]
    return L, H, lower, upper


'''
s = s[:start] + new_fn + s[end:]
s = s.replace("L, H, lower, upper = build_pools(conv, query, base, facts, folded)",
              "L, H, lower, upper = build_pools(conv, query, base, facts, folded, retriever)")
p.write_text(s, encoding="utf-8")
print("build_pools fixed:", "retriever.search(conv, query" in s and "h_low" in s)
