"""Unofficial local score. Answer, then judge, using the public AML prompts.

The judge endpoint defaults to the current ARK settings. Set JUDGE_API_KEY,
JUDGE_API_BASE, and JUDGE_MODEL to point the judge at Kingsoft Cloud later
without changing Add/Search.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import tiktoken

from benchmark_data.aml_models import EvalQuestion

ANSWER_INPUT_TOKEN_BUDGET = 117_760

_OPEN_ANSWER = """You are asked to answer a question based on your memories of a conversation.

1. Use only the provided memories. Prefer the memory that answers the question most directly.
2. Your memories are episodic raw observations. Reason about what they imply. Do not refuse just because the answer is not stated verbatim.
3. The question may contain typos. Match it to the most relevant memory even if the wording differs.
4. When multiple answers are possible, list all supported answers, not just the first.
5. For counts or time intervals, enumerate carefully before answering.
6. Preserve specific names, titles, places, and labels from the memories.
7. Convert relative times like "yesterday", "last month", and "last year" into dates, months, or years when the memory timestamp makes it clear. Keep week-based expressions relative.
8. If memories conflict, prefer the most recent supported memory.
9. For list questions, include all required items and no extras.
10. Keep the final answer minimal. Do not add explanation, background, or extra dates unless needed for correctness.

Memories:

{memories}

Question: {question}
Answer with the shortest correct phrase or sentence. No preamble, no fluff:"""

_ACCURACY = """Your task is to label an answer as 'CORRECT' or 'WRONG' given:
(1) a question,
(2) a gold (ground truth) answer,
(3) a generated answer.

Core principle — Inclusion + Non-contradiction
- Be GENEROUS: if the generated answer clearly includes the gold's key content (or a clear paraphrase of the same content) and does not contradict it, mark CORRECT — even if extra details are added.
- Mark WRONG only when the generated answer does not include the gold's content, changes it, or contradicts it.

TIME (strict granularity; relative form equivalence; no calendar math)
- Granularity must match exactly: HOUR to HOUR, DAY to DAY, MONTH to MONTH, YEAR to YEAR.
- Do NOT convert relative time to an absolute date, or the reverse.

Lists of DISTINCT facts:
- If the gold answer lists multiple distinct facts, the generated answer must cover all of them.
- Extra non-contradictory items generally count as WRONG.

Preference/Benefit Questions (e.g., "what X likes/values most")
- If gold lists multiple reasons, the generated answer only needs to include any one of them without contradiction to be CORRECT.

Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response.

Just return the label CORRECT or WRONG in a json format with the key as "label":

```json
{{
    "label": "CORRECT" or "WRONG"
}}
```"""

_NARROW_POSITIVE = """You are evaluating an AI assistant's personalization capabilities in open-ended conversations.

User's query:
{user_query}

Ground truth user preference: {preference}

Model's response to evaluate:
{model_response}

Give a score from 0.0 (completely irrelevant to or contradicts the preference) to 1.0 (explicitly and thoughtfully personalized to the preference).

Provide your reasoning first, then give your final score as \\boxed{{score}} where score is a number between 0.0 and 1.0.
"""

_NARROW_NEGATIVE = """You are evaluating an AI assistant's personalization capabilities in open-ended conversations.

User's query:
{user_query}

Ground truth user preference: {preference}

Model's response to evaluate:
{model_response}

The preference is a "do not personalize" instruction. Score 1.0 when the response stays helpful without using that preference, and 0.0 when it personalizes on the forbidden preference.

Provide your reasoning first, then give your final score as \\boxed{{score}} where score is a number between 0.0 and 1.0.
"""

_RECALL_SUFFIX = (
    " Please recall my related preferences from our conversation history "
    "to give personalized responses."
)


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


def parse_correct_label(text: str) -> bool:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("judge response has no JSON object")
    payload = json.loads(match.group(0))
    label = str(payload.get("label", "")).upper()
    if label not in {"CORRECT", "WRONG"}:
        raise ValueError("judge label must be CORRECT or WRONG")
    return label == "CORRECT"


def parse_boxed_score(text: str) -> float:
    match = re.search(r"\\boxed\{([0-9]*\.?[0-9]+)\}", text)
    if not match:
        return 0.0
    return max(0.0, min(1.0, float(match.group(1))))


def extract_final_letter(text: str) -> str:
    match = re.search(r"Final Answer:\s*([A-Z])", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    match = re.search(r"\b([A-Z])\s*$", text.strip())
    return match.group(1).upper() if match else ""


def letter_for_gold(question: EvalQuestion) -> str:
    for option in question.options or []:
        match = re.match(r"^([A-Z])\.\s*(.*)$", option)
        if match and match.group(2).strip() == question.gold.strip():
            return match.group(1)
    return ""


def score_mcq(question: EvalQuestion, generated: str) -> dict[str, Any]:
    predicted = extract_final_letter(generated)
    gold_letter = letter_for_gold(question)
    correct = bool(predicted) and predicted == gold_letter
    return {
        "correct": correct,
        "score": 1.0 if correct else 0.0,
        "predicted_letter": predicted,
        "gold_letter": gold_letter,
        "method": "letter_match",
    }


async def complete_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout_sec: float = 600.0,
) -> str:
    if not api_key or not model:
        raise RuntimeError("Answer/judge model is not configured")
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return str(response.json()["choices"][0]["message"]["content"]).strip()
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.NetworkError) as exc:
            last_error = exc
            if attempt == 2:
                raise
    raise last_error or RuntimeError("chat completion failed")
