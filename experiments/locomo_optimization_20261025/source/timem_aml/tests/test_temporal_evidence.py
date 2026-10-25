"""A0 unit tests: routing, calendar parsing, rendering, isolation, idempotency."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from server.temporal_evidence import (  # noqa: E402
    ANNOTATION_HEADER,
    SourceTurn,
    parse_time_mentions,
    plan_date_query,
    render_date_evidence,
)


def turn(observed_at: str, content: str, source_id: str = "src:a", user_id: str = "u1") -> SourceTurn:
    return SourceTurn(source_id=source_id, user_id=user_id, role="user", content=content, observed_at=observed_at)


def resolved(content: str, observed_at: str) -> list:
    return [m for m in parse_time_mentions(turn(observed_at, content)) if m.status == "resolved"]


def legacy_row(source_ids: list[str], content: str = "Original conversation evidence:\n[2023-05-25T12:00:00+00:00] user: text") -> dict:
    return {"id": "m1", "content": content, "score": -1.0, "created_at": "2023-05-25T12:00:00+00:00", "source_ids": source_ids}


class RoutingTests(unittest.TestCase):
    def test_accepts_date_main_questions(self) -> None:
        for query in [
            "When did Caroline go to the LGBTQ conference?",
            "When is Melanie planning on going camping?",
            "Which month did they move?",
            "What day did the concert happen?",
            "What year was the trip?",
        ]:
            self.assertIsNotNone(plan_date_query(query), query)

    def test_rejects_non_date_intents(self) -> None:
        for query in [
            "What is the first place she visited?",
            "When he was younger, what did he enjoy?",
            "How long did they stay in Tokyo?",
            "What happened after the roadtrip?",
            "Who did she meet before the trip?",
            "What day of the week was it?",
            "When did she finish her second race?",
            "Which was the earliest trip?",
        ]:
            self.assertIsNone(plan_date_query(query), query)

    def test_empty_or_huge_query_rejected(self) -> None:
        self.assertIsNone(plan_date_query(""))
        self.assertIsNone(plan_date_query("When did " + "x" * 400 + "?"))


class ParsingTests(unittest.TestCase):
    def test_yesterday_calendar_edges(self) -> None:
        self.assertEqual(resolved("I went yesterday.", "2024-03-01T10:00:00+00:00")[0].normalized_value, "2024-02-29")
        self.assertEqual(resolved("I went yesterday.", "2023-03-01T10:00:00+00:00")[0].normalized_value, "2023-02-28")
        self.assertEqual(resolved("I went yesterday.", "2023-01-01T10:00:00+00:00")[0].normalized_value, "2022-12-31")

    def test_day_before_yesterday_and_day_after_tomorrow(self) -> None:
        self.assertEqual(resolved("day before yesterday was busy", "2023-05-10T00:00:00+00:00")[0].normalized_value, "2023-05-08")
        self.assertEqual(resolved("see you day after tomorrow", "2022-07-09T00:00:00+00:00")[0].normalized_value, "2022-07-11")

    def test_month_and_year_rollover(self) -> None:
        may = resolved("going camping next month", "2023-05-25T12:00:00+00:00")[0]
        self.assertEqual((may.normalized_value, may.precision), ("2023-06", "month"))
        december = resolved("next month and next year", "2023-12-31T12:00:00+00:00")
        self.assertEqual(december[0].normalized_value, "2024-01")
        self.assertEqual(december[1].normalized_value, "2024")

    def test_n_days_ago(self) -> None:
        self.assertEqual(resolved("that was two days ago", "2023-07-12T09:00:00+00:00")[0].normalized_value, "2023-07-10")
        self.assertEqual(resolved("that was 3 days ago", "2023-07-12T09:00:00+00:00")[0].normalized_value, "2023-07-09")

    def test_each_turn_uses_its_own_anchor(self) -> None:
        a = parse_time_mentions(turn("2023-05-08T12:00:00+00:00", "next month we travel", source_id="src:a"))
        b = parse_time_mentions(turn("2023-05-25T12:00:00+00:00", "next month we travel", source_id="src:b"))
        self.assertEqual(a[0].normalized_value, "2023-06")
        self.assertEqual(b[0].normalized_value, "2023-06")
        self.assertEqual((a[0].source_id, b[0].source_id), ("src:a", "src:b"))

    def test_unsupported_phrases_are_not_annotated(self) -> None:
        for content in [
            "a few years ago I lived there",
            "last week was hard",
            "next Sunday works",
            "we met last weekend",
            "it was summer",
            "I finished it in two days",
            "three days after the trip",
        ]:
            self.assertEqual(resolved(content, "2023-05-25T12:00:00+00:00"), [], content)

    def test_hedged_phrases_are_blocked_whole(self) -> None:
        self.assertEqual(resolved("early next month maybe", "2023-05-25T12:00:00+00:00"), [])
        self.assertEqual(resolved("about two days ago", "2023-05-25T12:00:00+00:00"), [])

    def test_quotes_are_skipped(self) -> None:
        self.assertEqual(resolved('she said "I went yesterday"', "2023-05-25T12:00:00+00:00"), [])

    def test_missing_or_invalid_observed_at(self) -> None:
        self.assertEqual(parse_time_mentions(turn("", "yesterday")), ())
        self.assertEqual(parse_time_mentions(turn("not-a-date", "yesterday")), ())
        self.assertEqual(parse_time_mentions(turn("2023-05-25T12:00:00", "yesterday")), ())

    def test_absolute_date_in_sentence_blocks_annotation(self) -> None:
        self.assertEqual(resolved("on 2023-05-20 I went yesterday", "2023-05-25T12:00:00+00:00"), [])


class RenderingTests(unittest.TestCase):
    def test_appends_block_and_preserves_original(self) -> None:
        plan = plan_date_query("When is Melanie going camping?")
        original = "Original conversation evidence:\n[2023-05-25T12:00:00+00:00] assistant: We're going camping next month."
        rows = [legacy_row(["src:a"], original)]
        turns = {"src:a": turn("2023-05-25T12:00:00+00:00", "We're going camping next month.")}
        result = render_date_evidence(plan, rows, turns)
        self.assertTrue(result.changed)
        self.assertIn(original, result.rows[0]["content"])
        self.assertIn(ANNOTATION_HEADER, result.rows[0]["content"])
        self.assertIn("calendar_value=2023-06", result.rows[0]["content"])
        self.assertIn("precision=month", result.rows[0]["content"])
        self.assertEqual(result.rows[0]["id"], "m1")

    def test_idempotent(self) -> None:
        plan = plan_date_query("When is Melanie going camping?")
        rows = [legacy_row(["src:a"])]
        turns = {"src:a": turn("2023-05-25T12:00:00+00:00", "camping next month")}
        first = render_date_evidence(plan, rows, turns)
        second = render_date_evidence(plan, first.rows, turns)
        self.assertFalse(second.changed)
        self.assertEqual(first.rows[0]["content"].count(ANNOTATION_HEADER), 1)

    def test_summary_rows_untouched(self) -> None:
        plan = plan_date_query("When did it happen?")
        rows = [{"id": "m2", "content": "Derived memory summary:\nnext month", "source_ids": ["src:a"]}]
        result = render_date_evidence(plan, rows, {"src:a": turn("2023-05-25T12:00:00+00:00", "next month")})
        self.assertFalse(result.changed)
        self.assertEqual(result.rows[0]["content"], "Derived memory summary:\nnext month")

    def test_no_plan_or_no_turns_is_noop(self) -> None:
        rows = [legacy_row(["src:a"])]
        self.assertFalse(render_date_evidence(None, rows, {}).changed)
        self.assertFalse(render_date_evidence(plan_date_query("How long did it take?"), rows, {}).changed)
        self.assertFalse(render_date_evidence(plan_date_query("When did it happen?"), rows, {}).changed)

    def test_foreign_source_ids_are_ignored(self) -> None:
        plan = plan_date_query("When did it happen?")
        rows = [legacy_row(["src:other"])]
        turns = {"src:a": turn("2023-05-25T12:00:00+00:00", "next month")}
        self.assertFalse(render_date_evidence(plan, rows, turns).changed)

    def test_budget_exceeded_falls_back(self) -> None:
        plan = plan_date_query("When did it happen?")
        rows = [legacy_row(["src:a"])]
        turns = {"src:a": turn("2023-05-25T12:00:00+00:00", "next month")}
        result = render_date_evidence(plan, rows, turns, budget_chars=10)
        self.assertFalse(result.changed)
        self.assertTrue(result.budget_exceeded)


class IsolationAndSwitchTests(unittest.TestCase):
    def _db(self, path: Path) -> None:
        con = sqlite3.connect(path)
        con.executescript(
            """
            CREATE TABLE source_turns(seq INTEGER PRIMARY KEY, id TEXT UNIQUE, user_id TEXT,
              session_id TEXT, request_id TEXT, position INTEGER, observed_at TEXT, role TEXT, content TEXT);
            INSERT INTO source_turns VALUES(1,'src:a','u1','s','r',0,'2023-05-25T12:00:00+00:00','user','mine');
            INSERT INTO source_turns VALUES(2,'src:b','u2','s','r',0,'2023-05-25T12:00:00+00:00','user','theirs');
            """
        )
        con.commit()
        con.close()

    def test_read_source_turns_is_user_scoped(self) -> None:
        from server.source_evidence import ReadOnlySourceIndex, read_source_turns

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "src.sqlite"
            self._db(path)
            turns = read_source_turns(ReadOnlySourceIndex(path), "u1", ["src:a", "src:b"])
            self.assertIn("src:a", turns)
            self.assertNotIn("src:b", turns)

    def test_switch_matrix(self) -> None:
        import os

        from server import source_evidence as se

        old_source = os.environ.get("TIMEM_SOURCE_EVIDENCE")
        old_renderer = os.environ.get("TIMEM_TEMPORAL_RENDERER")
        try:
            rows = [{"id": "x", "content": "summary"}]
            os.environ["TIMEM_SOURCE_EVIDENCE"] = "0"
            os.environ["TIMEM_TEMPORAL_RENDERER"] = "calendar_v1"
            self.assertEqual(se.ground_evidence("u1", "When did it happen?", rows, 5), rows)
            os.environ["TIMEM_SOURCE_EVIDENCE"] = "1"
            os.environ["TIMEM_TEMPORAL_RENDERER"] = "off"
            self.assertEqual(se.ground_evidence("u1", "When did it happen?", rows, 5), rows)
            self.assertEqual(se.ground_evidence("u1", "When did it happen?", rows, 0), [])
        finally:
            for key, value in (("TIMEM_SOURCE_EVIDENCE", old_source), ("TIMEM_TEMPORAL_RENDERER", old_renderer)):
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main(verbosity=2)
