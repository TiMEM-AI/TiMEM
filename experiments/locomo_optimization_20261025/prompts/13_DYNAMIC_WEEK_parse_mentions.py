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
