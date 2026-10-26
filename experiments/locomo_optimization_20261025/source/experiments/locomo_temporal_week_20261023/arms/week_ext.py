"""W0/W1 extension: anchor `last week` / `last weekend` to the utterance date.

Rules (NEXT_ADJUSTMENT.md sections 5-6):
  * C1 content is an immutable prefix; week lines are appended after it
  * only the two phrases are supported, weekday parsing is out of scope
  * anchored_value keeps the relative form ("the week before March 27, 2023")
  * W1 additionally records a Monday-based calendar range; `last weekend` only
    gets a range when the anchor sits Monday-Friday
  * new text must fit the remaining 8,000-char annotation budget, otherwise the
    whole W0 addition is dropped and the C1 rows are returned untouched
  * trace records precision=week|weekend, the anchoring policy and skip reasons
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

HEADER = "Calendar annotations derived from the original turns:"
BUDGET_CHARS = 8000
POLICY = "monday-based-calendar-week"

_WEEK_PHRASES = [
    ("last_weekend", re.compile(r"\blast\s+weekend\b", re.I)),
    ("last_week", re.compile(r"\blast\s+week\b", re.I)),
]

# phrases that contain the target words but must not be parsed
_BLOCKERS = [
    re.compile(r"\bthe\s+last\s+week\s+of\b", re.I),
    re.compile(r"\bweek\s+before\s+last\b", re.I),
    re.compile(r"\bthe\s+week\s+before\s+last\b", re.I),
    re.compile(r"\blast\s+weekend\s+of\b", re.I),
    re.compile(r"\b(?:days?|weeks?|months?|years?)\s+(?:before|after)\s+last\s+week\b", re.I),
]

_QUOTES = [re.compile(r'"[^"\n]*"'), re.compile(r"\u201c[^\u201d\n]*\u201d")]


@dataclass
class WeekMention:
    source_id: str
    raw_phrase: str
    span: Tuple[int, int]
    observed_at: str
    anchor_date: str
    anchored_value: str
    precision: str
    rule: str
    status: str
    skip_reason: Optional[str] = None
    calendar_range: Optional[str] = None
    policy: Optional[str] = None


def _parse_observed(value: str) -> Optional[datetime]:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _overlaps(start: int, end: int, spans: Sequence[Tuple[int, int]]) -> bool:
    return any(start < e and end > s for s, e in spans)


def _blocked_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    for pattern in _QUOTES:
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    for pattern in _BLOCKERS:
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    return spans


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _human_date(day: date) -> str:
    return day.strftime("%B %-d, %Y") if False else f"{day.strftime('%B')} {day.day}, {day.year}"


def parse_week_mentions(turn: dict, *, with_range: bool) -> List[WeekMention]:
    content = str(turn.get("content") or "")
    parsed = _parse_observed(str(turn.get("observed_at") or ""))
    if not content or parsed is None:
        return []
    anchor = parsed.date()
    blocked = _blocked_spans(content)
    out: List[WeekMention] = []
    taken: List[Tuple[int, int]] = []
    for rule, pattern in _WEEK_PHRASES:
        for match in pattern.finditer(content):
            start, end = match.start(), match.end()
            if _overlaps(start, end, blocked) or _overlaps(start, end, taken):
                continue
            taken.append((start, end))
            if rule == "last_week":
                anchored = f"the week before {_human_date(anchor)}"
                precision = "week"
                calendar_range = None
                if with_range:
                    monday = _monday(anchor)
                    calendar_range = f"{(monday - timedelta(days=7)).isoformat()}/{(monday - timedelta(days=1)).isoformat()}"
            else:
                anchored = f"the weekend before {_human_date(anchor)}"
                precision = "weekend"
                calendar_range = None
                if with_range and anchor.weekday() <= 4:  # Mon-Fri only
                    monday = _monday(anchor)
                    calendar_range = f"{(monday - timedelta(days=2)).isoformat()}/{(monday - timedelta(days=1)).isoformat()}"
            out.append(WeekMention(
                source_id=str(turn.get("source_id") or ""),
                raw_phrase=content[start:end],
                span=(start, end),
                observed_at=str(turn.get("observed_at") or ""),
                anchor_date=anchor.isoformat(),
                anchored_value=anchored,
                precision=precision,
                rule=f"anchor_{rule}",
                status="resolved",
                calendar_range=calendar_range,
                policy=POLICY if with_range else None,
            ))
    return out


def _line(mention: WeekMention) -> str:
    parts = [
        f"- source_id={mention.source_id}",
        f'raw_phrase={json.dumps(mention.raw_phrase, ensure_ascii=False)}',
        f"observed_at={mention.observed_at}",
        f'anchored_value={json.dumps(mention.anchored_value, ensure_ascii=False)}',
        f"precision={mention.precision}",
        f"rule={mention.rule}",
    ]
    if mention.calendar_range:
        parts.append(f"calendar_range={mention.calendar_range}")
    if mention.policy:
        parts.append(f"policy={mention.policy}")
    return "; ".join(parts)


def _already_has_week_rule(content: str) -> bool:
    return "rule=anchor_last_week" in content or "rule=anchor_last_weekend" in content


def add_week_anchors(
    c1_rows: Sequence[dict],
    original_rows: Sequence[dict],
    turns: Dict[str, dict],
    *,
    with_range: bool,
    budget_chars: int = BUDGET_CHARS,
) -> Tuple[List[dict], List[dict], bool]:
    """Append week anchors on top of the C1 output; never modify C1 text."""
    rows = [dict(r) for r in c1_rows]
    c1_added = sum(
        max(0, len(str(new.get("content") or "")) - len(str(old.get("content") or "")))
        for new, old in zip(rows, original_rows)
    )
    remaining = max(0, budget_chars - c1_added)

    per_row: List[Tuple[int, List[WeekMention]]] = []
    trace: List[dict] = []
    seen: set = set()
    for index, row in enumerate(rows):
        content = str(row.get("content") or "")
        if _already_has_week_rule(content):
            continue
        source_ids = row.get("source_ids") or []
        if not isinstance(source_ids, (list, tuple)):
            continue
        picked: List[WeekMention] = []
        for sid in source_ids:
            turn = turns.get(str(sid))
            if turn is None:
                continue
            for mention in parse_week_mentions(turn, with_range=with_range):
                key = (mention.source_id, mention.span[0], mention.span[1], mention.rule)
                if key in seen:
                    continue
                seen.add(key)
                picked.append(mention)
        for mention in picked:
            trace.append({
                "schema_version": "week-anchor-v1",
                "source_id": mention.source_id,
                "span": list(mention.span),
                "raw_phrase": mention.raw_phrase,
                "observed_at": mention.observed_at,
                "anchor_date": mention.anchor_date,
                "anchored_value": mention.anchored_value,
                "precision": mention.precision,
                "rule": mention.rule,
                "calendar_range": mention.calendar_range,
                "policy": mention.policy,
                "status": mention.status,
                "rendered": False,
                "render_skip_reason": None,
            })
        if picked:
            per_row.append((index, picked))

    if not per_row:
        return rows, trace, False

    total = sum(len(_line(m)) + 1 for _, picked in per_row for m in picked)
    if total > remaining:
        for entry in trace:
            entry["render_skip_reason"] = "annotation_budget_exceeded"
        return rows, trace, False

    for index, picked in per_row:
        content = str(rows[index].get("content") or "")
        block = "\n" + "\n".join(_line(m) for m in picked)
        if HEADER in content:
            rows[index]["content"] = content + block
        else:
            rows[index]["content"] = content + "\n\n" + HEADER + block
        for entry in trace:
            if entry["source_id"] in {m.source_id for m in picked}:
                entry["rendered"] = True
    return rows, trace, True
