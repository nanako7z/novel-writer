"""Schema version constants for novel-writer state files.

Single source of truth for the on-disk schema version. We adopt inkos's
versioning scheme: schemaVersion lives **only on the manifest**, as a
*number* matching ``z.literal(2)`` in inkos's ``StateManifestSchema``. Other
state files (hooks/current_state/chapter_summaries) do **not** carry a
schemaVersion field — they are versioned implicitly by the manifest in
the same book.

Bump policy:

  * MAJOR (e.g., 2 → 3) — breaks readers (renamed/removed required field,
    changed enum values, changed type of an existing field). Old tools
    must refuse to read; a migration tool must rewrite the files.
  * MINOR is not used because inkos's manifest schema declares the version
    as ``z.literal(2)`` (a single integer). Additive changes ride on the
    same major; new fields go through ``migrationWarnings`` instead.

When you bump this constant, document the migration in
``references/schemas/migration-log.md`` BEFORE shipping.

Versioned files (single field on a single file):

    story/state/manifest.json   — number, top-level ``schemaVersion``

Implicitly versioned (no per-file field, governed by the manifest):

    story/state/hooks.json
    story/state/current_state.json
    story/state/chapter_summaries.json

Files explicitly NOT versioned (no place for a top-level field):

    chapters/index.json — top-level array; the file's existence implies
        version 2 (per inkos's index.json convention).
    story/runtime/chapter-{NNNN}.delta.json — transient Settler delta;
        schema documented separately in references/schemas/runtime-state-delta.md.
    story/runtime/chapter-{NNNN}.audit-r{i}.json — runtime artifact; the
        wrapper carries a ``schemaVersion`` for the audit-round shape only,
        independent of the truth-file SCHEMA_VERSION.
"""
from __future__ import annotations

SCHEMA_VERSION = 2  # number, matches inkos StateManifestSchema z.literal(2)

# Dict-shaped state files that carry a top-level "schemaVersion" field.
# Only the manifest, since inkos governs the rest implicitly.
STATE_FILES_WITH_VERSION = [
    "story/state/manifest.json",
]
