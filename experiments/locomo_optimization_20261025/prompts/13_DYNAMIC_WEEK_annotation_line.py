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
