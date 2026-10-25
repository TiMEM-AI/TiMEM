"""
LoCoMo dataset loader.

Downloads from HuggingFace and normalizes into LoCoMoPersona objects.
Parsing is defensive — adjust `_parse_row` if your dataset revision differs.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable

from models.records import LoCoMoMessage, LoCoMoPersona, LoCoMoQA, LoCoMoSession
from utils.config import load_yaml_config
from utils.ids import session_id_for
from utils.locomo_dates import message_timestamps, parse_locomo_datetime

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = PROJECT_ROOT / "benchmark_data" / "fixtures" / "sample_persona.json"
LOCOMO_GITHUB_URL = (
    "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
)
LOCOMO_CACHE_PATH = PROJECT_ROOT / "benchmark_data" / "cache" / "locomo10.json"

# Set by last load_locomo_personas() call (for UI warnings)
_last_load_info: dict[str, Any] = {"source": "fixture", "error": None}


def get_last_load_info() -> dict[str, Any]:
    return dict(_last_load_info)


def load_locomo_personas(
    persona_count: int | None = None,
    *,
    dataset_id: str | None = None,
    split: str | None = None,
    use_fixture: bool = False,
) -> list[LoCoMoPersona]:
    global _last_load_info
    cfg = load_yaml_config().get("locomo", {})
    persona_count = persona_count or int(cfg.get("persona_count", 10))
    dataset_id = dataset_id or cfg.get("dataset_id", "snap-stanford/LoCoMo")
    split = split or cfg.get("split", "test")

    if use_fixture:
        _last_load_info = {"source": "fixture", "error": None, "requested": persona_count}
        logger.info("Using local fixture dataset")
        return _load_fixture(persona_count)

    errors: list[str] = []

    try:
        personas = _load_locomo_github(persona_count)
        if personas:
            _last_load_info = {
                "source": "github",
                "error": None,
                "requested": persona_count,
                "url": LOCOMO_GITHUB_URL,
            }
            logger.info("Loaded %d LoCoMo personas from GitHub", len(personas))
            return personas
    except Exception as exc:
        errors.append(f"GitHub: {exc}")
        logger.warning("Failed to load LoCoMo from GitHub: %s", exc)

    if _hf_available():
        try:
            from datasets import load_dataset

            ds = load_dataset(dataset_id, split=split)
            personas = _parse_hf_dataset(ds, persona_count)
            if personas:
                _last_load_info = {
                    "source": "huggingface",
                    "error": None,
                    "requested": persona_count,
                    "dataset_id": dataset_id,
                }
                return personas
            errors.append("HuggingFace: empty parse")
            logger.warning("HF parse returned empty")
        except Exception as exc:
            errors.append(f"HuggingFace: {exc}")
            logger.warning("Failed to load LoCoMo from HuggingFace (%s): %s", dataset_id, exc)

    _last_load_info = {
        "source": "fixture",
        "error": "; ".join(errors) or "LoCoMo unavailable",
        "requested": persona_count,
    }
    logger.warning("Falling back to local fixture (%s)", _last_load_info["error"])
    return _load_fixture(persona_count)


def _hf_available() -> bool:
    try:
        from datasets import load_dataset  # noqa: F401

        return True
    except ImportError:
        return False


def _parse_hf_dataset(ds: Any, persona_count: int) -> list[LoCoMoPersona]:
    """Parse HuggingFace dataset rows into personas."""
    by_persona: dict[str, dict[str, Any]] = {}

    for idx, row in enumerate(ds):
        if len(by_persona) >= persona_count and idx > persona_count * 20:
            break
        parsed = _parse_row(row, idx)
        if not parsed:
            continue
        persona_id = parsed["persona_id"]
        bucket = by_persona.setdefault(
            persona_id,
            {"sessions": {}, "qa_pairs": []},
        )
        if parsed.get("session"):
            sid = parsed["session"]["session_id"]
            bucket["sessions"][sid] = parsed["session"]
        for qa in parsed.get("qa_pairs", []):
            bucket["qa_pairs"].append(qa)

    personas: list[LoCoMoPersona] = []
    for pid in sorted(by_persona.keys())[:persona_count]:
        bucket = by_persona[pid]
        sessions = list(bucket["sessions"].values())
        sessions.sort(key=lambda s: s.session_id)
        personas.append(
            LoCoMoPersona(
                persona_id=pid,
                sessions=sessions,
                qa_pairs=bucket["qa_pairs"],
            )
        )
    return personas


def _parse_row(row: dict[str, Any], idx: int) -> dict[str, Any] | None:
    """Best-effort row parser for common LoCoMo-like schemas."""
    persona_id = (
        row.get("sample_id")
        or row.get("persona_id")
        or row.get("conversation_id")
        or row.get("id")
        or f"locomo_persona_{idx:02d}"
    )
    persona_id = str(persona_id)

    conv = row.get("conversation") or row.get("dialogue") or row.get("messages")
    sessions_field = row.get("sessions") or row.get("session_list")
    persona_speakers: list[str] = []
    if conv is not None:
        persona_speakers.extend(_speakers_from_container(conv))
    if sessions_field is not None:
        for sess in _as_list(sessions_field):
            persona_speakers.extend(_speakers_from_container(sess))
    speaker_map = _build_persona_speaker_map(persona_speakers)

    # --- conversation / session messages ---
    session: LoCoMoSession | None = None
    extra_sessions: list[LoCoMoSession] = []
    if conv is not None:
        if isinstance(conv, dict) and _is_official_conversation(conv):
            official = _sessions_from_official_conv(conv, persona_id, speaker_map=speaker_map)
            if official:
                session = official[0]
                extra_from_conv = official[1:]
                if extra_from_conv:
                    extra_sessions.extend(extra_from_conv)
        else:
            started_at = _session_started_at_from_container(conv)
            messages = _extract_messages(conv, started_at=started_at, speaker_map=speaker_map)
            if messages:
                session = LoCoMoSession(
                    session_id=session_id_for(persona_id, 0),
                    persona_id=persona_id,
                    messages=messages,
                    started_at=started_at,
                )

    # multi-session field
    if sessions_field is not None:
        for sidx, sess in enumerate(_as_list(sessions_field)):
            started_at = _session_started_at_from_container(sess)
            messages = _extract_messages(sess, started_at=started_at, speaker_map=speaker_map)
            if messages:
                extra_sessions.append(
                    LoCoMoSession(
                        session_id=session_id_for(persona_id, sidx),
                        persona_id=persona_id,
                        messages=messages,
                        started_at=started_at,
                    )
                )

    chosen_session = extra_sessions[0] if extra_sessions else session

    # --- QA ---
    qa_pairs: list[LoCoMoQA] = []
    qa_field = row.get("qa") or row.get("question") or row.get("qa_pairs")
    if isinstance(qa_field, list):
        for qidx, qa in enumerate(qa_field):
            if not isinstance(qa, dict):
                continue
            question = qa.get("question") or qa.get("query")
            answer = qa.get("answer") or qa.get("gold") or qa.get("response")
            if question and answer:
                qa_pairs.append(
                    LoCoMoQA(
                        persona_id=persona_id,
                        question=str(question),
                        answer=str(answer),
                        session_id=qa.get("session_id"),
                        category=str(qa.get("category")) if qa.get("category") else None,
                    )
                )
    elif isinstance(row.get("question"), str) and isinstance(row.get("answer"), str):
        qa_pairs.append(
            LoCoMoQA(
                persona_id=persona_id,
                question=row["question"],
                answer=row["answer"],
            )
        )

    if not chosen_session and not qa_pairs:
        return None

    result: dict[str, Any] = {"persona_id": persona_id, "qa_pairs": qa_pairs}
    if chosen_session:
        result["session"] = chosen_session
    for s in extra_sessions[1:]:
        result.setdefault("extra_sessions", []).append(s)
    return result


_DIALOGUE_ROLES = ("user1", "user2")
_LEGACY_DIALOGUE_ROLE_KEYS = frozenset(
    {"user", "user1", "user2", "assistant", "agent", "bot", "system"}
)


def _is_named_speaker(value: str) -> bool:
    return value.strip().casefold() not in _LEGACY_DIALOGUE_ROLE_KEYS


def _speakers_from_turns(turns: Any) -> list[str]:
    speakers: list[str] = []
    if not isinstance(turns, list):
        return speakers
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        for key in ("speaker", "role"):
            raw = turn.get(key)
            if raw and _is_named_speaker(str(raw)):
                speakers.append(str(raw).strip())
                break
    return speakers


def _speakers_from_container(container: Any) -> list[str]:
    if isinstance(container, str):
        try:
            container = json.loads(container)
        except json.JSONDecodeError:
            return []
    if isinstance(container, list):
        return _speakers_from_turns(container)
    if isinstance(container, dict):
        if _is_official_conversation(container):
            return _collect_speakers_from_official_conv(container)
        turns = container.get("messages") or container.get("turns") or container.get("dialogue") or []
        return _speakers_from_turns(turns)
    return []


def _collect_speakers_from_official_conv(conv: dict[str, Any]) -> list[str]:
    speakers: list[str] = []
    for key, value in conv.items():
        if re.match(r"session_\d+$", str(key)):
            speakers.extend(_speakers_from_turns(value))
    return speakers


def _build_persona_speaker_map(speakers: Iterable[str]) -> dict[str, str]:
    """Stable user1/user2 for a persona: speakers sorted alphabetically (case-insensitive)."""
    unique: list[str] = []
    seen: set[str] = set()
    for raw in speakers:
        name = str(raw).strip()
        if not name or not _is_named_speaker(name):
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    unique.sort(key=str.casefold)

    mapping: dict[str, str] = {}
    for idx, name in enumerate(unique):
        mapping[name.casefold()] = _DIALOGUE_ROLES[min(idx, len(_DIALOGUE_ROLES) - 1)]
    return mapping


def _map_dialogue_role(role_or_speaker: str, speaker_map: dict[str, str]) -> str:
    """Map LoCoMo speakers / legacy roles to persona-stable user1 & user2."""
    raw = str(role_or_speaker).strip()
    r = raw.lower()
    if r in _DIALOGUE_ROLES:
        return r
    if r == "system":
        return "system"
    if r in {"assistant", "agent", "bot"}:
        return "user2"
    if r == "user":
        return "user1"

    key = raw.casefold()
    if key in speaker_map:
        return speaker_map[key]
    return "user2"


def _extract_messages(
    conv: Any,
    *,
    started_at: str | None = None,
    speaker_map: dict[str, str] | None = None,
) -> list[LoCoMoMessage]:
    messages: list[LoCoMoMessage] = []
    if isinstance(conv, str):
        try:
            conv = json.loads(conv)
        except json.JSONDecodeError:
            return [LoCoMoMessage(role="user1", content=conv)]

    if isinstance(conv, dict):
        turns = conv.get("messages") or conv.get("turns") or conv.get("dialogue") or []
    elif isinstance(conv, list):
        turns = conv
    else:
        return messages

    timestamps: list[str] = []
    if started_at and turns:
        timestamps = message_timestamps(started_at, len(turns))

    resolved_map = speaker_map or _build_persona_speaker_map(_speakers_from_turns(turns))
    for idx, turn in enumerate(turns):
        ts = timestamps[idx] if idx < len(timestamps) else None
        if isinstance(turn, dict):
            raw_label = turn.get("speaker") or turn.get("role") or "user"
            content = turn.get("content") or turn.get("text") or turn.get("utterance") or ""
            if content:
                label = str(raw_label).strip()
                speaker = label if _is_named_speaker(label) else None
                messages.append(
                    LoCoMoMessage(
                        role=_map_dialogue_role(label, resolved_map),
                        content=str(content),
                        timestamp=ts,
                        speaker=speaker,
                    )
                )
        elif isinstance(turn, str):
            messages.append(LoCoMoMessage(role="user1", content=turn, timestamp=ts))
    return messages


def _is_official_conversation(conv: dict[str, Any]) -> bool:
    return any(re.match(r"session_\d+$", str(key)) for key in conv)


def _session_started_at_from_container(container: Any) -> str | None:
    if not isinstance(container, dict):
        return None
    for key in ("date_time", "started_at", "session_date_time"):
        raw = container.get(key)
        if raw:
            return parse_locomo_datetime(str(raw))
    return None


def _sessions_from_official_conv(
    conv: dict[str, Any],
    persona_id: str,
    *,
    speaker_map: dict[str, str] | None = None,
) -> list[LoCoMoSession]:
    session_nums = sorted(
        int(m.group(1))
        for key in conv
        if (m := re.match(r"session_(\d+)$", str(key)))
    )
    resolved_map = speaker_map or _build_persona_speaker_map(_collect_speakers_from_official_conv(conv))
    sessions: list[LoCoMoSession] = []
    for sidx, num in enumerate(session_nums):
        date_raw = conv.get(f"session_{num}_date_time")
        started_at = parse_locomo_datetime(str(date_raw)) if date_raw else None
        messages = _extract_messages(
            conv.get(f"session_{num}"),
            started_at=started_at,
            speaker_map=resolved_map,
        )
        if messages:
            sessions.append(
                LoCoMoSession(
                    session_id=session_id_for(persona_id, sidx),
                    persona_id=persona_id,
                    messages=messages,
                    started_at=started_at,
                )
            )
    return sessions


def _as_list(value: Any) -> Iterable[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _load_locomo_github(persona_count: int) -> list[LoCoMoPersona]:
    if LOCOMO_CACHE_PATH.exists():
        with open(LOCOMO_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    else:
        import httpx

        logger.info("Downloading LoCoMo from %s", LOCOMO_GITHUB_URL)
        resp = httpx.get(LOCOMO_GITHUB_URL, timeout=120.0, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        LOCOMO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCOMO_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)

    if not isinstance(data, list):
        raise ValueError("locomo10.json must be a list of conversations")

    personas: list[LoCoMoPersona] = []
    for idx, row in enumerate(data[:persona_count]):
        if not isinstance(row, dict):
            continue
        persona = _parse_official_locomo_row(row, idx)
        if persona:
            personas.append(persona)
    if not personas:
        raise ValueError("No personas parsed from locomo10.json")
    return personas


def _parse_official_locomo_row(row: dict[str, Any], idx: int) -> LoCoMoPersona | None:
    persona_id = str(row.get("sample_id") or row.get("id") or f"locomo_persona_{idx:02d}")
    conv = row.get("conversation")
    if not isinstance(conv, dict):
        return None

    sessions = _sessions_from_official_conv(conv, persona_id)

    qa_pairs: list[LoCoMoQA] = []
    for qa in row.get("qa") or []:
        if not isinstance(qa, dict):
            continue
        question = qa.get("question")
        answer = qa.get("answer")
        if question and answer:
            qa_pairs.append(
                LoCoMoQA(
                    persona_id=persona_id,
                    question=str(question),
                    answer=str(answer),
                    category=str(qa.get("category")) if qa.get("category") else None,
                )
            )

    if not sessions and not qa_pairs:
        return None
    return LoCoMoPersona(persona_id=persona_id, sessions=sessions, qa_pairs=qa_pairs)


def _load_fixture(persona_count: int) -> list[LoCoMoPersona]:
    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(
            f"Fixture not found: {FIXTURE_PATH}. "
            "Install datasets and configure HuggingFace, or add sample_persona.json."
        )
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    personas = []
    for item in data[:persona_count]:
        sessions = [
            LoCoMoSession(
                session_id=s["session_id"],
                persona_id=item["persona_id"],
                messages=[LoCoMoMessage(**m) for m in s["messages"]],
                started_at=s.get("started_at"),
            )
            for s in item.get("sessions", [])
        ]
        qa_pairs = [
            LoCoMoQA(
                persona_id=item["persona_id"],
                question=q["question"],
                answer=q["answer"],
                session_id=q.get("session_id"),
                category=q.get("category"),
            )
            for q in item.get("qa_pairs", [])
        ]
        personas.append(LoCoMoPersona(persona_id=item["persona_id"], sessions=sessions, qa_pairs=qa_pairs))
    return personas
