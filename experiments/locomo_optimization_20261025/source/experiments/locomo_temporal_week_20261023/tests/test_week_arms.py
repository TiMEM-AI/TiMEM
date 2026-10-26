"""Tests for W0/W1 week anchoring (NEXT_ADJUSTMENT.md section 11)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "arms"))
sys.path.insert(0, str(ROOT / "frozen"))

from week_ext import add_week_anchors, parse_week_mentions  # noqa: E402


def turn(observed: str, content: str, sid: str = "src:a") -> dict:
    return {"source_id": sid, "user_id": "u1", "role": "user", "content": content, "observed_at": observed}


def row(content: str, source_ids=("src:a",)) -> dict:
    return {"id": "m1", "content": content, "created_at": "2023-01-01T00:00:00+00:00", "source_ids": list(source_ids)}


BASE = "Original conversation evidence:\n[2023-03-27T05:10:00+00:00] user: text"


class ParseTests(unittest.TestCase):
    def test_last_week_anchor(self) -> None:
        mentions = parse_week_mentions(turn("2023-03-27T05:10:00+00:00", "I started a new job last week."), with_range=False)
        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0].anchored_value, "the week before March 27, 2023")
        self.assertEqual(mentions[0].precision, "week")
        self.assertIsNone(mentions[0].calendar_range)

    def test_last_weekend_anchor(self) -> None:
        mentions = parse_week_mentions(turn("2023-10-17T05:10:00+00:00", "we went to ER last weekend"), with_range=False)
        self.assertEqual(mentions[0].anchored_value, "the weekend before October 17, 2023")
        self.assertEqual(mentions[0].precision, "weekend")

    def test_ranges(self) -> None:
        monday = parse_week_mentions(turn("2023-03-27T00:00:00+00:00", "last week"), with_range=True)[0]
        self.assertEqual(monday.calendar_range, "2023-03-20/2023-03-26")
        sunday = parse_week_mentions(turn("2023-01-01T00:00:00+00:00", "last week"), with_range=True)[0]
        self.assertEqual(sunday.calendar_range, "2022-12-19/2022-12-25")

    def test_weekend_range_only_on_weekdays(self) -> None:
        friday = parse_week_mentions(turn("2023-10-20T00:00:00+00:00", "last weekend"), with_range=True)[0]
        self.assertEqual(friday.calendar_range, "2023-10-14/2023-10-15")
        saturday = parse_week_mentions(turn("2023-10-21T00:00:00+00:00", "last weekend"), with_range=True)[0]
        self.assertIsNone(saturday.calendar_range)

    def test_blocked_phrases(self) -> None:
        for content in [
            "the last week of May was busy",
            "the week before last we met",
            'she said "last weekend we went"',
        ]:
            self.assertEqual(parse_week_mentions(turn("2023-03-27T00:00:00+00:00", content), with_range=False), [], content)

    def test_missing_time(self) -> None:
        self.assertEqual(parse_week_mentions(turn("", "last week"), with_range=False), [])


class RenderTests(unittest.TestCase):
    def test_appends_after_c1_prefix(self) -> None:
        c1 = [row(BASE + "\n\nCalendar annotations derived from the original turns:\n- source_id=src:a; raw_phrase=\"yesterday\"; precision=day")]
        original = [row(BASE)]
        turns = {"src:a": turn("2023-03-27T05:10:00+00:00", "I started last week")}
        out, trace, changed = add_week_anchors(c1, original, turns, with_range=False)
        self.assertTrue(changed)
        self.assertTrue(out[0]["content"].startswith(c1[0]["content"]))  # C1 is an immutable prefix
        self.assertIn('anchored_value="the week before March 27, 2023"', out[0]["content"])

    def test_no_absolute_range_in_w0(self) -> None:
        c1 = [row(BASE)]
        turns = {"src:a": turn("2023-03-27T05:10:00+00:00", "last week")}
        out, _trace, _ = add_week_anchors(c1, [row(BASE)], turns, with_range=False)
        self.assertNotIn("calendar_range=", out[0]["content"])

    def test_w1_keeps_week_not_single_day(self) -> None:
        c1 = [row(BASE)]
        turns = {"src:a": turn("2023-03-27T05:10:00+00:00", "last week")}
        out, _trace, _ = add_week_anchors(c1, [row(BASE)], turns, with_range=True)
        self.assertIn("calendar_range=2023-03-20/2023-03-26", out[0]["content"])

    def test_budget_drops_week_only(self) -> None:
        c1_text = BASE + "\n\nCalendar annotations derived from the original turns:\n- x" + ("y" * 7900)
        c1 = [row(c1_text)]
        turns = {"src:a": turn("2023-03-27T05:10:00+00:00", "last week")}
        out, trace, changed = add_week_anchors(c1, [row(BASE)], turns, with_range=False)
        self.assertFalse(changed)
        self.assertEqual(out[0]["content"], c1_text)
        self.assertEqual(trace[0]["render_skip_reason"], "annotation_budget_exceeded")

    def test_untouched_when_no_phrase(self) -> None:
        c1 = [row(BASE)]
        turns = {"src:a": turn("2023-03-27T05:10:00+00:00", "nothing temporal")}
        out, _trace, changed = add_week_anchors(c1, [row(BASE)], turns, with_range=False)
        self.assertFalse(changed)
        self.assertEqual(out[0]["content"], c1[0]["content"])

    def test_idempotent(self) -> None:
        c1 = [row(BASE)]
        turns = {"src:a": turn("2023-03-27T05:10:00+00:00", "last week")}
        first, _t1, _ = add_week_anchors(c1, [row(BASE)], turns, with_range=False)
        second, _t2, changed = add_week_anchors(first, [row(BASE)], turns, with_range=False)
        self.assertFalse(changed)
        self.assertEqual(second[0]["content"].count("anchor_last_week"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
