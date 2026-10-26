def format_memories(records: list[dict[str, Any]], *, question: str, options: list[str] | None) -> str:
    """Keep a token-counted prefix of Search hits, in returned order."""
    encoder = tiktoken.get_encoding("cl100k_base")
    reserved = len(encoder.encode(question))
    if options:
        reserved += len(encoder.encode("\n".join(options)))
    budget = max(0, ANSWER_INPUT_TOKEN_BUDGET - reserved)
    lines: list[str] = []
    used = 0
    for record in records:
        content = str(record.get("content") or "").strip()
        if not content:
            continue
        created_at = str(record.get("created_at") or "").strip()
        line = f"- [{created_at}] {content}" if created_at else f"- {content}"
        cost = len(encoder.encode(line))
        if lines and used + cost > budget:
            break
        if not lines and cost > budget:
            line = line[: budget * 4]
            lines.append(line)
            break
        lines.append(line)
        used += cost
    return "\n".join(lines) if lines else "(no memories)"
