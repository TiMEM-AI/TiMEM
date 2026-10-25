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
