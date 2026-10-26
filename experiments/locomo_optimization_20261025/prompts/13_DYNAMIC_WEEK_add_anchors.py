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
