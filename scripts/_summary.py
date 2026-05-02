"""One-line summary to stderr for human readers.

Scripts whose primary output is JSON-on-stdout call ``emit_summary`` after the
main work succeeds (or fails) so that a human running the script directly sees
a quick status line, while pipe consumers (``... | jq ...``) still get clean
stdout JSON. Suppressible via ``2>/dev/null``.
"""
from __future__ import annotations

import sys


def emit_summary(text: str, *, prefix: str = "summary") -> None:
    """Write a single-line human summary to stderr.

    Format: ``[prefix] text`` on one line, newline-terminated.
    Truncates text to 200 chars + '...' if longer (terminal-friendly).
    Stays out of stdout so JSON callers are unaffected.
    """
    if not text:
        return
    text = text.strip().replace("\n", " ")
    if len(text) > 200:
        text = text[:197] + "..."
    print(f"[{prefix}] {text}", file=sys.stderr, flush=True)
