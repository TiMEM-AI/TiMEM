def judge_prompt(dataset_id: str, question: EvalQuestion, generated: str) -> str | None:
    """Return None when the score is a deterministic letter match."""
    if dataset_id.startswith("personamem_v2") and question.options:
        return None
    if dataset_id.startswith("personamem_v2"):
        preference = question.preference or question.gold
        template = _NARROW_NEGATIVE if preference.lower().startswith("do not") else _NARROW_POSITIVE
        return template.format(
            user_query=question.question,
            preference=preference,
            model_response=generated,
        )
    return _ACCURACY.format(
        question=question.question,
        gold_answer=question.gold,
        generated_answer=generated,
    )
