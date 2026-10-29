"""Apply an explicitly selected, prompt-version-bound L1 policy.

No optimized policy is enabled by default. A mismatch fails before an LLM call.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def apply_l1_policy(formatted_prompt: str, template: str | None) -> str:
    path = os.environ.get("TIMEM_L1_OPT_POLICY_PATH")
    if not path:
        return formatted_prompt
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    if artifact.get("version") != 1:
        raise ValueError("Unsupported L1 optimization policy version")
    if not isinstance(template, str):
        raise TypeError("L1 prompt template text is required for policy validation")
    expected = hashlib.sha256(template.encode("utf-8")).hexdigest()
    if artifact.get("base_template_sha256") != expected:
        raise ValueError(
            "L1 optimization policy was trained against a different prompt template"
        )
    policy = artifact.get("policy")
    if (
        not isinstance(policy, str)
        or len(policy) > 2000
        or "{" in policy
        or "}" in policy
    ):
        raise ValueError("Invalid L1 optimization policy")
    return (
        formatted_prompt
        if not policy
        else formatted_prompt + "\n\nAdditional preservation guidance:\n" + policy
    )
