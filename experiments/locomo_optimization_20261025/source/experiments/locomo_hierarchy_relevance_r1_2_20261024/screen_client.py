"""R1.2 screening client: Bearer auth, error taxonomy, centre-first rendering,
strict JSON validation and per-question persistence. No batch run here.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

R12 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024")
BENCH = Path(r"F:\worktable\research\bench")
sys.path.insert(0, str(BENCH))

STATUS = {"valid", "auth_error", "network_error", "http_error", "json_error", "invalid_output", "not_run"}
PROMPT = """You audit retrieval candidates for a question.

Question: {question}

Candidates (the centre fact is listed first and is always fully visible):
{candidates}

For each candidate return an object with:
- id: the given id
- supported: true only if the text supports itself without invented facts
- relevance: direct | bridge | context | unrelated
- covers: short slot names it covers

Return ONLY a JSON array of objects. No prose.
"""


def endpoints() -> dict:
    from runners.aml_run import _score_endpoints

    e = _score_endpoints()
    return {"base": e["judge_base"].rstrip("/"), "key": e["judge_key"], "model": e["judge_model"]}


def render_candidate(cid: str, centre_text: str, context_texts: list[str], budget: int = 420) -> str:
    """Centre first; context only if budget remains."""
    text = f"[{cid}] CENTRE: {centre_text}"
    for ctx in context_texts:
        room = budget - len(text)
        if room <= 40:
            break
        text += f" | ctx: {ctx[:room - 10]}"
    return text[:budget]


class ScreenClient:
    def __init__(self, persist_path: Path | None = None):
        self.cfg = endpoints()
        self.persist = persist_path
        self.attempts = 0

    def screen(self, question: str, candidates: list[dict]) -> dict:
        """candidates: [{id, centre, context[]}]; returns status + parsed verdicts."""
        prompt = PROMPT.format(
            question=question,
            candidates="\n".join(render_candidate(c["id"], c["centre"], c.get("context") or []) for c in candidates),
        )
        record = {
            "at": time.time(), "model": self.cfg["model"],
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "candidates": [c["id"] for c in candidates], "status": "not_run",
        }
        try:
            r = httpx.post(f"{self.cfg['base']}/chat/completions", timeout=120, trust_env=False,
                           headers={"Authorization": f"Bearer {self.cfg['key']}", "Content-Type": "application/json"},
                           json={"model": self.cfg["model"], "temperature": 0,
                                 "messages": [{"role": "user", "content": prompt}]})
            self.attempts += 1
            record["http"] = r.status_code
            if r.status_code in (401, 403):
                record["status"] = "auth_error"
            elif r.status_code >= 400:
                record["status"] = "http_error"
                record["body"] = r.text[:200]
            else:
                data = r.json()
                record["response_model"] = data.get("model")
                record["response_id"] = data.get("id")
                record["usage"] = data.get("usage")
                text = data["choices"][0]["message"]["content"]
                start, end = text.find("["), text.rfind("]")
                if start < 0 or end < 0:
                    record["status"] = "json_error"
                    record["raw"] = text[:200]
                else:
                    parsed = json.loads(text[start:end + 1])
                    record["verdicts"] = parsed
                    record["status"] = "valid"
        except httpx.HTTPError as exc:
            record["status"] = "network_error"
            record["error"] = type(exc).__name__
        except (ValueError, KeyError) as exc:
            record["status"] = "json_error"
            record["error"] = str(exc)[:200]
        if record["status"] == "valid":
            by_id = {}
            for item in record.get("verdicts") or []:
                if not isinstance(item, dict):
                    continue
                if not isinstance(item.get("supported"), bool):
                    continue
                if item.get("relevance") not in {"direct", "bridge", "context", "unrelated"}:
                    continue
                by_id[item.get("id")] = item
            record["valid_ids"] = sorted(by_id)
            record["invalid_ids"] = sorted(set(record["candidates"]) - set(by_id))
        if self.persist:
            self.persist.parent.mkdir(parents=True, exist_ok=True)
            with self.persist.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record




    def screen_raw(self, prompt: str, ids: list[str]) -> dict:
        """Free-form prompt returning a JSON object; ids are validated against the pool."""
        record = {"at": time.time(), "model": self.cfg["model"],
                  "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                  "candidates": ids, "status": "not_run"}
        try:
            r = httpx.post(f"{self.cfg['base']}/chat/completions", timeout=120, trust_env=False,
                           headers={"Authorization": f"Bearer {self.cfg['key']}", "Content-Type": "application/json"},
                           json={"model": self.cfg["model"], "temperature": 0,
                                 "messages": [{"role": "user", "content": prompt}]})
            self.attempts += 1
            record["http"] = r.status_code
            if r.status_code in (401, 403):
                record["status"] = "auth_error"
            elif r.status_code >= 400:
                record["status"] = "http_error"
                record["body"] = r.text[:200]
            else:
                data = r.json()
                record["response_model"] = data.get("model")
                record["usage"] = data.get("usage")
                text = data["choices"][0]["message"]["content"]
                start, end = text.find("{"), text.rfind("}")
                if start < 0 or end < 0:
                    record["status"] = "json_error"
                    record["raw"] = text[:200]
                else:
                    parsed = json.loads(text[start:end + 1])
                    if not isinstance(parsed, dict):
                        record["status"] = "invalid_output"
                    else:
                        sel = parsed.get("selected_optional_window_ids") or []
                        rem = parsed.get("removed_base_tail_ids") or []
                        if not isinstance(sel, list) or not isinstance(rem, list) or not set(sel) <= set(ids):
                            record["status"] = "invalid_output"
                        else:
                            record["parsed"] = parsed
                            record["status"] = "valid"
        except httpx.HTTPError as exc:
            record["status"] = "network_error"
            record["error"] = type(exc).__name__
        except (ValueError, KeyError) as exc:
            record["status"] = "json_error"
            record["error"] = str(exc)[:200]
        if self.persist:
            self.persist.parent.mkdir(parents=True, exist_ok=True)
            with self.persist.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\\n")
        return record



if __name__ == "__main__":
    client = ScreenClient(R12 / "screen.jsonl")
    record = client.screen("Which games did Jolene recommend?",
                           [{"id": "c1", "centre": "Jolene recommended Overcooked 2 to Deborah."}])
    print(json.dumps({k: record.get(k) for k in ("status", "http", "valid_ids", "invalid_ids")}, ensure_ascii=False))
