"""A0: conservative date-question routing + original-utterance calendar anchoring.

Pure functions only: nothing here reads benchmark labels, gold answers or
question ids. The router looks at the user query, the parser looks at original
turn text (from `source_evidence.read_source_turns`), and the renderer appends a
separate annotation block while leaving the original evidence untouched.

Scope (see IMPLEMENTATION_PLAN.md section 7):
  supported   today / yesterday / tomorrow
              day before yesterday / day after tomorrow
              N days ago (digits and one..ten)
              last|this|next month   (YYYY-MM)
              last|this|next year    (YYYY)
  unsupported weekday phrases, week/weekend, seasons, fuzzy qualifiers,
              durations, "in N days", relative-to-event expressions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

RULE_VERSION = "a0-calendar-v1"
ANNOTATION_HEADER = "Calendar annotations derived from the original turns:"
DEFAULT_BUDGET_CHARS = 8000

# ---------------------------------------------------------------- routing ----

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
_NON_DATE_INTENT = re.compile(
    r"\b(how\s+long|how\s+many\s+(?:days|weeks|months|years)|duration|"
    r"day\s+of\s+the\s+week|what\s+day\s+of\s+the\s+week|weekday)\b",
    re.I,
)


@dataclass(frozen=True)
class DateQueryPlan:
    intent: str
    precision: Optional[str]
    rule_version: str
    matched_rule: str


def plan_date_query(query: str) -> Optional[DateQueryPlan]:
    """Return a plan only for questions whose main ask is a calendar value."""
    text = (query or "").strip()
    if not text:
        return None
    if len(text) > 300:
        return None
    if not _PREFIX.search(text):
        return None
    if _EXCLUDE.search(text):
        return None
    if _NON_DATE_INTENT.search(text):
        return None
    lowered = text.lower()
    precision = None
    if re.search(r"\b(month)\b", lowered):
        precision = "month"
    elif re.search(r"\b(year)\b", lowered):
        precision = "year"
    elif re.search(r"\b(date|day)\b", lowered):
        precision = "day"
    return DateQueryPlan(intent="DATE_VALUE", precision=precision, rule_version=RULE_VERSION, matched_rule="prefix")


# ---------------------------------------------------------------- parsing ----

@dataclass(frozen=True)
class SourceTurn:
    source_id: str
    user_id: str
    role: str
    content: str
    observed_at: str

    @property
    def anchor(self) -> Optional[datetime]:
        return parse_observed_at(self.observed_at)


@dataclass(frozen=True)
class TimeMention:
    source_id: str
    raw_phrase: str
    span: Tuple[int, int]
    observed_at: str
    anchor_date: Optional[str]
    precision: str
    normalized_value: Optional[str]
    rule_id: str
    qualifier: Optional[str]
    status: str
    skip_reason: Optional[str] = None


@dataclass
class RenderResult:
    rows: List[dict]
    changed: bool
    annotations: List[TimeMention] = field(default_factory=list)
    skipped: List[TimeMention] = field(default_factory=list)
    added_chars: int = 0
    budget_exceeded: bool = False


def parse_observed_at(value: str) -> Optional[datetime]:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# Spans that must be excluded wholesale before matching supported phrases.
_HEDGE = (
    r"\b(?:about|around|roughly|approximately|approx\.?|nearly|almost|sometime)\s+"
    r"(?=(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|next|last|this|today|yesterday|tomorrow|day|week|month|year)s?\b)"
    r"|\b(?:early|late|mid)\s+(?=(?:next|last|this|in)\b)"
)
_QUOTE_PATTERNS = [
    re.compile(r'"[^"\n]{1,200}"'),
    re.compile(r"\u201c[^\u201d\n]{1,200}\u201d"),
    re.compile(r"(?<!\w)'[^'\n]{1,200}'(?!\w)"),
]
_UNSUPPORTED_PATTERNS = [
    re.compile(r"\b(?:last|next|this)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|weekend|spring|summer|autumn|fall|winter)\b", re.I),
    re.compile(r"\b(?:a\s+few|several|many|couple\s+of)\s+(?:days?|weeks?|months?|years?)\s+ago\b", re.I),
    re.compile(r"\bin\s+(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:days?|weeks?|months?|years?)\b", re.I),
    re.compile(r"\bfor\s+(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:days?|weeks?|months?|years?)\b", re.I),
    re.compile(r"\b(?:days?|weeks?|months?|years?)\s+(?:before|after)\b(?!\s+(?:yesterday|tomorrow))", re.I),
]
_DURATION_HINT = re.compile(r"\b(?:for|in|within)\s+\S+\s+(?:days?|weeks?|months?|years?)\b", re.I)
_ABS_DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b(?:january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+\d{1,2},?\s+\d{4}\b",
    re.I,
)


def _sentence_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    start = 0
    for match in re.finditer(r"[.!?](?=\s|$)", text):
        spans.append((start, match.end()))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _date_conflict_spans(text: str) -> List[Tuple[int, int]]:
    """Sentences that already state an absolute date keep their phrases untouched."""
    return [(s, e) for s, e in _sentence_spans(text) if _ABS_DATE.search(text[s:e])]


_SUPPORTED: List[Tuple[str, re.Pattern]] = [
    ("day_before_yesterday", re.compile(r"\bday\s+before\s+yesterday\b", re.I)),
    ("day_after_tomorrow", re.compile(r"\bday\s+after\s+tomorrow\b", re.I)),
    ("yesterday", re.compile(r"\byesterday\b", re.I)),
    ("tomorrow", re.compile(r"\btomorrow\b", re.I)),
    ("today", re.compile(r"\btoday\b", re.I)),
    ("n_days_ago", re.compile(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+days?\s+ago\b", re.I)),
    ("next_month", re.compile(r"\bnext\s+month\b", re.I)),
    ("last_month", re.compile(r"\blast\s+month\b", re.I)),
    ("this_month", re.compile(r"\bthis\s+month\b", re.I)),
    ("next_year", re.compile(r"\bnext\s+year\b", re.I)),
    ("last_year", re.compile(r"\blast\s+year\b", re.I)),
    ("this_year", re.compile(r"\bthis\s+year\b", re.I)),
]


def _blocked_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    for pattern in _QUOTE_PATTERNS:
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    for pattern in _UNSUPPORTED_PATTERNS:
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    # hedged phrases: block the qualifier together with the time phrase it modifies
    for match in re.finditer(_HEDGE, text, re.I):
        window = text[match.start(): match.start() + 60]
        phrase_end = None
        for _, pattern in _SUPPORTED:
            found = pattern.search(window)
            if found:
                phrase_end = found.end()
                break
        spans.append((match.start(), match.start() + (phrase_end or len(match.group(0)))))
    return spans


def _overlaps(start: int, end: int, spans: Sequence[Tuple[int, int]]) -> bool:
    return any(start < s_end and end > s_start for s_start, s_end in spans)


def _normalize(rule: str, match: re.Match, anchor_date: date) -> Tuple[Optional[str], str, Optional[str]]:
    """Return (normalized_value, precision, skip_reason)."""
    if rule == "day_before_yesterday":
        return (anchor_date - timedelta(days=2)).isoformat(), "day", None
    if rule == "day_after_tomorrow":
        return (anchor_date + timedelta(days=2)).isoformat(), "day", None
    if rule == "yesterday":
        return (anchor_date - timedelta(days=1)).isoformat(), "day", None
    if rule == "tomorrow":
        return (anchor_date + timedelta(days=1)).isoformat(), "day", None
    if rule == "today":
        return anchor_date.isoformat(), "day", None
    if rule == "n_days_ago":
        raw = match.group(1).lower()
        days = _WORD_NUMBERS.get(raw) if not raw.isdigit() else int(raw)
        if days is None:
            return None, "day", "unsupported_quantity"
        return (anchor_date - timedelta(days=days)).isoformat(), "day", None
    if rule in {"next_month", "last_month", "this_month"}:
        first = anchor_date.replace(day=1)
        if rule == "next_month":
            value = (first + timedelta(days=32)).strftime("%Y-%m")
        elif rule == "last_month":
            value = (first - timedelta(days=1)).strftime("%Y-%m")
        else:
            value = anchor_date.strftime("%Y-%m")
        return value, "month", None
    if rule in {"next_year", "last_year", "this_year"}:
        if rule == "next_year":
            value = str(anchor_date.year + 1)
        elif rule == "last_year":
            value = str(anchor_date.year - 1)
        else:
            value = str(anchor_date.year)
        return value, "year", None
    return None, "day", "unknown_rule"


def parse_time_mentions(turn: SourceTurn, *, max_mentions: int = 8) -> Tuple[TimeMention, ...]:
    """Extract supported calendar phrases from one original turn."""
    anchor = turn.anchor
    if anchor is None:
        return ()
    anchor_date = anchor.date()
    text = turn.content or ""
    if not text:
        return ()
    blocked = _blocked_spans(text)
    conflicts = _date_conflict_spans(text)
    mentions: List[TimeMention] = []
    taken: List[Tuple[int, int]] = []
    for rule, pattern in _SUPPORTED:
        for match in pattern.finditer(text):
            start, end = match.start(), match.end()
            if _overlaps(start, end, blocked) or _overlaps(start, end, taken):
                continue
            if _overlaps(start, end, conflicts):
                mentions.append(
                    TimeMention(
                        source_id=turn.source_id,
                        raw_phrase=text[start:end],
                        span=(start, end),
                        observed_at=turn.observed_at,
                        anchor_date=anchor_date.isoformat(),
                        precision="day",
                        normalized_value=None,
                        rule_id=rule,
                        qualifier=None,
                        status="ambiguous",
                        skip_reason="absolute_date_conflict",
                    )
                )
                continue
            value, precision, reason = _normalize(rule, match, anchor_date)
            mentions.append(
                TimeMention(
                    source_id=turn.source_id,
                    raw_phrase=text[start:end],
                    span=(start, end),
                    observed_at=turn.observed_at,
                    anchor_date=anchor_date.isoformat(),
                    precision=precision,
                    normalized_value=value,
                    rule_id=rule,
                    qualifier=None,
                    status="resolved" if value else "unsupported",
                    skip_reason=reason,
                )
            )
            if value:
                taken.append((start, end))
            if len(mentions) >= max_mentions:
                break
        if len(mentions) >= max_mentions:
            break
    return tuple(mentions)


# --------------------------------------------------------------- rendering ---

def _annotation_line(mention: TimeMention) -> str:
    return (
        f'- source_id={mention.source_id}; raw_phrase="{mention.raw_phrase}"; '
        f"observed_at={mention.observed_at}; calendar_value={mention.normalized_value}; "
        f"precision={mention.precision}; rule={mention.rule_id}."
    )


def _already_annotated(content: str) -> bool:
    return ANNOTATION_HEADER in content


def render_date_evidence(
    plan: Optional[DateQueryPlan],
    legacy_rows: Sequence[dict],
    turns_by_id: Dict[str, SourceTurn],
    *,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> RenderResult:
    """Append calendar annotations to original-evidence rows; never rewrite text."""
    rows = [dict(row) for row in legacy_rows]
    result = RenderResult(rows=rows, changed=False)
    if plan is None or not rows:
        return result

    per_row: List[Tuple[int, List[str], List[TimeMention]]] = []
    for index, row in enumerate(rows):
        content = str(row.get("content") or "")
        if _already_annotated(content):
            continue
        if not content.lstrip().startswith("Original conversation evidence:"):
            # summary/derived rows are never annotated
            continue
        source_ids = row.get("source_ids") or []
        if not isinstance(source_ids, (list, tuple)):
            continue
        lines: List[str] = []
        used: List[TimeMention] = []
        seen: set = set()
        for source_id in source_ids:
            turn = turns_by_id.get(str(source_id))
            if turn is None:
                continue
            for mention in parse_time_mentions(turn):
                if mention.status != "resolved" or not mention.normalized_value:
                    result.skipped.append(mention)
                    continue
                key = (mention.source_id, mention.span, mention.rule_id)
                if key in seen:
                    continue
                seen.add(key)
                lines.append(_annotation_line(mention))
                used.append(mention)
        if lines:
            per_row.append((index, lines, used))

    if not per_row:
        return result

    total = 0
    for index, lines, used in per_row:
        total += sum(len(line) + 1 for line in lines) + len(ANNOTATION_HEADER) + 2
    if total > budget_chars:
        result.budget_exceeded = True
        return result

    added = 0
    for index, lines, used in per_row:
        block = "\n\n" + ANNOTATION_HEADER + "\n" + "\n".join(lines)
        rows[index]["content"] = str(rows[index].get("content") or "") + block
        added += len(block)
        result.annotations.extend(used)
    result.changed = True
    result.added_chars = added
    return result
