# State Schema Migration Log

Single source of truth for the on-disk state schema version. We adopt
inkos's versioning scheme verbatim: schemaVersion is a *number* matching
`z.literal(2)` in inkos's `StateManifestSchema`, and lives **only on the
manifest**. Other state files are versioned implicitly.

## Current version

`SCHEMA_VERSION = 2` — defined in [`scripts/_schema.py`](../../scripts/_schema.py).

## File-by-file shape (matches inkos `models/runtime-state.ts`)

| File | Top-level shape | Has `schemaVersion`? |
|---|---|---|
| `story/state/manifest.json` | `{schemaVersion: 2, language, lastAppliedChapter, projectionVersion, migrationWarnings}` | **yes** (number 2) |
| `story/state/current_state.json` | `{chapter, facts: [...]}` | no |
| `story/state/hooks.json` | `{hooks: [...]}` | no |
| `story/state/chapter_summaries.json` | `{rows: [...]}` | no |
| `chapters/index.json` | top-level array of `ChapterMeta` | no |
| `story/runtime/chapter-{NNNN}.audit-r{i}.json` | dict | yes (own version, runtime-only) |
| `story/runtime/chapter-{NNNN}.delta.json` | transient Settler delta | no |

## Bump policy

inkos's manifest schema declares the version as `z.literal(2)` — a single
integer. We follow the same convention:

- **MAJOR** (e.g., 2 → 3) — breaks readers. Examples: rename or remove a
  required field, change the type of an existing field, change accepted
  enum values, re-shape a nested structure. Old tools must refuse to
  read; a migration tool is required.
- We don't use a MINOR component. Additive changes ride on the same
  major; new fields go through `migrationWarnings` instead.

When you bump:

1. Update `SCHEMA_VERSION` in `scripts/_schema.py` (number, not string).
2. Update template files under `templates/story/state/` so new books
   seed at the new version.
3. Add a section below describing the change and the migration steps.
4. Verify `python scripts/doctor.py --book <existing-book>` flags
   pre-bump books with the expected `manifest schema version` warning.

## Past migration: legacy `"1.0"` (string) → `2` (number)

**Why**: the original SKILL implementation used a custom `"1.0"` string
on every dict-shaped state file. inkos's strict zod schema expects:

- `schemaVersion: z.literal(2)` (number) on `manifest.json` only.
- No `schemaVersion` field on `hooks.json` / `current_state.json` /
  `chapter_summaries.json`.

Aligning with inkos lets the same on-disk book be read by both tools
without a migration step.

**Self-healing path** (already wired):

- `apply_delta.py:ensure_manifest_schema_version` — reads manifest, on
  legacy `"1.0"` string writes back `2` and emits a one-time warning.
- `apply_delta.py:strip_legacy_schema_version` — on next write of any
  non-manifest state file, strips a stray `schemaVersion` field with a
  warning.
- Backward-read of `chapter_summaries.json` wrapper key
  (`summaries` → `rows`): every consuming script tries `rows` first,
  falls back to `summaries`. On next apply_delta write, the key is
  migrated to `rows` and a warning logged.

**No manual migration needed**: any pre-existing book is auto-migrated
on its next chapter persist. Books already on v2 (newly initialized
post-this-commit) start clean.

## TODO: 2 → 3 migrations

(empty — fill in when the first MAJOR bump lands. Will require a
`scripts/migrate_schema.py` tool and a documented runbook.)
