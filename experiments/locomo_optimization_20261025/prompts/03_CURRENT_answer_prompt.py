def answer_prompt(dataset_id: str, question: EvalQuestion, memories: str) -> str:
    if dataset_id.startswith("personamem_v2") and question.options:
        options = "\n".join(question.options)
        return (
            f"{memories}\n\nUser query: {question.question}{_RECALL_SUFFIX}\n\n"
            f"Please choose the best answer from the following options:\n{options}\n\n"
            "Provide your reasoning first, then give your final answer as 'Final Answer: [Letter]'."
        )
    if dataset_id.startswith("personamem_v2"):
        return _OPEN_ANSWER.format(
            memories=memories,
            question=question.question + _RECALL_SUFFIX,
        )
    return _OPEN_ANSWER.format(memories=memories, question=question.question)
