def _annotation_line(mention: TimeMention) -> str:
    return (
        f'- source_id={mention.source_id}; raw_phrase="{mention.raw_phrase}"; '
        f"observed_at={mention.observed_at}; calendar_value={mention.normalized_value}; "
        f"precision={mention.precision}; rule={mention.rule_id}."
    )
