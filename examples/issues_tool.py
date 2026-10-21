"""Tiny deterministic helper for examples/maintainer.ai.

Zero network: it only reads a local file and returns JSON.
"""

import json
from pathlib import Path


def read_lines(path: str) -> str:
    """Read an issues file, skip blanks/# comments, return a JSON array."""
    p = Path(str(path))
    lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    return json.dumps(lines, ensure_ascii=False)
