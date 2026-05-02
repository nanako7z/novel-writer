"""Schema version constants for novel-writer state files.

Single source of truth for the on-disk schema version stamped onto every
dict-shaped state JSON. Importable, pure data, no side effects.

Bump policy: ``MAJOR.MINOR``.

  * MAJOR — breaks readers (renamed/removed required field, changed enum
    values, changed type of an existing field). Old tools must refuse to
    read; a migration tool must rewrite the files.
  * MINOR — additive only (new optional field, new enum value that
    readers can ignore). Old tools keep working.

When you bump this constant, document the migration in
``references/schemas/migration-log.md`` BEFORE shipping.

Files versioned via this constant:

    story/state/chapter_summaries.json     (dict with `summaries: []`)
    story/state/current_state.json         (dict with `facts: []`)
    story/state/hooks.json                 (dict with `hooks: []`)
    story/state/manifest.json              (dict)
    story/runtime/chapter-{NNNN}.audit-r{i}.json   (per-write)

Files explicitly NOT versioned (no place for a top-level field):

    chapters/index.json — top-level array; the file's existence implies
        version 1.0. (Mirrors inkos's index.json convention.)
    story/runtime/chapter-{NNNN}.delta.json — transient Settler delta;
        schema documented separately in references/schemas/runtime-state-delta.md.
"""
from __future__ import annotations

SCHEMA_VERSION = "1.0"

# Dict-shaped state files that should carry a top-level "schemaVersion" key.
# Audit round artifacts (chapter-{NNNN}.audit-r{i}.json) are stamped per-write
# in audit_round_log.cmd_write — they're not in this list because their paths
# aren't fixed.
STATE_FILES_WITH_VERSION = [
    "story/state/chapter_summaries.json",
    "story/state/current_state.json",
    "story/state/hooks.json",
    "story/state/manifest.json",
]
