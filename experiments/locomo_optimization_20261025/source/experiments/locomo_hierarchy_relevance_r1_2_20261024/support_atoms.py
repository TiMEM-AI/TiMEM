"""R1.2 support atoms and the support-set selector (offline, no model calls).

A support atom is a structured index over a derived fact:
  (subject_refs, relation, answer_values, observed_at, fact_id)
Selection compares support sets, not type labels:
  gain = support(candidate) - support(selected)
  loss = support(victim) - support(selected - victim + candidate)
Accept only when gain is non-empty and loss is empty.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

STOP = set("the a an and or of to in on at for from with by is are was were be been it its this that these those i you he she they we my your his her their our said says told".split())
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")
NAME = re.compile(r"\b([A-Z][a-z]{2,})\b")
YEAR = re.compile(r"\b(19|20)\d{2}\b")
NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
RELATIONS = {
    "recommend", "recommended", "suggest", "invite", "invited", "visit", "visited", "work", "worked",
    "adopt", "adopted", "move", "moved", "buy", "bought", "meet", "met", "travel", "travelled",
    "play", "played", "cook", "cooked", "read", "start", "started", "finish", "finished", "join",
    "joined", "make", "made", "take", "took", "give", "gave", "plan", "planned", "happen", "happened",
    "crash", "crashed", "return", "returned", "raise", "raised", "share", "shared", "have", "has", "had",
}
BANAL = {"user", "assistant", "memory", "derived", "summary", "said", "says", "told"}


def tokens(text: str) -> list[str]:
    return [t.lower() for t in TOKEN.findall(text or "") if t.lower() not in STOP and len(t) > 1]


@dataclass(frozen=True)
class SupportAtom:
    fact_id: str
    conv: str
    observed_at: str
    subjects: tuple[str, ...]
    relation: str
    values: tuple[str, ...]
    kind: str  # entity | value | time

    def key(self) -> tuple:
        return (self.kind, self.relation, self.subjects, self.values)


def build_atoms(fact: dict) -> list[SupportAtom]:
    text = fact.get("text") or ""
    low = text.lower()
    toks = tokens(text)
    subjects = tuple(sorted({m.group(1).lower() for m in NAME.finditer(text)} - BANAL))
    relation = next((t for t in toks if t in RELATIONS), "unknown")
    atoms = []
    # entity atoms
    for name in subjects:
        atoms.append(SupportAtom(fact["id"], fact["conv"], fact["observed_at"], subjects, "entity", (name,), "entity"))
    # value atoms: numbers, years and capitalised multiword values after the relation
    values = set(NUMBER.findall(text)) | set(YEAR.findall(text))
    for phrase in re.findall(r"\b(?:[A-Z][A-Za-z0-9]+(?:\s+[A-Z0-9][A-Za-z0-9]*)*)\b", text):
        p = phrase.strip().lower()
        if len(p) > 2 and p not in BANAL and not p.isdigit():
            values.add(p)
    for value in sorted(values):
        atoms.append(SupportAtom(fact["id"], fact["conv"], fact["observed_at"], subjects, relation, (value,), "value"))
    # time atom
    if YEAR.search(text) or re.search(r"\b\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)", low):
        atoms.append(SupportAtom(fact["id"], fact["conv"], fact["observed_at"], subjects, relation, ("time",), "time"))
    return atoms


def support_set(facts: list[dict]) -> set:
    out = set()
    for fact in facts:
        for atom in build_atoms(fact):
            out.add(atom.key())
    return out


def select_with_support(base_windows, candidates, facts, max_windows=24, protected=20):
    """Support-set selection with transactional replacement."""
    selected = [dict(w) for w in base_windows]
    fact_by_id = {f["id"]: f for f in facts}

    def facts_of(windows):
        return [fact_by_id[f] for w in windows for f in w["fact_ids"] if f in fact_by_id]

    audit = {"accepted": [], "replaced": [], "rejected": []}
    for cand in candidates:
        ids = [x for x in cand["fact_ids"] if x in fact_by_id]
        if not ids:
            audit["rejected"].append(("no_fact", cand.get("center")))
            continue
        cand_support = support_set([fact_by_id[x] for x in ids])
        covered = support_set(facts_of(selected))
        gain = cand_support - covered
        if not gain:
            audit["rejected"].append(("no_gain", cand.get("center")))
            continue
        if len(selected) < max_windows:
            selected.append({"center": cand.get("center"), "fact_ids": ids, "channel": cand.get("channel", "support")})
            audit["accepted"].append({"center": cand.get("center"), "ids": ids, "gain": len(gain)})
            continue
        placed = False
        for idx in range(len(selected) - 1, protected - 1, -1):
            victim = selected[idx]
            others = [w for i, w in enumerate(selected) if i != idx]
            victim_support = support_set([fact_by_id[x] for x in victim["fact_ids"] if x in fact_by_id])
            after_support = support_set(facts_of(others)) | cand_support
            loss = victim_support - after_support
            if loss:
                continue
            selected[idx] = {"center": cand.get("center"), "fact_ids": ids, "channel": cand.get("channel", "support")}
            audit["replaced"].append({"removed": victim["fact_ids"], "added": ids, "gain": len(gain)})
            audit["accepted"].append({"center": cand.get("center"), "ids": ids, "gain": len(gain)})
            placed = True
            break
        if not placed:
            audit["rejected"].append(("no_safe_replacement", cand.get("center")))
    return selected, audit
