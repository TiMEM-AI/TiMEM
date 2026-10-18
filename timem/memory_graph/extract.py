"""Extract event slots from one L2 memory. The prompt is for dialogue, not news."""

from __future__ import annotations

import json
import re
from typing import Awaitable, Callable, Dict, Optional

PLACEHOLDER_RE = re.compile(r"(未提及|未提供|未知|不适用|n/?a|none|null)", re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

EXTRACT_PROMPT = """从下面这段会话记忆中抽出一个事件。只根据原文，不要补充没有写到的信息。

记忆:
{content}

返回一个 JSON 对象，不要解释。字段:
name: 不超过 40 字的事件名
subject: 谁发起
object: 对谁或对什么
action: 做了什么
time: 何时
location: 何地
cause: 原因
result: 结果

没有依据的字段用空字符串。"""

SLOTS = ("subject", "object", "action", "time", "location", "cause", "result")


def is_valid_slot_name(name: Optional[str]) -> bool:
    if not name or not isinstance(name, str):
        return False
    text = name.strip()
    if len(text) < 2:
        return False
    if PLACEHOLDER_RE.search(text):
        return False
    return True


def parse_event_json(text: str) -> Dict[str, str]:
    raw = (text or "").strip()
    fenced = _FENCE_RE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    result = {"name": str(payload.get("name") or "").strip()[:80]}
    for slot in SLOTS:
        value = str(payload.get(slot) or "").strip()
        result[slot] = value if is_valid_slot_name(value) else ""
    return result


async def extract_event(
    content: str,
    complete: Callable[[str], Awaitable[str]],
) -> Dict[str, str]:
    text = await complete(EXTRACT_PROMPT.format(content=content))
    return parse_event_json(text)


async def default_complete(prompt: str) -> str:
    from llm.llm_manager import get_llm

    llm = get_llm()
    messages = llm.format_chat_prompt("", prompt)
    response = await llm.chat(messages, temperature=0)
    return str(getattr(response, "content", "") or "")
