"""Offline checks for the one-hop entity expansion (no network, no LLM)."""
import asyncio
import sys

sys.path.insert(0, r"F:\worktable\research\timem-aml")

from server.search_expand import extract_entities, search_with_expansion

FIRST = [
    {"id": "a1", "level": "L1", "score": 0.9, "content": "Dr. Stockmann told Hovstad that the baths are polluted."},
    {"id": "a2", "level": "L1", "score": 0.8, "content": "Hovstad printed the article in the People's Messenger."},
    {"id": "a3", "level": "L2", "score": 0.7, "content": "Billing argued with Peter Stockmann about the vote."},
    {"id": "a4", "level": "L1", "score": 0.6, "content": "The Mayor warned Stockmann about the report."},
    {"id": "a5", "level": "L3", "score": 0.5, "content": "The town meeting turned hostile."},
]

SECOND = [
    {"id": "a2", "level": "L1", "score": 0.95, "content": "duplicate of a2"},          # dedupe
    {"id": "b1", "level": "L1", "score": 0.4, "content": "Hovstad met Aslaksen at the print shop."},
    {"id": "b2", "level": "L2", "score": 0.99, "content": "Stockmann's family history."},
    {"id": "b3", "level": "L1", "score": 0.3, "content": "Billing mocked the Mayor's speech."},
]


class StubService:
    def __init__(self):
        self.calls = []

    async def search(self, user_id, query, top_k, memory_model=None, **kwargs):
        self.calls.append({"query": query, "top_k": top_k})
        return list(FIRST if len(self.calls) == 1 else SECOND)


def main() -> None:
    ents = extract_entities(FIRST, 4)
    print("entities:", ents)
    assert "Stockmann" in " ".join(ents).lower() or any("Stockmann" in e for e in ents), ents
    assert not any(e in {"The", "Dr"} for e in ents), ents

    svc = StubService()
    out = asyncio.run(search_with_expansion(svc, "u1", "Who polluted the baths?", 8))

    print("calls:", len(svc.calls))
    for c in svc.calls:
        print("   ", c["top_k"], "|", c["query"][:70])

    assert len(svc.calls) == 2, "must be exactly one extra hop"
    assert any(any(e in c["query"] for e in ents) for c in svc.calls[1:]), "second hop must use entities"

    ids = [r["id"] for r in out]
    print("merged ids:", ids)
    assert len(ids) == len(set(ids)), "ids must be unique"
    assert len(out) <= 8, "must respect top_k cap"
    assert "b1" in ids and "b3" in ids, "missing L1 supplements should be added"

    # cap check: with top_k=6 the first hop already fills it, no overflow
    svc3 = StubService()
    out3 = asyncio.run(search_with_expansion(svc3, "u1", "q", 6))
    print("cap check ->", len(out3), "rows")
    assert len(out3) <= 6, len(out3)

    # first hop only when expansion is disabled
    svc2 = StubService()
    out2 = asyncio.run(search_with_expansion(svc2, "u1", "q", 100, enabled=False))
    assert len(svc2.calls) == 1 and len(out2) == len(FIRST)
    print("disabled ->", len(out2), "rows,", len(svc2.calls), "call")

    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()

