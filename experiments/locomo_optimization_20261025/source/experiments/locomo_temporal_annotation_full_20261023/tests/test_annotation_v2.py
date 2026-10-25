"""C3 arm tests: original 17 spec cases plus the boundary fixes required by the plan."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ARMS = Path(__file__).resolve().parents[1] / "arms"
sys.path.insert(0, str(ARMS))

from arm_c3 import MARKER, SourceTurn, annotate, parse_mentions, plan_date_query  # noqa: E402


def turn(observed: str, content: str, sid: str = "src:a", user: str = "u1") -> SourceTurn:
    return SourceTurn(source_id=sid, user_id=user, role="user", content=content, observed_at=observed)


def resolved(observed: str, content: str):
    return [(m.value, m.precision) for m in parse_mentions(turn(observed, content)) if m.status == "resolved"]


def row(source_ids, content="Original conversation evidence:\n[2023-05-25T05:14:00+00:00] user: x"):
    return {"id": "m1", "content": content, "score": -1.0, "created_at": "2023-05-25T05:14:00+00:00", "source_ids": list(source_ids)}


class OriginalSpecCases(unittest.TestCase):
    CASES = [
        ("2024-03-01T10:00:00+00:00", "I went yesterday.", [("2024-02-29", "day")]),
        ("2023-03-01T10:00:00+00:00", "I went yesterday.", [("2023-02-28", "day")]),
        ("2023-12-31T12:00:00+00:00", "next month and next year", [("2024-01", "month"), ("2024", "year")]),
        ("2023-01-01T09:00:00+00:00", "I went yesterday.", [("2022-12-31", "day")]),
        ("2023-07-12T09:00:00+00:00", "that was two days ago", [("2023-07-10", "day")]),
        ("2023-05-10T00:00:00+00:00", "day before yesterday was busy", [("2023-05-08", "day")]),
        ("2022-07-09T00:00:00+00:00", "see you day after tomorrow", [("2022-07-11", "day")]),
        ("2023-10-23T06:00:00+00:00", "last month was hard", [("2023-09", "month")]),
        ("2023-05-25T05:14:00+00:00", "going camping next month", [("2023-06", "month")]),
        ("2023-05-25T05:14:00+00:00", "last year I lived in Sweden", [("2022", "year")]),
        ("2023-05-25T05:14:00+00:00", "a few years ago I lived there", []),
        ("2023-05-25T05:14:00+00:00", "early next month maybe", []),
        ("2023-05-25T05:14:00+00:00", "I finished it in two days", []),
        ("2023-05-25T05:14:00+00:00", "last Friday was fun", []),
        ("2023-05-25T05:14:00+00:00", 'she said "I went yesterday"', []),
        ("2023-05-25T05:14:00+00:00", "on 2023-05-20 I went yesterday", []),
        ("", "yesterday", []),
    ]

    def test_cases(self) -> None:
        for observed, content, expected in self.CASES:
            self.assertEqual(resolved(observed, content), expected, content)


class CompoundFixes(unittest.TestCase):
    def test_compound_relative_not_partially_parsed(self) -> None:
        for content in [
            "two days before yesterday was rough",
            "three days after tomorrow works",
            "twenty one days ago we met",
        ]:
            self.assertEqual(resolved("2023-05-25T00:00:00+00:00", content), [], content)

    def test_hedge_does_not_swallow_later_phrase(self) -> None:
        text = "early next month, after yesterday's meeting"
        values = resolved("2023-05-25T00:00:00+00:00", text)
        self.assertNotIn(("2023-06", "month"), values)      # hedged phrase blocked
        self.assertIn(("2023-05-24", "day"), values)         # later phrase still parsed

    def test_long_quote_does_not_leak(self) -> None:
        quoted = '"' + ("filler words " * 40) + 'I went yesterday."'
        self.assertEqual(resolved("2023-05-25T00:00:00+00:00", quoted), [])

    def test_ambiguity_keeps_phrase_precision(self) -> None:
        mentions = parse_mentions(turn("2023-05-25T00:00:00+00:00", "last month, on 2023-04-10, we moved"))
        target = next(m for m in mentions if m.raw_phrase.lower() == "last month")
        self.assertEqual(target.status, "ambiguous")
        self.assertEqual(target.precision, "month")
        self.assertIsNone(target.value)
        self.assertEqual(target.skip_reason, "absolute_date_cooccurrence")

    def test_compound_ambiguity_single_span(self) -> None:
        mentions = parse_mentions(turn("2023-05-25T00:00:00+00:00", "day before yesterday, on 2023-05-20"))
        phrases = [m.raw_phrase.lower() for m in mentions]
        self.assertEqual(phrases, ["day before yesterday"])

    def test_question_unit_is_request_only(self) -> None:
        plan = plan_date_query("What year did the month-long workshop happen?")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.requested_unit, "year")
        # the phrase's own precision is unaffected
        self.assertEqual(resolved("2023-05-25T00:00:00+00:00", "it happened last month"), [("2023-04", "month")])


class RenderingTests(unittest.TestCase):
    def test_span_matches_raw_content_and_marker(self) -> None:
        turns = {"src:a": turn("2023-05-25T05:14:00+00:00", "We're thinking about going camping next month")}
        rows = [row(["src:a"])]
        result = annotate(rows, turns, "When is Melanie going camping?")
        self.assertTrue(result.changed)
        self.assertIn(MARKER, result.rows[0]["content"])
        self.assertIn("source_sentence=", result.rows[0]["content"])
        self.assertIn("value=2023-06", result.rows[0]["content"])
        entry = next(t for t in result.trace if t["status"] == "resolved")
        content = turns["src:a"].content
        start, end = entry["span"]
        self.assertEqual(content[start:end], entry["raw_phrase"])

    def test_unicode_spans(self) -> None:
        content = "我们昨天在杭州开会，然后 yesterday 去了上海🎉"
        turns = {"src:a": turn("2023-05-25T00:00:00+00:00", content)}
        result = annotate([row(["src:a"])], turns, "When did they meet?")
        entry = next(t for t in result.trace if t["status"] == "resolved")
        start, end = entry["span"]
        self.assertEqual(content[start:end], entry["raw_phrase"])

    def test_idempotent_via_marker(self) -> None:
        turns = {"src:a": turn("2023-05-25T05:14:00+00:00", "camping next month")}
        first = annotate([row(["src:a"])], turns, "When is the trip?")
        second = annotate(first.rows, turns, "When is the trip?")
        self.assertFalse(second.changed)
        self.assertEqual(first.rows[0]["content"].count(MARKER), 1)

    def test_dedupe_across_rows(self) -> None:
        turns = {"src:a": turn("2023-05-25T05:14:00+00:00", "camping next month")}
        rows = [row(["src:a"]), row(["src:a"])]
        result = annotate(rows, turns, "When is the trip?")
        self.assertEqual(result.rows[0]["content"].count(MARKER), 1)
        self.assertEqual(result.rows[1]["content"].count(MARKER), 0)

    def test_budget_fallback(self) -> None:
        turns = {"src:a": turn("2023-05-25T05:14:00+00:00", "camping next month")}
        result = annotate([row(["src:a"])], turns, "When is the trip?", budget_chars=10)
        self.assertFalse(result.changed)
        self.assertTrue(result.budget_exceeded)

    def test_long_sentence_not_rendered(self) -> None:
        content = ("padding " * 60) + "camping next month"
        turns = {"src:a": turn("2023-05-25T05:14:00+00:00", content)}
        result = annotate([row(["src:a"])], turns, "When is the trip?")
        self.assertFalse(result.changed)
        entry = next(t for t in result.trace if t["status"] == "resolved")
        self.assertFalse(entry["rendered"])
        self.assertEqual(entry["render_skip_reason"], "source_sentence_too_long")

    def test_foreign_source_ignored(self) -> None:
        result = annotate([row(["src:other"])], {"src:a": turn("2023-05-25T00:00:00+00:00", "next month")}, "When?")
        self.assertFalse(result.changed)

    def test_two_anchors_not_shared(self) -> None:
        turns = {
            "src:a": turn("2023-05-08T00:00:00+00:00", "next month we travel", sid="src:a"),
            "src:b": turn("2023-06-25T00:00:00+00:00", "next month we travel", sid="src:b"),
        }
        result = annotate([row(["src:a", "src:b"])], turns, "When do we travel?")
        values = {t["source_id"]: t["value"] for t in result.trace if t["status"] == "resolved"}
        self.assertEqual(values["src:a"], "2023-06")
        self.assertEqual(values["src:b"], "2023-07")


if __name__ == "__main__":
    unittest.main(verbosity=2)
