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
