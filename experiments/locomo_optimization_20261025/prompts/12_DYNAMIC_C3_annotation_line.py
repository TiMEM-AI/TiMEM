def _line(m: Mention) -> str:
    span = f"[{m.span[0]},{m.span[1]})"
    return (
        f"- source_id={m.source_id}; span={span}; source_sentence={_quote(m.source_sentence or '')}; "
        f"raw_phrase={_quote(m.raw_phrase)}; observed_at={m.observed_at}; anchor_date={m.anchor_date}; "
        f"precision={m.precision}; value={m.value}; rule={m.rule}; status={m.status}"
    )
