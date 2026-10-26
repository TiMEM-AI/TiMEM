"""R1 build: shared entity + event mentions + evidence bundles.

Deterministic and question-bank free. Support basis is the derived fact text,
never original quotes (that is R2). Fields are deliberately conservative:
unknown stays unknown, only explicit material is recorded.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")

ROLE_RE = re.compile(r"\b(the user|user|assistant|speaker \d)\b", re.I)
CAP_RE = re.compile(r"\b([A-Z][a-z]{2,})\b")
MODALITY = [
    ("plan", re.compile(r"\b(plan|planning|going to|will|intend|thinking about|hope|want to|would like)\b", re.I)),
    ("invitation", re.compile(r"\b(invite|invited|ask(?:ed)? .* to join|suggest(?:ed)?)\b", re.I)),
    ("habit", re.compile(r"\b(usually|every (?:day|week|month|year)|often|always)\b", re.I)),
]
POLARITY = re.compile(r"\b(not|never|no longer|didn't|don't|doesn't|can't|couldn't|wasn't|weren't)\b", re.I)
VERB_HINT = re.compile(r"\b(\w+(?:ed|ing)|is|was|has|had|said|told|went|got|made|took|gave|bought|met|moved|started|finished|adopted|passed|joined)\b", re.I)
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
STOP_NAMES = {"The", "A", "An", "She", "He", "They", "We", "I", "It", "That", "This", "Her", "His", "Their", "On", "In", "At", "After", "Before"}


def stable(prefix: str, *parts: str) -> str:
    return prefix + hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def extract_mentions(fact: dict) -> list[dict]:
    text = (fact.get("text") or "").strip()
    if not text:
        return []
    mentions = []
    for index, sentence in enumerate(SENT_SPLIT.split(text)[:4]):
        sentence = sentence.strip()
        if not sentence:
            continue
        roles = sorted({m.group(1).lower() for m in ROLE_RE.finditer(sentence)})
        names = sorted({m.group(1) for m in CAP_RE.finditer(sentence) if m.group(1) not in STOP_NAMES})
        subject_refs = roles + names
        verb = VERB_HINT.search(sentence)
        predicate = verb.group(1).lower() if verb else "unknown"
        modality = next((name for name, pattern in MODALITY if pattern.search(sentence)), "actual_or_unknown")
        polarity = "negated" if POLARITY.search(sentence) else "affirmative"
        mentions.append({
            "mention_id": stable("mention:", fact["id"], str(index)),
            "conv": fact["conv"],
            "fact_id": fact["id"],
            "sentence_index": index,
            "subject_refs": subject_refs,
            "predicate": predicate,
            "object_refs": [],
            "participant_roles": roles,
            "modality": modality,
            "polarity": polarity,
            "source_refs": [fact["id"]],
            "observed_at": fact.get("observed_at"),
            "support_basis": "derived_fact",
            "status": "resolved" if subject_refs and predicate != "unknown" else "partial",
        })
    return mentions


def main() -> int:
    store = json.loads((SRC / "store.json").read_text(encoding="utf-8"))
    facts = store["facts"]
    groups = store.get("context_groups", {})

    mentions = {}
    entities = defaultdict(lambda: {"conv": set(), "aliases": set(), "fact_ids": set()})
    fact_mentions = defaultdict(list)

    for fid, fact in facts.items():
        for mention in extract_mentions(fact):
            mentions[mention["mention_id"]] = mention
            fact_mentions[fid].append(mention["mention_id"])
            for ref in mention["subject_refs"]:
                key = (fact["conv"], ref.lower())
                entities[key]["conv"].add(fact["conv"])
                entities[key]["aliases"].add(ref)
                entities[key]["fact_ids"].add(fid)

    entity_out = {}
    for (conv, key), value in entities.items():
        entity_out[stable("entity:", conv, key)] = {
            "entity_id": stable("entity:", conv, key),
            "conv": conv,
            "canonical": sorted(value["aliases"])[0],
            "aliases": sorted(value["aliases"]),
            "fact_ids": sorted(value["fact_ids"]),
        }

    bundles = {}
    for key, fact_ids in groups.items():
        fact_ids = [f for f in fact_ids if f in facts]
        if not fact_ids:
            continue
        conv = facts[fact_ids[0]]["conv"]
        bundles[stable("bundle:", key)] = {
            "bundle_id": stable("bundle:", key),
            "conv": conv,
            "group_key": key,
            "fact_ids": list(fact_ids),
            "mention_ids": sorted(m for fid in fact_ids for m in fact_mentions.get(fid, [])),
            "support_basis": "derived_fact",
        }

    # facts not covered by any context group get their own bundle
    uncovered = [fid for fid in facts if fid not in {x for ids in groups.values() for x in ids}]
    for fid in uncovered:
        bundles[stable("bundle:", "solo", fid)] = {
            "bundle_id": stable("bundle:", "solo", fid),
            "conv": facts[fid]["conv"],
            "group_key": "solo:" + fid,
            "fact_ids": [fid],
            "mention_ids": fact_mentions.get(fid, []),
            "support_basis": "derived_fact",
        }

    stats = {
        "facts": len(facts),
        "mentions": len(mentions),
        "mentions_by_status": dict(Counter(m["status"] for m in mentions.values())),
        "mentions_by_modality": dict(Counter(m["modality"] for m in mentions.values())),
        "mentions_by_polarity": dict(Counter(m["polarity"] for m in mentions.values())),
        "entities": len(entity_out),
        "bundles": len(bundles),
        "facts_without_mentions": sum(1 for fid in facts if not fact_mentions.get(fid)),
        "uncovered_facts": len(uncovered),
    }
    out = {
        "schema": "r1-events-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_store_sha": hashlib.sha256((SRC / "store.json").read_bytes()).hexdigest(),
        "support_basis": "derived_fact",
        "stats": stats,
        "mentions": mentions,
        "entities": entity_out,
        "bundles": bundles,
        "fact_mentions": {k: v for k, v in fact_mentions.items()},
    }
    R1.mkdir(parents=True, exist_ok=True)
    path = R1 / "events_r1.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"\nwritten: {path} ({path.stat().st_size/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
