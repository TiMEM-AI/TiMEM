def _line(mention) -> str:
    span = f"[{mention.span[0]},{mention.span[1]})"
    return (
        f"- source_id={mention.source_id}; span={span}; "
        f'raw_phrase="{mention.raw_phrase}"; observed_at={mention.observed_at}; '
        f"anchor_date={mention.anchor_date}; precision={mention.precision}; "
        f"value={mention.normalized_value}; rule={mention.rule_id}; status=resolved"
    )
