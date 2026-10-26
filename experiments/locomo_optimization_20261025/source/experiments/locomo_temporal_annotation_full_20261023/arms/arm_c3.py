"""C3 arm: spec fields + deterministic parser fixes + source-sentence linkage.

Contract (EXECUTION_PLAN.md sections 6 and 7):
  * spans are 0-based, right-open, Unicode character indices over the raw content
  * every rendered annotation carries the sentence it came from (<= 400 chars)
  * compound / unsupported expressions are skipped as a whole, never partially
  * ambiguous mentions keep the phrase's own precision with value=null
  * dedupe by (source_id, span) across the whole question, first occurrence wins
  * budget is applied after dedupe; exceeding it falls back to the unannotated rows
  * idempotent through an explicit version marker, not the human header text
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "temporal-annotation-v2"
HEADER = "Calendar annotations derived from the original turns:"
MARKER = f"[{SCHEMA_VERSION}]"
DEFAULT_BUDGET_CHARS = 8000
MAX_SOURCE_SENTENCE_CHARS = 400

_WEEKDAY = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_MONTH_NAME = (
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
)

_PREFIX = re.compile(
    r"^(?:when\s+(?:did|does|do|is|are|was|were|has|have|will|can)\b"
    r"|(?:what|which)\s+(?:date|day|month|year|week)\b)",
    re.I,
)
_EXCLUDE = re.compile(
    r"\b(first|last|earliest|latest|recently|before|after|since|until|earlier|later|"
    r"second|third|fourth|fifth)\b",
    re.I,
)
_NON_DATE = re.compile(r"\b(how\s+long|how\s+many\s+(?:days|weeks|months|years)|duration|day\s+of\s+the\s+week|weekday)\b", re.I)

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_NUMBER_TOKEN = r"(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"

# --- expressions -----------------------------------------------------------


@dataclass(frozen=True)
class DateQueryPlan:
    intent: str
    requested_unit: Optional[str]
    rule_version: str


@dataclass(frozen=True)
class SourceTurn:
    source_id: str
    user_id: str
    role: str
    content: str
    observed_at: str


@dataclass
class Mention:
    source_id: str
    span: Tuple[int, int]
    raw_phrase: str
    source_sentence: Optional[str]
    sentence_span: Optional[Tuple[int, int]]
    observed_at: str
    anchor_date: Optional[str]
    precision: Optional[str]
    value: Optional[str]
    rule: str
    status: str
    skip_reason: Optional[str] = None
    rendered: bool = False
    render_skip_reason: Optional[str] = None

    def as_trace(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_id": self.source_id,
            "span": list(self.span),
            "raw_phrase": self.raw_phrase,
            "source_sentence": self.source_sentence,
            "sentence_span": list(self.sentence_span) if self.sentence_span else None,
            "observed_at": self.observed_at,
            "anchor_date": self.anchor_date,
            "precision": self.precision,
            "value": self.value,
            "rule": self.rule,
            "status": self.status,
            "skip_reason": self.skip_reason,
            "rendered": self.rendered,
            "render_skip_reason": self.render_skip_reason,
        }


def plan_date_query(query: str) -> Optional[DateQueryPlan]:
    text = (query or "").strip()
    if not text or len(text) > 300:
        return None
    if not _PREFIX.search(text):
        return None
    if _EXCLUDE.search(text) or _NON_DATE.search(text):
        return None
    head = text[:80].lower()
    unit = None
    m = re.search(r"\b(month|year|week|day|date)\b", head)
    if m:
        token = m.group(1).lower()
        unit = {"date": "day"}.get(token, token)
    return DateQueryPlan(intent="DATE_VALUE", requested_unit=unit, rule_version=SCHEMA_VERSION)


def parse_observed_at(value: str) -> Optional[datetime]:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


# --- span helpers ----------------------------------------------------------

def _sentence_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    start = 0
    for match in re.finditer(r"[.!?](?=\s|$)", text):
        spans.append((start, match.end()))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans or [(0, len(text))]


def _quote_spans(text: str) -> List[Tuple[int, int]]:
    """Quote spans without a length cap; apostrophes inside words are not quotes."""
    spans: List[Tuple[int, int]] = []
    for pattern in (re.compile(r'"[^"\n]*"'), re.compile(r"\u201c[^\u201d\n]*\u201d")):
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    for match in re.finditer(r"(?<![A-Za-z])'[^'\n]*'(?![A-Za-z])", text):
        spans.append((match.start(), match.end()))
    return spans


def _overlaps(start: int, end: int, spans: Sequence[Tuple[int, int]]) -> bool:
    return any(start < e and end > s for s, e in spans)


# --- unsupported compound patterns ----------------------------------------

_COMPOUND_UNSUPPORTED = [
    # "two days before yesterday", "three days after tomorrow"
    re.compile(rf"\b{_NUMBER_TOKEN}\s+days?\s+(?:before|after)\s+(?:yesterday|tomorrow|today)\b", re.I),
    # "twenty one days ago" (any quantity outside the supported set)
    re.compile(r"\b(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[-\s](?:one|two|three|four|five|six|seven|eight|nine))?\s+days?\s+ago\b", re.I),
    re.compile(r"\b(?:a\s+few|several|many|couple\s+of)\s+(?:days?|weeks?|months?|years?)\s+ago\b", re.I),
    re.compile(rf"\b(?:last|next|this)\s+{_WEEKDAY}\b", re.I),
    re.compile(r"\b(?:last|next|this)\s+(?:week|weekend|spring|summer|autumn|fall|winter|month-end|year-end)\b", re.I),
    re.compile(r"\bin\s+(?:" + _NUMBER_TOKEN + r")\s+(?:days?|weeks?|months?|years?)\b", re.I),
    re.compile(r"\bfor\s+(?:" + _NUMBER_TOKEN + r")\s+(?:days?|weeks?|months?|years?)\b", re.I),
    re.compile(r"\b(?:days?|weeks?|months?|years?)\s+(?:before|after)\b(?!\s+(?:yesterday|tomorrow))", re.I),
]

_HEDGE = re.compile(
    r"\b(?:about|around|roughly|approximately|approx\.?|nearly|almost|sometime)\s+(?=(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|next|last|this|today|yesterday|tomorrow|day|week|month|year)s?\b)"
    r"|\b(?:early|late|mid)\s+(?=(?:next|last|this|in)\b)",
    re.I,
)

_ABS_DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    rf"|\b{_MONTH_NAME}\s+\d{{1,2}},?\s+\d{{4}}\b"
    rf"|\b\d{{1,2}}\s+{_MONTH_NAME},?\s+\d{{4}}\b",
    re.I,
)

_SUPPORTED: List[Tuple[str, re.Pattern]] = [
    ("day_before_yesterday", re.compile(r"\bday\s+before\s+yesterday\b", re.I)),
    ("day_after_tomorrow", re.compile(r"\bday\s+after\s+tomorrow\b", re.I)),
    ("yesterday", re.compile(r"\byesterday\b", re.I)),
    ("tomorrow", re.compile(r"\btomorrow\b", re.I)),
    ("today", re.compile(r"\btoday\b", re.I)),
    ("n_days_ago", re.compile(rf"\b({_NUMBER_TOKEN})\s+days?\s+ago\b", re.I)),
    ("next_month", re.compile(r"\bnext\s+month\b", re.I)),
    ("last_month", re.compile(r"\blast\s+month\b", re.I)),
    ("this_month", re.compile(r"\bthis\s+month\b", re.I)),
    ("next_year", re.compile(r"\bnext\s+year\b", re.I)),
    ("last_year", re.compile(r"\blast\s+year\b", re.I)),
    ("this_year", re.compile(r"\bthis\s+year\b", re.I)),
]


def _blocked_spans(text: str) -> List[Tuple[int, int]]:
    spans = _quote_spans(text)
    for pattern in _COMPOUND_UNSUPPORTED:
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    # a hedge blocks only the time phrase it directly modifies
    for hedge in _HEDGE.finditer(text):
        best = None
        for _, pattern in _SUPPORTED:
            found = pattern.search(text, hedge.end())
            if found and (best is None or found.start() < best.start()):
                best = found
        end = best.end() if best else hedge.end()
        spans.append((hedge.start(), end))
    return spans


def _normalize(rule: str, match: re.Match, anchor: date) -> Tuple[Optional[str], str, Optional[str]]:
    if rule == "day_before_yesterday":
        return (anchor - timedelta(days=2)).isoformat(), "day", None
    if rule == "day_after_tomorrow":
        return (anchor + timedelta(days=2)).isoformat(), "day", None
    if rule == "yesterday":
        return (anchor - timedelta(days=1)).isoformat(), "day", None
    if rule == "tomorrow":
        return (anchor + timedelta(days=1)).isoformat(), "day", None
    if rule == "today":
        return anchor.isoformat(), "day", None
    if rule == "n_days_ago":
        raw = match.group(1).lower()
        if raw.isdigit():
            days = int(raw)
            if not 0 <= days <= 99:
                return None, "day", "unsupported_quantity"
        else:
            days = _WORD_NUMBERS.get(raw)
            if days is None:
                return None, "day", "unsupported_quantity"
        return (anchor - timedelta(days=days)).isoformat(), "day", None
    if rule in {"next_month", "last_month", "this_month"}:
        first = anchor.replace(day=1)
        if rule == "next_month":
            value = (first + timedelta(days=32)).strftime("%Y-%m")
        elif rule == "last_month":
            value = (first - timedelta(days=1)).strftime("%Y-%m")
        else:
            value = anchor.strftime("%Y-%m")
        return value, "month", None
    if rule in {"next_year", "last_year", "this_year"}:
        value = str(anchor.year + {"next_year": 1, "last_year": -1, "this_year": 0}[rule])
        return value, "year", None
    return None, "day", "unknown_rule"


def _rule_precision(rule: str) -> str:
    if rule.endswith("_month"):
        return "month"
    if rule.endswith("_year"):
        return "year"
    return "day"


def parse_mentions(turn: SourceTurn) -> List[Mention]:
    anchor_dt = parse_observed_at(turn.observed_at)
    text = turn.content or ""
    if anchor_dt is None or not text:
        return []
    anchor = anchor_dt.date()
    blocked = _blocked_spans(text)
    sentences = _sentence_spans(text)
    mentions: List[Mention] = []
    taken: List[Tuple[int, int]] = []

    for rule, pattern in _SUPPORTED:
        for match in pattern.finditer(text):
            start, end = match.start(), match.end()
            if _overlaps(start, end, taken) or _overlaps(start, end, blocked):
                continue
            sentence = next(((s, e) for s, e in sentences if s <= start < e), None)
            source_sentence = text[sentence[0]:sentence[1]].strip() if sentence else None
            sentence_span = sentence
            takes_in_sentence = _ABS_DATE.search(text[sentence[0]:sentence[1]]) if sentence else None

            if takes_in_sentence:
                mentions.append(Mention(
                    source_id=turn.source_id, span=(start, end), raw_phrase=match.group(0),
                    source_sentence=source_sentence, sentence_span=sentence_span,
                    observed_at=turn.observed_at, anchor_date=anchor.isoformat(),
                    precision=_rule_precision(rule), value=None, rule=rule, status="ambiguous",
                    skip_reason="absolute_date_cooccurrence",
                ))
                taken.append((start, end))
                continue

            value, precision, reason = _normalize(rule, match, anchor)
            mentions.append(Mention(
                source_id=turn.source_id, span=(start, end), raw_phrase=match.group(0),
                source_sentence=source_sentence, sentence_span=sentence_span,
                observed_at=turn.observed_at, anchor_date=anchor.isoformat(),
                precision=precision, value=value, rule=rule,
                status="resolved" if value else "skipped", skip_reason=reason,
            ))
            taken.append((start, end))

    mentions.sort(key=lambda m: m.span[0])
    return mentions


# --- rendering -------------------------------------------------------------

def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _line(m: Mention) -> str:
    span = f"[{m.span[0]},{m.span[1]})"
    return (
        f"- source_id={m.source_id}; span={span}; source_sentence={_quote(m.source_sentence or '')}; "
        f"raw_phrase={_quote(m.raw_phrase)}; observed_at={m.observed_at}; anchor_date={m.anchor_date}; "
        f"precision={m.precision}; value={m.value}; rule={m.rule}; status={m.status}"
    )


@dataclass
class RenderResult:
    rows: List[dict]
    changed: bool
    trace: List[dict] = field(default_factory=list)
    added_chars: int = 0
    budget_exceeded: bool = False


def annotate(rows: Sequence[dict], turns_by_id: Dict[str, SourceTurn], query: str,
             budget_chars: int = DEFAULT_BUDGET_CHARS) -> RenderResult:
    plan = plan_date_query(query)
    result = RenderResult(rows=[dict(r) for r in rows], changed=False)
    if plan is None:
        return result.rows, result.trace, result.changed

    turns_by_id = {
        sid: (item if isinstance(item, SourceTurn) else SourceTurn(
            source_id=item.get("source_id", sid), user_id=item.get("user_id", ""),
            role=item.get("role", "user"), content=item.get("content", ""),
            observed_at=item.get("observed_at", "")))
        for sid, item in turns_by_id.items()
    }
    seen: Dict[Tuple[str, int, int], Mention] = {}
    per_row: List[Tuple[int, List[Mention]]] = []

    for index, row in enumerate(result.rows):
        content = str(row.get("content") or "")
        if content.rstrip().endswith(MARKER):
            continue
        if not content.lstrip().startswith("Original conversation evidence:"):
            continue
        source_ids = row.get("source_ids") or []
        if not isinstance(source_ids, (list, tuple)):
            continue
        picked: List[Mention] = []
        for source_id in source_ids:
            turn = turns_by_id.get(str(source_id))
            if turn is None:
                continue
            for mention in parse_mentions(turn):
                key = (mention.source_id, mention.span[0], mention.span[1])
                if key in seen:
                    continue
                seen[key] = mention
                if mention.status != "resolved":
                    continue
                if not mention.source_sentence or len(mention.source_sentence) > MAX_SOURCE_SENTENCE_CHARS:
                    mention.rendered = False
                    mention.render_skip_reason = "source_sentence_too_long" if mention.source_sentence else "source_sentence_missing"
                    continue
                if mention.source_sentence and mention.raw_phrase not in mention.source_sentence:
                    mention.rendered = False
                    mention.render_skip_reason = "span_mismatch"
                    continue
                text = turn.content or ""
                if text[mention.span[0]:mention.span[1]] != mention.raw_phrase:
                    mention.rendered = False
                    mention.render_skip_reason = "span_mismatch"
                    continue
                picked.append(mention)
        if picked:
            per_row.append((index, picked))

    if not per_row:
        result.trace = [m.as_trace() for m in seen.values()]
        return result.rows, result.trace, result.changed

    total = sum(len(_line(m)) + 1 for _, picked in per_row for m in picked) + len(HEADER) + len(MARKER) + 6
    if total > budget_chars:
        result.budget_exceeded = True
        for mention in seen.values():
            if mention.status == "resolved" and mention.rendered is False and mention.render_skip_reason is None:
                mention.render_skip_reason = "annotation_budget_exceeded"
        result.trace = [m.as_trace() for m in seen.values()]
        return result.rows, result.trace, result.changed

    added = 0
    for index, picked in per_row:
        for mention in picked:
            mention.rendered = True
            mention.render_skip_reason = None
        block = "\n\n" + HEADER + "\n" + "\n".join(_line(m) for m in picked) + "\n" + MARKER
        result.rows[index]["content"] = str(result.rows[index].get("content") or "") + block
        added += len(block)
    result.changed = True
    result.added_chars = added
    result.trace = [m.as_trace() for m in seen.values()]
    return result.rows, result.trace, result.changed
