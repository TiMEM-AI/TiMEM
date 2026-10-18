"""Stable episode keys and fact sentences for the memory event graph."""

from __future__ import annotations

import hashlib
import uuid
from typing import Dict, Iterable, List, Optional

HAS_FACT_TEMPLATES = {
    "HAS_SUBJECT": "{event_name} 的主体是 {target_name}",
    "HAS_OBJECT": "{event_name} 的客体是 {target_name}",
    "HAS_LOCATION": "{event_name} 发生在 {target_name}",
    "HAS_TIME": "{event_name} 发生在 {target_name}",
    "HAS_CAUSE": "{event_name} 的原因是 {target_name}",
    "HAS_RESULT": "{event_name} 导致 {target_name}",
    "HAS_ACTION": "{event_name} 的行为是 {target_name}",
}

SLOT_REL = {
    "subject": "HAS_SUBJECT",
    "object": "HAS_OBJECT",
    "location": "HAS_LOCATION",
    "time": "HAS_TIME",
    "cause": "HAS_CAUSE",
    "result": "HAS_RESULT",
    "action": "HAS_ACTION",
}

_EPISODE_NS = uuid.UUID("6b1c0e2a-4f33-4c1a-9a7e-0d2f8b91c4e1")
_ENTITY_NS = uuid.UUID("c3a91e47-8d2b-4f60-9e15-7a0c4d88b2f6")


def content_hash(l2_id: str, content: str) -> str:
    raw = f"{l2_id}\n{content}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def episode_key(
    *,
    user_id: str,
    expert_id: str,
    report_date: str,
    seg_hash: str,
) -> str:
    return f"memory:local:{user_id}:{expert_id}:{report_date}:{seg_hash}"


def stable_id(namespace: uuid.UUID, key: str) -> str:
    return str(uuid.uuid5(namespace, key))


def episode_id(key: str) -> str:
    return stable_id(_EPISODE_NS, key)


def entity_id(*, user_id: str, expert_id: str, slot: str, name: str) -> str:
    return stable_id(_ENTITY_NS, f"{user_id}:{expert_id}:{slot}:{name.strip().lower()}")


def fact_sentence(rel_type: str, event_name: str, target_name: str) -> str:
    template = HAS_FACT_TEMPLATES.get(rel_type)
    if not template:
        return f"{event_name} {rel_type} {target_name}".strip()
    return template.format(event_name=event_name, target_name=target_name)


def merge_source_l2_ids(existing: Optional[Iterable[str]], l2_id: Optional[str]) -> List[str]:
    ids = [str(item) for item in (existing or []) if str(item or "").strip()]
    text = str(l2_id or "").strip()
    if text and text not in ids:
        ids.append(text)
    return ids


def episode_name(extracted_name: str, content: str) -> str:
    name = (extracted_name or "").strip()
    if name:
        return name[:80]
    compact = " ".join((content or "").split())
    return (compact[:40] or "记忆片段").strip()


def build_facts(event_name: str, slots: Dict[str, str]) -> List[Dict[str, str]]:
    facts = []
    for slot, rel in SLOT_REL.items():
        target = str(slots.get(slot) or "").strip()
        if not target:
            continue
        facts.append({
            "slot": slot,
            "rel_type": rel,
            "name": target,
            "sentence": fact_sentence(rel, event_name, target),
        })
    return facts
