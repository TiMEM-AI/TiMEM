def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
