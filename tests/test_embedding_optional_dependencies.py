"""API-backed embedding imports must not require optional local model libraries."""

import subprocess
import sys
from pathlib import Path


def test_remote_embedding_module_does_not_import_qwen_local():
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import llm.embedding_service; assert 'llm.qwen_embedding_adapter' not in sys.modules",
        ],
        check=False,
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
