# State Schema Migration Log

Single source of truth for the on-disk schema version stamped onto every
dict-shaped state JSON file.

## Current version

`SCHEMA_VERSION = "1.0"` — defined in [`scripts/_schema.py`](../../scripts/_schema.py).

## Versioned files

These dict-shaped JSON files carry a top-level `schemaVersion` field:

- `story/state/chapter_summaries.json`
- `story/state/current_state.json`
- `story/state/hooks.json`
- `story/state/manifest.json`
- `story/runtime/chapter-{NNNN}.audit-r{i}.json` — per-write, stamped by
  `audit_round_log.py`

## Files explicitly **not** versioned

- `chapters/index.json` — top-level array; the file's existence implies
  version 1.0. (Mirrors inkos's `index.json` convention.) If the array
  shape ever changes, the file's structure itself is the migration signal.
- `story/runtime/chapter-{NNNN}.delta.json` — transient Settler delta;
  schema documented in [`runtime-state-delta.md`](runtime-state-delta.md).
  Re-generated each chapter, so versioning would be noise.

## Bump policy: `MAJOR.MINOR`

- **MAJOR** — breaks readers. Examples: rename or remove a required field,
  change the type of an existing field, change accepted enum values,
  re-shape a nested structure. Old tools must refuse to read; a migration
  tool is required.
- **MINOR** — additive only. Examples: new optional field, new enum value
  that readers can ignore. Old tools keep working.

When you bump:

1. Update `SCHEMA_VERSION` in `scripts/_schema.py`.
2. Update template files under `templates/story/state/` so new books are
   seeded at the new version.
3. Add a section below describing the change and the migration steps.
4. Verify `python scripts/doctor.py --book <existing-book>` flags pre-bump
   books with the expected `state schema version` warning.

## TODO: 1.0 → 1.1 migrations

(empty — fill in when the first MINOR bump lands.)

## TODO: 1.x → 2.0 migrations

(empty — fill in when the first MAJOR bump lands. Will require a
`scripts/migrate_schema.py` tool and a documented runbook.)
