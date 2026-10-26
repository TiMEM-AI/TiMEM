def annotate(rows: Sequence[dict], turns: Dict[str, dict], query: str, budget_chars: int = DEFAULT_BUDGET_CHARS) -> Tuple[List[dict], List[dict], bool]:
    plan = a0.plan_date_query(query)
    out = [dict(row) for row in rows]
    if plan is None:
        return out, [], False

    turns_by_id = {
        sid: a0.SourceTurn(
            source_id=item["source_id"], user_id=item.get("user_id", ""),
            role=item.get("role", "user"), content=item.get("content", ""),
            observed_at=item.get("observed_at", ""),
        )
        for sid, item in turns.items()
    }

    per_row: List[Tuple[int, List[str], list]] = []
    trace: List[dict] = []
    for index, row in enumerate(out):
        content = str(row.get("content") or "")
        if a0.ANNOTATION_HEADER in content:
            continue
        if not content.lstrip().startswith("Original conversation evidence:"):
            continue
        source_ids = row.get("source_ids") or []
        if not isinstance(source_ids, (list, tuple)):
            continue
        lines, used, seen = [], [], set()
        for sid in source_ids:
            turn = turns_by_id.get(str(sid))
            if turn is None:
                continue
            for mention in a0.parse_time_mentions(turn):
                if mention.status != "resolved" or not mention.normalized_value:
                    continue
                key = (mention.source_id, mention.span, mention.rule_id)
                if key in seen:
                    continue
                seen.add(key)
                lines.append(_line(mention))
                used.append(mention)
        if lines:
            per_row.append((index, lines, used))

    if not per_row:
        return out, trace, False

    total = sum(len(line) + 1 for _, lines, _ in per_row for line in lines) + len(HEADER) + 2
    if total > budget_chars:
        return out, trace, False

    for index, lines, used in per_row:
        block = "\n\n" + HEADER + "\n" + "\n".join(lines)
        out[index]["content"] = str(out[index].get("content") or "") + block
        for mention in used:
            trace.append({
                "schema_version": "temporal-annotation-v2-format-only",
                "source_id": mention.source_id,
                "span": list(mention.span),
                "raw_phrase": mention.raw_phrase,
                "observed_at": mention.observed_at,
                "anchor_date": mention.anchor_date,
                "precision": mention.precision,
                "value": mention.normalized_value,
                "rule": mention.rule_id,
                "status": "resolved",
            })
    return out, trace, True
