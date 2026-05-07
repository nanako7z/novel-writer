#!/usr/bin/env python3
"""docOps — apply LLM-driven edits to author-domain markdown guidance files.

Sister module to apply_delta.py's stateOps/hookOps. Handles three op kinds:

  1. SectionReplaceOp — H2/H3 section-level edits to prose-style files
     (current_focus.md, style_guide.md, outline/*.md, roles/*.md).
  2. TableRowOp        — upsert/update/delete on markdown-table files
     (character_matrix.md, emotional_arcs.md, subplot_board.md).
  3. RoleFileOp        — patch_role_section / rename_role on roles/<slug>.md.

Writes are atomic via .tmp + rename; every op is backed up to
`story/runtime/doc_ops.bak/<NNNN>/<filename>.<seq>.bak` before being applied,
and an NDJSON entry is appended to `story/runtime/doc_changes.log`.

Whitelist / blacklist enforcement happens here as a defense-in-depth layer;
settler_parse.validate_delta also rejects blacklist targets at the schema
stage, but apply-time enforcement guarantees that bypassing the schema
(e.g. forged delta JSON) still cannot touch author constitution files.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# ──────────────────────────── routing tables ───────────────────────────────

# Whitelist: docOps top-level keys → story/-relative paths.
DOC_OPS_WHITELIST: dict[str, str] = {
    "currentFocus":    "story/current_focus.md",
    "styleGuide":      "story/style_guide.md",
    "storyFrame":      "story/outline/story_frame.md",
    "volumeMap":       "story/outline/volume_map.md",
    "characterMatrix": "story/character_matrix.md",
    "emotionalArcs":   "story/emotional_arcs.md",
    "subplotBoard":    "story/subplot_board.md",
    # roles is special — slug-resolved per op, see _resolve_role_path
    "roles":           "story/roles/{slug}.md",
}

# Blacklist: paths that NEVER accept docOps writes (author constitution).
# Keys are book-relative paths; presence of a forbidden top-level key in
# delta.docOps also triggers schema-fail.
DOC_OPS_BLACKLIST: frozenset[str] = frozenset({
    "story/author_intent.md",
    "story/fanfic_canon.md",
    "story/parent_canon.md",
    "book.json#bookRules",
})

# Per-target newContent character caps (defense against runaway LLM writes).
# Keyed by docOps key. RoleFileOp uses ROLE_CAP.
NEW_CONTENT_CAP: dict[str, int] = {
    "currentFocus":    2000,
    "styleGuide":      3000,
    "storyFrame":      5000,
    "volumeMap":       5000,
    "characterMatrix":  500,   # per-row notes field; the *row* itself is small
    "emotionalArcs":    500,
    "subplotBoard":     500,
}
ROLE_CAP = 4000
REASON_CAP = 200
MAX_OPS_PER_BATCH = 20

# Section ops apply to these targets; table ops apply to those.
SECTION_TARGETS = {"currentFocus", "styleGuide", "storyFrame", "volumeMap"}
TABLE_TARGETS = {"characterMatrix", "emotionalArcs", "subplotBoard"}
ROLE_TARGET = "roles"

# Table key columns by target (used to identify upsert-by-key).
# These must align with the markdown column order in templates/story/<file>.md.
TABLE_KEY_COLUMNS: dict[str, tuple[int, ...]] = {
    "characterMatrix": (0, 1),   # charA, charB
    "emotionalArcs":   (0, 1),   # character, chapter
    "subplotBoard":    (0,),     # subplotId
}

# Canonical column NAMES for the key columns above. Used when bootstrapping
# a brand-new table file via upsert_row to guarantee the key columns end up
# at the indices declared in TABLE_KEY_COLUMNS — without this, header order
# == list(fields.keys()) and the first non-key field accidentally becomes
# column 0.
_CANONICAL_KEY_COLS: dict[str, tuple[str, ...]] = {
    "characterMatrix": ("charA", "charB"),
    "emotionalArcs":   ("character", "chapter"),
    "subplotBoard":    ("subplotId",),
}

# Optional column order for emit (None → preserve incoming header).
# Headers come from the existing file when present.

VALID_SOURCE_PHASES = frozenset({
    "settler",
    "auditor-derived",
    "architect",
    "user-directive",
})

VALID_SECTION_OPS = frozenset({
    "replace_section",
    "append_section",
    "delete_section",
})
VALID_TABLE_OPS = frozenset({"upsert_row", "update_row", "delete_row"})
VALID_ROLE_OPS = frozenset({
    "create_role", "patch_role_section", "rename_role", "delete_role",
})

VALID_ROLE_TIERS = ("主要角色", "次要角色")
DEFAULT_ROLE_TIER = "次要角色"

# Roles template — shipped at templates/story/roles/_template.md
_ROLE_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "templates" / "story" / "roles" / "_template.md"
)


# ──────────────────────────── tiny helpers ─────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _atomic_write_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def _op_id(file_rel: str, anchor: str | None, applied_at: str) -> str:
    payload = f"{file_rel}|{anchor or ''}|{applied_at}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _backup_path(book: Path, chapter: int | None, file_rel: str, seq: int) -> Path:
    chap = f"{int(chapter):04d}" if isinstance(chapter, int) and chapter > 0 else "0000"
    safe_name = file_rel.replace("/", "__")
    return book / "story" / "runtime" / "doc_ops.bak" / chap / f"{safe_name}.{seq}.bak"


def _append_doc_changes_log(book: Path, entry: dict) -> None:
    log = book / "story" / "runtime" / "doc_changes.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ──────────────────────────── role path resolver ───────────────────────────


# Slug = filename stem. Architect uses Chinese names directly under
# `story/roles/主要角色/` and `story/roles/次要角色/`; we accept any printable
# string that is filesystem-safe (no `/`, `\`, control chars, no leading dot).
_INVALID_SLUG_CHARS = re.compile(r"[\\/\x00-\x1f]")


def _validate_slug(slug) -> str | None:
    """Return error string if slug is invalid; else None."""
    if not isinstance(slug, str):
        return f"slug must be string, got {type(slug).__name__}"
    s = slug.strip()
    if not s:
        return "slug must be non-empty"
    if s.startswith(".") or s in {".", ".."}:
        return f"slug must not start with '.': {slug!r}"
    if _INVALID_SLUG_CHARS.search(s):
        return f"slug contains forbidden chars (slash/control): {slug!r}"
    if len(s) > 80:
        return f"slug too long ({len(s)} > 80)"
    return None


def _list_existing_role_files(book: Path) -> list[Path]:
    """Return all `story/roles/**/<name>.md` files (recursive, both tiers)."""
    root = book / "story" / "roles"
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in root.rglob("*.md"):
        if p.is_file() and not p.name.startswith("_"):
            out.append(p)
    return out


def _resolve_role_path(book: Path, slug: str) -> Path:
    """Find existing `<slug>.md` under story/roles/** (recursive).

    If multiple matches exist (shouldn't happen but defensive), the first one
    wins by stable ordering. If no match, raise — callers handling create_role
    must compute the path themselves via `_role_create_path()`.
    """
    err = _validate_slug(slug)
    if err:
        raise ValueError(f"invalid role slug: {err}")
    s = slug.strip()
    candidates = [p for p in _list_existing_role_files(book) if p.stem == s]
    if not candidates:
        raise FileNotFoundError(f"role file not found for slug={s!r}")
    return sorted(candidates)[0]


def _role_create_path(book: Path, slug: str, tier: str | None) -> Path:
    err = _validate_slug(slug)
    if err:
        raise ValueError(f"invalid role slug: {err}")
    s = slug.strip()
    chosen_tier = tier if tier in VALID_ROLE_TIERS else DEFAULT_ROLE_TIER
    return book / "story" / "roles" / chosen_tier / f"{s}.md"


def _load_role_template(display_name: str = "") -> str:
    """Return the role file template body with `{{name}}` substituted."""
    body: str
    if _ROLE_TEMPLATE_PATH.is_file():
        try:
            body = _ROLE_TEMPLATE_PATH.read_text(encoding="utf-8")
        except OSError:
            body = ""
    else:
        body = ""
    if not body:
        # fallback skeleton — kept tiny so create_role works without templates
        body = (
            "# {{name}}\n\n"
            "## 核心标签\n\n（一句话定位）\n\n"
            "## 反差细节\n\n（与第一印象不符的小细节）\n\n"
            "## 人物小传（过往经历）\n\n（来历）\n\n"
            "## 当前现状\n\n（首次出场时的状态）\n\n"
        )
    return body.replace("{{name}}", display_name or "")


# ──────────────────────────── section ops ──────────────────────────────────


_HEADING_RE = re.compile(r"(?m)^(#{2,3}) (.+)$")


def _split_into_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (heading_or_empty, body) pairs.

    The first element's heading is "" (preamble before any H2/H3).
    Each subsequent element's heading is the full line including '## ' or '### '.
    Body includes the trailing newline(s) up to the next heading.
    """
    parts: list[tuple[str, str]] = []
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", text)]

    # preamble before the first heading
    if matches[0].start() > 0:
        parts.append(("", text[: matches[0].start()]))

    for i, m in enumerate(matches):
        heading_line = m.group(0)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        parts.append((heading_line, body))

    return parts


def _join_sections(parts: Iterable[tuple[str, str]]) -> str:
    out: list[str] = []
    for heading, body in parts:
        if heading:
            # body already includes its trailing whitespace; ensure newline after heading
            if not body.startswith("\n"):
                out.append(heading + "\n" + body)
            else:
                out.append(heading + body)
        else:
            out.append(body)
    return "".join(out)


def _sanitize_new_content(new_content: str, anchor: str) -> tuple[str, list[str]]:
    """Strip leaked structural artifacts from `newContent`.

    Notices accumulated:
    - `stripped_leading_anchor`: newContent started with the anchor line
      itself (a common LLM mistake — the anchor goes in `anchor`, the
      body goes in `newContent`).  Without stripping, every replace_section
      doubles the heading on disk; on the next read the file's section list
      gets a phantom new entry that future replaces can't reach.
    - `embedded_h2_lines`: newContent contains *other* `## ` / `### ` lines
      mid-stream.  Less harmful than the leading-anchor case but on the
      next read those lines split the section, so we record a warning so
      the caller can flag it.  We do NOT strip these — they may be
      legitimate H4+ misformatted, and silent removal would lose data.
    """
    notices: list[str] = []
    if not isinstance(new_content, str):
        return "", notices

    # Strip leading anchor (and its blank-line padding) if present.
    text = new_content.lstrip("\n")
    anchor_stripped = anchor.strip()
    while True:
        head, sep, rest = text.partition("\n")
        if head.strip() == anchor_stripped:
            text = rest.lstrip("\n")
            notices.append("stripped_leading_anchor")
            continue
        break

    # Detect mid-stream H2/H3 lines that are NOT the original anchor (those
    # we can't detect as accidental — they may be intended sub-sections of
    # the body or genuine other sections; we just flag).
    other_headings = [
        m.group(0) for m in _HEADING_RE.finditer(text)
        if m.group(0).strip() != anchor_stripped
    ]
    if other_headings:
        notices.append(
            f"embedded_h2_lines: {len(other_headings)} non-anchor heading(s) "
            f"in newContent; they will split the section on next read"
        )

    return text, notices


def _apply_section_op(text: str, op: dict) -> tuple[str, str | None, list[str]]:
    """Apply one SectionReplaceOp; return (new_text, error_or_None, notices).

    `notices` is a list of soft-warning strings (e.g. "stripped_leading_anchor")
    that the caller can surface via warnings or doc_changes.log; they don't
    indicate failure.
    """
    notices: list[str] = []
    kind = op.get("op")
    anchor = op.get("anchor")
    new_content_raw = op.get("newContent", "")
    if kind not in VALID_SECTION_OPS:
        return text, f"invalid section op: {kind!r}", notices
    if not isinstance(anchor, str) or not anchor.strip():
        return text, "anchor must be non-empty string", notices
    if not anchor.startswith("## ") and not anchor.startswith("### "):
        return text, "anchor must start with '## ' or '### '", notices

    new_content, sanitize_notices = _sanitize_new_content(new_content_raw, anchor)
    notices.extend(sanitize_notices)

    parts = _split_into_sections(text)
    matching_indices = [
        i for i, (h, _) in enumerate(parts) if h.strip() == anchor.strip()
    ]

    if kind == "replace_section":
        if not matching_indices:
            return text, f"anchor not found: {anchor!r}", notices
        body = new_content if new_content.endswith("\n") else new_content + "\n"
        if not body.startswith("\n"):
            body = "\n" + body
        # Replace the FIRST occurrence with the new body.
        first_idx = matching_indices[0]
        parts[first_idx] = (anchor, body)
        # If duplicates exist (file was already dirty), drop them — keeping
        # multiple same-anchor sections breaks future replaces.  Iterate from
        # tail so indices stay valid.
        if len(matching_indices) > 1:
            for dup_idx in reversed(matching_indices[1:]):
                parts.pop(dup_idx)
            notices.append(
                f"deduped_duplicate_anchors: removed {len(matching_indices) - 1} "
                f"redundant {anchor!r} section(s) to keep the file canonical"
            )
        return _join_sections(parts), None, notices

    if kind == "delete_section":
        if not matching_indices:
            return text, f"anchor not found: {anchor!r}", notices
        # Remove ALL occurrences of the anchor (defensive against duplicates).
        for dup_idx in reversed(matching_indices):
            parts.pop(dup_idx)
        if len(matching_indices) > 1:
            notices.append(
                f"deleted_duplicate_anchors: removed {len(matching_indices)} "
                f"section(s) matching {anchor!r}"
            )
        return _join_sections(parts), None, notices

    # append_section
    if matching_indices:
        return text, f"append_section but anchor already exists: {anchor!r}", notices
    body = new_content if new_content.endswith("\n") else new_content + "\n"
    if not body.startswith("\n"):
        body = "\n" + body
    # append at end of doc; ensure file ends with newline first
    if parts and not parts[-1][1].endswith("\n"):
        last_h, last_b = parts[-1]
        parts[-1] = (last_h, last_b + "\n")
    parts.append((anchor, body))
    return _join_sections(parts), None, notices


# ──────────────────────────── table ops ────────────────────────────────────


_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")


def _parse_table_block(text: str) -> tuple[str, list[str], list[list[str]], str]:
    """Locate the first markdown table in text and return its parts.

    Returns (preamble, header_cells, rows_cells, postamble). Header includes
    only the column names — the separator row (---|---) is dropped on parse
    and re-emitted on render.

    If no table is found, returns ("", [], [], text).
    """
    lines = text.splitlines(keepends=False)
    # find first run of consecutive pipe-lines with a separator second line
    i = 0
    n = len(lines)
    while i < n:
        if _TABLE_LINE_RE.match(lines[i]):
            # require separator on next line
            if i + 1 < n and _TABLE_LINE_RE.match(lines[i + 1]) and re.match(
                r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]
            ):
                # found a table starting at i
                start = i
                end = i + 2
                while end < n and _TABLE_LINE_RE.match(lines[end]):
                    end += 1
                preamble = "\n".join(lines[:start])
                header_line = lines[start]
                rows_block = lines[start + 2 : end]
                postamble = "\n".join(lines[end:])
                header = [c.strip() for c in header_line.strip().strip("|").split("|")]
                rows = [
                    [c.strip() for c in r.strip().strip("|").split("|")]
                    for r in rows_block
                ]
                # preserve trailing newline parity
                if preamble and text.startswith(preamble):
                    preamble = preamble + ("\n" if start > 0 else "")
                if postamble:
                    postamble = "\n" + postamble
                if text.endswith("\n") and not postamble.endswith("\n"):
                    postamble = postamble + "\n"
                return preamble, header, rows, postamble
        i += 1
    return "", [], [], text


def _render_table(header: list[str], rows: list[list[str]]) -> str:
    if not header:
        return ""
    sep = ["---"] * len(header)

    def line(cells: list[str]) -> str:
        # pad row to header width
        c = list(cells) + [""] * max(0, len(header) - len(cells))
        c = c[: len(header)]
        return "| " + " | ".join(c) + " |"

    out = [line(header), line(sep)]
    out.extend(line(r) for r in rows)
    return "\n".join(out) + "\n"


def _row_key(row: list[str], key_cols: tuple[int, ...]) -> tuple[str, ...]:
    return tuple((row[c] if c < len(row) else "") for c in key_cols)


def _normalize_op_key(op_key, key_cols: tuple[int, ...]) -> tuple[str, ...]:
    """Coerce op['key'] into a tuple of strings matching key_cols length."""
    if isinstance(op_key, str):
        op_key = [op_key]
    if not isinstance(op_key, (list, tuple)):
        raise ValueError(f"key must be string or array; got {type(op_key).__name__}")
    if len(op_key) != len(key_cols):
        raise ValueError(
            f"key arity mismatch: expected {len(key_cols)} parts, got {len(op_key)}"
        )
    return tuple(str(k) for k in op_key)


def _apply_table_op(
    text: str, op: dict, key_cols: tuple[int, ...], target: str
) -> tuple[str, str | None, list[str]]:
    """Apply a TableRowOp; return (new_text, error_or_None, notices)."""
    notices: list[str] = []
    kind = op.get("op")
    if kind not in VALID_TABLE_OPS:
        return text, f"invalid table op: {kind!r}", notices

    preamble, header, rows, postamble = _parse_table_block(text)
    if not header:
        # bootstrap: no table yet — only upsert_row may create one (need fields)
        if kind != "upsert_row":
            return text, "table not found in file (only upsert_row may bootstrap)", notices
        fields = op.get("fields") or {}
        if not isinstance(fields, dict) or not fields:
            return text, "upsert_row on empty file requires non-empty 'fields' dict", notices
        # Bootstrap header: place key column NAMES at their declared positions
        # first, then append remaining fields. Without this, header order =
        # list(fields.keys()) — meaning the first non-key field could
        # accidentally become column 0, breaking _row_key on next reads.
        canonical_key_names = _CANONICAL_KEY_COLS.get(target, ())
        ordered: list[str] = []
        for name in canonical_key_names:
            ordered.append(name)
        for k in fields.keys():
            if k not in ordered:
                ordered.append(k)
        header = ordered
        rows = []
        # preserve full original text as preamble; ensure trailing newline
        preamble = text if text.endswith("\n") else text + "\n"
        postamble = ""

    try:
        op_key = _normalize_op_key(op.get("key"), key_cols)
    except ValueError as e:
        return text, str(e), notices

    # build column-name → index map for fields-based mutation
    col_idx = {name: i for i, name in enumerate(header)}

    # Defense: refuse to let `fields` mutate the row's primary key. The op's
    # `key` is the only authoritative identifier — letting fields override
    # would let upsert(key=A, fields={charA:B}) silently relocate the row,
    # violating "key is immutable primary".
    fields_in = op.get("fields") or {}
    if isinstance(fields_in, dict):
        forbidden_field_names = {
            header[c] for c in key_cols if c < len(header)
        }
        sanitized_fields = {}
        for k, v in fields_in.items():
            if k in forbidden_field_names:
                notices.append(
                    f"dropped_key_field: fields[{k!r}] would mutate primary "
                    f"key column; ignored (use op.key, not fields)"
                )
                continue
            sanitized_fields[k] = v
        op = {**op, "fields": sanitized_fields}

    # locate existing row
    match_idx = next(
        (i for i, r in enumerate(rows) if _row_key(r, key_cols) == op_key),
        -1,
    )

    if kind == "delete_row":
        if match_idx < 0:
            return text, f"delete_row: key not found: {list(op_key)}", notices
        rows.pop(match_idx)
    elif kind == "update_row":
        if match_idx < 0:
            return text, f"update_row: key not found: {list(op_key)}", notices
        fields = op.get("fields") or {}
        if not isinstance(fields, dict):
            return text, "update_row: 'fields' must be object", notices
        row = rows[match_idx] + [""] * max(0, len(header) - len(rows[match_idx]))
        for k, v in fields.items():
            if k not in col_idx:
                return text, f"update_row: unknown column {k!r}", notices
            row[col_idx[k]] = "" if v is None else str(v)
        rows[match_idx] = row
    elif kind == "upsert_row":
        fields = op.get("fields") or {}
        if not isinstance(fields, dict):
            return text, "upsert_row: 'fields' must be object", notices
        # build row: start from existing if present, else empty
        if match_idx >= 0:
            row = rows[match_idx] + [""] * max(0, len(header) - len(rows[match_idx]))
        else:
            row = [""] * len(header)
            # populate key columns from op key
            for i, c in enumerate(key_cols):
                if c < len(row):
                    row[c] = op_key[i]
        for k, v in fields.items():
            if k not in col_idx:
                # auto-extend header for upsert (allow new columns over time)
                col_idx[k] = len(header)
                header.append(k)
                row.append("")
                # extend other rows
                for r in rows:
                    r.append("")
            row[col_idx[k]] = "" if v is None else str(v)
        if match_idx >= 0:
            rows[match_idx] = row
        else:
            rows.append(row)

    new_text = (preamble or "") + _render_table(header, rows) + (postamble or "")
    return new_text, None, notices


# ──────────────────────────── role ops ─────────────────────────────────────


def _apply_role_op(book: Path, op: dict) -> tuple[Path | None, str | None, str | None, str | None, list[str]]:
    """Returns (target_path, before_text_or_None, after_text, error_or_None, notices).

    `before_text` is None when the file did not exist before (create_role,
    rename source). `target_path` is the path that ended up modified
    (post-rename for rename_role; new file path for create_role).
    `notices` is a list of soft-warning strings from section sanitation.
    """
    notices: list[str] = []
    kind = op.get("op")
    if kind not in VALID_ROLE_OPS:
        return None, None, None, f"invalid role op: {kind!r}", notices

    slug = op.get("slug")
    err = _validate_slug(slug)
    if err:
        return None, None, None, err, notices

    if kind == "create_role":
        tier = op.get("tier") or DEFAULT_ROLE_TIER
        if tier not in VALID_ROLE_TIERS:
            return None, None, None, (
                f"invalid tier {tier!r}; must be one of {list(VALID_ROLE_TIERS)}"
            ), notices
        # refuse if a file with this stem already exists anywhere under roles/
        existing = [p for p in _list_existing_role_files(book) if p.stem == slug.strip()]
        if existing:
            return None, None, None, (
                f"create_role: file already exists at {existing[0].relative_to(book)}; "
                "use patch_role_section or rename_role"
            ), notices
        new_path = _role_create_path(book, slug, tier)
        display_name = (op.get("displayName") or slug).strip()
        body = op.get("initialContent")
        if not isinstance(body, str) or not body.strip():
            body = _load_role_template(display_name)
        # prefix with H1 display name if not already present
        if not body.lstrip().startswith("# "):
            body = f"# {display_name}\n\n{body}"
        return new_path, None, body, None, notices

    if kind == "patch_role_section":
        try:
            path = _resolve_role_path(book, slug)
        except (ValueError, FileNotFoundError) as e:
            return None, None, None, str(e), notices
        before = path.read_text(encoding="utf-8")
        new_text, err, sec_notices = _apply_section_op(before, op)
        notices.extend(sec_notices)
        if err:
            return path, before, None, err, notices
        return path, before, new_text, None, notices

    if kind == "rename_role":
        try:
            path = _resolve_role_path(book, slug)
        except (ValueError, FileNotFoundError) as e:
            return None, None, None, str(e), notices
        new_slug = op.get("newSlug")
        err = _validate_slug(new_slug)
        if err:
            return None, None, None, f"newSlug invalid: {err}", notices
        # new file lives in same tier dir (parent of current path)
        new_path = path.parent / f"{new_slug.strip()}.md"
        if new_path.exists():
            return None, None, None, f"target slug already exists: {new_slug}", notices
        before = path.read_text(encoding="utf-8")
        return new_path, before, before, None, notices  # caller renames via os.rename

    if kind == "delete_role":
        try:
            path = _resolve_role_path(book, slug)
        except (ValueError, FileNotFoundError) as e:
            return None, None, None, str(e), notices
        before = path.read_text(encoding="utf-8")
        # Signal "delete" by returning after_text=None with no error.
        # Caller checks (kind == delete_role) and unlinks instead of writing.
        return path, before, None, None, notices

    return None, None, None, "unreachable", notices


# ──────────────────────────── public entrypoint ────────────────────────────


def _config_filter(
    book: Path, doc_ops: dict, source_phase_default: str | None
) -> tuple[set[str], set[str]]:
    """Return (denied_paths, allowed_phases) from book.json#docOpsConfig.

    denied_paths is a set of book-relative paths to refuse.
    allowed_phases is the set of acceptable sourcePhase values; empty set
    means "no restriction".
    """
    book_json = book / "book.json"
    denied: set[str] = set()
    allowed: set[str] = set()
    if not book_json.exists():
        return denied, allowed
    try:
        cfg = json.loads(book_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return denied, allowed
    sub = (cfg or {}).get("docOpsConfig") or {}
    if isinstance(sub.get("deny"), list):
        denied = {str(p) for p in sub["deny"]}
    if isinstance(sub.get("allowSourcePhases"), list):
        allowed = {str(p) for p in sub["allowSourcePhases"]}
    return denied, allowed


def apply(
    book: Path,
    doc_ops: dict | None,
    warnings: list[str],
    modified: list[str],
    *,
    chapter: int | None = None,
) -> list[dict]:
    """Apply a docOps batch and return the ``docOpsApplied`` ledger.

    `book`        — book directory (Path).
    `doc_ops`     — delta["docOps"] payload (dict of target → list[op]); may
                    be None.
    `warnings`    — caller's warnings list, appended in-place on partial
                    failures.
    `modified`    — caller's modified-files list, appended with absolute
                    paths of every file actually written.
    `chapter`     — current chapter number (used for backup directory naming;
                    falls back to per-op sourceChapter, then 0).

    Returns a list of NDJSON-shaped entries (one per applied op).

    This function is intentionally permissive at the *batch* level: if one
    op fails, we restore that file from its .bak and continue with the rest
    (matches hookOps "partialFailure" semantics). Schema-level errors should
    have been caught by settler_parse.validate_delta upstream.
    """
    if not isinstance(doc_ops, dict) or not doc_ops:
        return []

    applied: list[dict] = []
    denied_paths, allowed_phases = _config_filter(book, doc_ops, None)

    # Defense in depth: blacklist keys must never appear at top level.
    for blk in DOC_OPS_BLACKLIST:
        if blk in doc_ops:
            warnings.append(
                f"docOps: blacklist target {blk!r} present in batch; entire batch refused."
            )
            return []

    seq = 0
    for target, ops in doc_ops.items():
        if target not in DOC_OPS_WHITELIST:
            warnings.append(f"docOps: unknown target {target!r}; skipped.")
            continue
        if not isinstance(ops, list):
            warnings.append(f"docOps.{target}: expected list, got {type(ops).__name__}; skipped.")
            continue

        for op in ops:
            if not isinstance(op, dict):
                warnings.append(f"docOps.{target}: non-object op skipped.")
                continue

            phase = op.get("sourcePhase")
            if allowed_phases and phase not in allowed_phases:
                warnings.append(
                    f"docOps.{target}: sourcePhase={phase!r} blocked by docOpsConfig.allowSourcePhases."
                )
                continue

            # Resolve target path (roles uses slug)
            if target == ROLE_TARGET:
                # role ops are special — handled below
                pass
            else:
                rel = DOC_OPS_WHITELIST[target]
                if rel in denied_paths:
                    warnings.append(f"docOps.{target}: path {rel!r} denied by docOpsConfig.")
                    continue

            applied_at = _now_iso()
            seq += 1

            if target in SECTION_TARGETS:
                rel = DOC_OPS_WHITELIST[target]
                path = book / rel
                before = path.read_text(encoding="utf-8") if path.exists() else ""
                bak = _backup_path(book, chapter or op.get("sourceChapter"), rel, seq)
                if path.exists():
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    bak.write_text(before, encoding="utf-8")
                new_text, err, sec_notices = _apply_section_op(before, op)
                if err:
                    warnings.append(f"docOps.{target}[seq={seq}] failed: {err}")
                    continue
                for n in sec_notices:
                    warnings.append(f"docOps.{target}[seq={seq}] notice: {n}")
                _atomic_write_text(path, new_text)
                modified.append(str(path))
                op_id = _op_id(rel, op.get("anchor"), applied_at)
                entry = {
                    "appliedAt": applied_at,
                    "chapter": op.get("sourceChapter"),
                    "file": rel,
                    "op": op.get("op"),
                    "anchor": op.get("anchor"),
                    "reason": op.get("reason", ""),
                    "sourcePhase": op.get("sourcePhase"),
                    "backupPath": str(bak.relative_to(book)) if path.exists() or bak.exists() else None,
                    "opId": op_id,
                    "notices": sec_notices,
                }
                _append_doc_changes_log(book, entry)
                applied.append(entry)
                continue

            if target in TABLE_TARGETS:
                rel = DOC_OPS_WHITELIST[target]
                path = book / rel
                before = path.read_text(encoding="utf-8") if path.exists() else ""
                bak = _backup_path(book, chapter or op.get("sourceChapter"), rel, seq)
                if path.exists():
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    bak.write_text(before, encoding="utf-8")
                key_cols = TABLE_KEY_COLUMNS[target]
                new_text, err, tbl_notices = _apply_table_op(before, op, key_cols, target)
                if err:
                    warnings.append(f"docOps.{target}[seq={seq}] failed: {err}")
                    continue
                for n in tbl_notices:
                    warnings.append(f"docOps.{target}[seq={seq}] notice: {n}")
                _atomic_write_text(path, new_text)
                modified.append(str(path))
                key_repr = "|".join(str(k) for k in (op.get("key") or []))
                op_id = _op_id(rel, key_repr, applied_at)
                entry = {
                    "appliedAt": applied_at,
                    "chapter": op.get("sourceChapter"),
                    "file": rel,
                    "op": op.get("op"),
                    "anchor": key_repr,  # key serves as "anchor" in log
                    "reason": op.get("reason", ""),
                    "sourcePhase": op.get("sourcePhase"),
                    "backupPath": str(bak.relative_to(book)) if path.exists() or bak.exists() else None,
                    "opId": op_id,
                    "notices": tbl_notices,
                }
                _append_doc_changes_log(book, entry)
                applied.append(entry)
                continue

            if target == ROLE_TARGET:
                tgt_path, before_text, after_text, err, role_notices = _apply_role_op(book, op)
                if err:
                    warnings.append(f"docOps.roles[seq={seq}] failed: {err}")
                    continue
                for n in role_notices:
                    warnings.append(f"docOps.roles[seq={seq}] notice: {n}")
                rel = (
                    str(tgt_path.relative_to(book))
                    if tgt_path
                    else f"story/roles/{op.get('slug')}.md"
                )
                if rel in denied_paths:
                    warnings.append(f"docOps.roles: path {rel!r} denied by docOpsConfig.")
                    continue
                bak = _backup_path(book, chapter or op.get("sourceChapter"), rel, seq)
                if before_text is not None:
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    bak.write_text(before_text, encoding="utf-8")
                kind = op.get("op")
                if kind == "rename_role":
                    # tgt_path is the NEW path; resolve OLD path via current slug
                    try:
                        old_path = _resolve_role_path(book, op["slug"])
                        if old_path.exists():
                            old_path.unlink()
                    except (ValueError, FileNotFoundError):
                        pass
                    _atomic_write_text(tgt_path, after_text or "")
                elif kind == "delete_role":
                    # tgt_path exists (resolved); .bak already written above.
                    if tgt_path and tgt_path.exists():
                        tgt_path.unlink()
                else:
                    _atomic_write_text(tgt_path, after_text or "")
                modified.append(str(tgt_path))
                op_id = _op_id(rel, op.get("anchor") or kind, applied_at)
                entry = {
                    "appliedAt": applied_at,
                    "chapter": op.get("sourceChapter"),
                    "file": rel,
                    "op": kind,
                    "anchor": op.get("anchor"),
                    "reason": op.get("reason", ""),
                    "sourcePhase": op.get("sourcePhase"),
                    "backupPath": str(bak.relative_to(book)) if before_text is not None else None,
                    "opId": op_id,
                    "notices": role_notices,
                }
                _append_doc_changes_log(book, entry)
                applied.append(entry)
                continue

    return applied


# ──────────────────────────── direct-edit log helper ──────────────────────


def log_direct_edit(book: Path, file_rel: str, reason: str,
                    chapter: int | None = None) -> dict:
    """Append a `user-directive-direct-edit` entry to doc_changes.log.

    For author-constitution files (author_intent / fanfic_canon / parent_canon /
    book.json#bookRules) which are blacklisted from the docOps pipeline but
    permitted to be edited directly when the author explicitly asks. The LLM
    runs its `Edit` against the file, then calls this to leave an audit trail
    that doctor.py can verify.

    No .bak — for blacklist constitution files we rely on git or manual
    backups (changes are infrequent). Returns the log entry that was written
    (so the caller can echo opId back to the user).
    """
    if not isinstance(file_rel, str) or not file_rel.strip():
        return {"ok": False, "error": "file path required"}
    if not isinstance(reason, str) or not reason.strip():
        return {"ok": False, "error": "reason required"}
    if len(reason) > REASON_CAP:
        return {"ok": False, "error": f"reason too long: {len(reason)} > {REASON_CAP}"}
    target = book / file_rel
    if not target.exists():
        return {
            "ok": False,
            "error": f"file does not exist: {file_rel} (log-direct-edit is "
            "for files you've JUST edited; if you meant to create a new "
            "constitution file, do that first then re-run)",
        }
    applied_at = _now_iso()
    op_id = _op_id(file_rel, "direct_edit", applied_at)
    entry = {
        "appliedAt": applied_at,
        "chapter": chapter,
        "file": file_rel,
        "op": "direct_edit",
        "anchor": None,
        "reason": reason,
        "sourcePhase": "user-directive-direct-edit",
        "backupPath": None,
        "opId": op_id,
    }
    _append_doc_changes_log(book, entry)
    return {"ok": True, "logged": entry}


# ──────────────────────────── revert by op-id ──────────────────────────────


def revert(book: Path, op_id: str) -> dict:
    """Restore the file modified by a previous op (looked up in doc_changes.log)."""
    log = book / "story" / "runtime" / "doc_changes.log"
    if not log.exists():
        return {"ok": False, "error": "doc_changes.log not found"}

    entry: dict | None = None
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("opId") == op_id:
            entry = rec
            # don't break — last match wins (most recent revert target)

    if entry is None:
        return {"ok": False, "error": f"opId not found: {op_id}"}

    target = book / entry["file"]
    bak_rel = entry.get("backupPath")
    op_kind = entry.get("op")

    # Special case: create_role has no .bak (the file didn't exist before).
    # revert(create_role) = unlink the created file.
    if op_kind == "create_role":
        if not target.exists():
            return {
                "ok": False,
                "error": f"create_role target already absent: {entry['file']} "
                "(perhaps reverted earlier?)",
                "entry": entry,
            }
        target.unlink()
        revert_entry = {
            "appliedAt": _now_iso(),
            "chapter": entry.get("chapter"),
            "file": entry["file"],
            "op": "revert",
            "anchor": entry.get("anchor"),
            "reason": f"revert opId={op_id} (create_role → unlink)",
            "sourcePhase": "revert",
            "backupPath": None,
            "opId": _op_id(entry["file"], "revert:" + op_id, _now_iso()),
            "revertedOpId": op_id,
            "revertedKind": op_kind,
        }
        _append_doc_changes_log(book, revert_entry)
        return {"ok": True, "deleted": str(target), "entry": entry}

    # General path: restore from .bak.
    if not bak_rel:
        return {
            "ok": False,
            "error": "this op has no backup (likely a user-directive direct edit "
            "or a non-revertible op kind); use git checkout or restore manually",
            "entry": entry,
        }
    bak_path = book / bak_rel
    if not bak_path.exists():
        return {"ok": False, "error": f"backup file missing: {bak_rel}", "entry": entry}

    bak_text = bak_path.read_text(encoding="utf-8")
    if op_kind == "delete_role":
        # File was deleted by the original op; restore it from .bak.
        target.parent.mkdir(parents=True, exist_ok=True)
    elif op_kind == "rename_role":
        # The original op left the OLD slug file deleted and the NEW slug file
        # created. Reverting means: delete NEW, restore OLD from .bak.
        # entry["file"] = new path; .bak holds old content; we need the old slug.
        # The .bak filename encodes the new slug (since we backed up before the
        # write). For a clean revert we must drop the new file and restore at
        # the old slug — but that path isn't recorded in the log entry.
        # Pragma: rename_role revert is brittle; warn caller.
        if target.exists():
            target.unlink()
        return {
            "ok": True,
            "partial": True,
            "deleted": str(target),
            "warning": (
                "rename_role revert dropped the new file but cannot reliably "
                "recreate the old-slug path from log alone. Restore the "
                ".bak content under the original filename manually if needed."
            ),
            "bakContent": bak_rel,
            "entry": entry,
        }
    _atomic_write_text(target, bak_text)

    revert_entry = {
        "appliedAt": _now_iso(),
        "chapter": entry.get("chapter"),
        "file": entry["file"],
        "op": "revert",
        "anchor": entry.get("anchor"),
        "reason": f"revert opId={op_id}",
        "sourcePhase": "revert",
        "backupPath": None,
        "opId": _op_id(entry["file"], "revert:" + op_id, _now_iso()),
        "revertedOpId": op_id,
        "revertedKind": op_kind,
    }
    _append_doc_changes_log(book, revert_entry)
    return {"ok": True, "restored": str(target), "from": str(bak_path), "entry": entry}
