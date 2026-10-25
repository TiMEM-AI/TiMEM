def build_prompt(payload: dict) -> str:
    return PROMPT_TEMPLATE.format(
        question=payload["question"],
        protected=json.dumps(payload["protected"], ensure_ascii=False),
        tail=json.dumps(payload["tail"], ensure_ascii=False),
        optional=json.dumps(payload["optional"], ensure_ascii=False))
