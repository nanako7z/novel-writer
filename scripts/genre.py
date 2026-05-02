#!/usr/bin/env python3
"""Genre catalog CLI: list / show / add / validate.

Genre profiles live as YAML-frontmatter markdown files in two locations:

  1. Bundled (project-wide):  <skill_root>/templates/genres/*.md
  2. User override:           <user-workdir>/genres/*.md   (optional)

User overrides shadow bundled profiles with the same id. `book.json#genre`
is just a string id; Writer / Auditor look it up via the same search order.

Subcommands
-----------
  list                              all genres available (bundled + user)
  show     <id>                     full profile (frontmatter + body)
  add      <id>  --from <template>  copy template to user dir, patch id/name
  validate [<id>]                   schema-check one or all profiles

Stdlib only.  We hand-roll a tiny YAML scalar parser for the frontmatter —
profiles only use scalars, lists of strings, and lists of ints.  We never
emit nested mappings, so PyYAML is not needed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_GENRES_DIR = SKILL_ROOT / "templates" / "genres"

REQUIRED_FIELDS = ["id", "name", "chapterTypes", "fatigueWords", "satisfactionTypes"]
# language is required-ish but inkos defaults to "zh" if absent — we treat it
# as required for new files but allow legacy bundled files to omit it.
RECOMMENDED_FIELDS = ["language"]

KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


# --------------------------- workdir ---------------------------------------

def find_project_root(start: Path) -> Path | None:
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / "inkos.json").is_file():
            return p
    return None


def resolve_workdir(arg: str | None) -> Path:
    if arg:
        return Path(arg).resolve()
    root = find_project_root(Path.cwd())
    if root is not None:
        return root
    return Path.cwd().resolve()


def user_genres_dir(workdir: Path) -> Path:
    return workdir / "genres"


# --------------------------- YAML mini-parser ------------------------------

def _strip_inline_comment(s: str) -> str:
    # Strip ` # ...` trailing comment (only when preceded by whitespace and
    # not inside a quoted string).
    out: list[str] = []
    in_squote = in_dquote = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == "'" and not in_dquote:
            in_squote = not in_squote
            out.append(c)
        elif c == '"' and not in_squote:
            in_dquote = not in_dquote
            out.append(c)
        elif c == "#" and not in_squote and not in_dquote and (i == 0 or s[i - 1].isspace()):
            break
        else:
            out.append(c)
        i += 1
    return "".join(out).rstrip()


def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if not s:
        return ""
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if s.lower() in ("null", "~"):
        return None
    # quoted string
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    # int
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except ValueError:
            pass
    # float
    if re.fullmatch(r"-?\d+\.\d+", s):
        try:
            return float(s)
        except ValueError:
            pass
    return s


def _parse_inline_list(raw: str) -> list[Any]:
    """Parse `[a, b, "c", 1, 2]`-style inline lists."""
    s = raw.strip()
    if not (s.startswith("[") and s.endswith("]")):
        raise ValueError(f"not an inline list: {raw!r}")
    inner = s[1:-1].strip()
    if not inner:
        return []
    out: list[Any] = []
    buf: list[str] = []
    depth = 0
    in_squote = in_dquote = False
    for c in inner:
        if c == "'" and not in_dquote:
            in_squote = not in_squote
            buf.append(c)
        elif c == '"' and not in_squote:
            in_dquote = not in_dquote
            buf.append(c)
        elif c == "[" and not in_squote and not in_dquote:
            depth += 1
            buf.append(c)
        elif c == "]" and not in_squote and not in_dquote:
            depth -= 1
            buf.append(c)
        elif c == "," and depth == 0 and not in_squote and not in_dquote:
            out.append(_parse_scalar("".join(buf)))
            buf = []
        else:
            buf.append(c)
    if buf:
        tail = "".join(buf).strip()
        if tail:
            out.append(_parse_scalar(tail))
    return out


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (front_dict, body_text). If no frontmatter, returns ({}, text)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    front_text, body = m.group(1), m.group(2)
    fm: dict[str, Any] = {}

    # Each line: `key: value` (we don't support nested maps / multi-line lists here)
    for raw_line in front_text.splitlines():
        line = _strip_inline_comment(raw_line.rstrip())
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        # Skip indented continuation lines (we don't support nested mappings)
        if line[0] in (" ", "\t"):
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if val.startswith("["):
            try:
                fm[key] = _parse_inline_list(val)
            except ValueError:
                fm[key] = val
        else:
            fm[key] = _parse_scalar(val)
    return fm, body


def _yaml_emit_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        # Quote when needed: contains special chars, leading/trailing whitespace,
        # looks like a number/bool, or contains `:`/`#`.
        needs_quote = (
            v == ""
            or v != v.strip()
            or v.lower() in ("true", "false", "null", "~")
            or re.fullmatch(r"-?\d+(\.\d+)?", v) is not None
            or any(ch in v for ch in (":", "#", "[", "]", "{", "}", ",", "&", "*", "!", "|", ">", "'", '"', "%", "@", "`"))
        )
        if needs_quote:
            esc = v.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{esc}"'
        return v
    raise TypeError(f"cannot YAML-emit {type(v).__name__}")


def _yaml_emit_list(items: list[Any]) -> str:
    parts = [_yaml_emit_scalar(x) for x in items]
    return "[" + ", ".join(parts) + "]"


def _patch_frontmatter_field(text: str, key: str, new_value: str) -> str:
    """Replace a top-level scalar field inside the YAML frontmatter without
    disturbing surrounding lines (including nested mappings). If the key is
    missing in frontmatter, insert it just before the closing `---`."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return text
    front_text = m.group(1)
    body = m.group(2)
    # Match top-level (unindented) `<key>:` line.
    line_re = re.compile(rf"(?m)^{re.escape(key)}:\s*.*$")
    quoted = _yaml_emit_scalar(new_value)
    new_line = f"{key}: {quoted}"
    if line_re.search(front_text):
        new_front = line_re.sub(new_line, front_text, count=1)
    else:
        # Append at end of frontmatter
        new_front = front_text.rstrip("\n") + "\n" + new_line
    return f"---\n{new_front}\n---\n{body}"


def emit_frontmatter(fm: dict[str, Any]) -> str:
    """Re-emit a frontmatter dict in canonical order: id/name/language first,
    then the rest in insertion order."""
    priority = ["id", "name", "language"]
    keys: list[str] = []
    for k in priority:
        if k in fm:
            keys.append(k)
    for k in fm:
        if k not in keys:
            keys.append(k)
    lines = ["---"]
    for k in keys:
        v = fm[k]
        if isinstance(v, list):
            lines.append(f"{k}: {_yaml_emit_list(v)}")
        else:
            lines.append(f"{k}: {_yaml_emit_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# --------------------------- discovery -------------------------------------

def discover_genres(workdir: Path) -> list[dict[str, Any]]:
    """Return all genre profiles. User overrides shadow bundled by id."""
    out: dict[str, dict[str, Any]] = {}

    def _scan(directory: Path, source: str) -> None:
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fm, _body = parse_frontmatter(text)
            gid = fm.get("id") or path.stem
            entry = {
                "id": gid,
                "name": fm.get("name") or gid,
                "language": fm.get("language") or "zh",
                "chapterTypes": fm.get("chapterTypes") or [],
                "satisfactionTypes": fm.get("satisfactionTypes") or [],
                "source": source,
                "path": str(path),
            }
            # User shadows bundled
            if source == "user" or gid not in out:
                out[gid] = entry

    _scan(BUNDLED_GENRES_DIR, "bundled")
    _scan(user_genres_dir(workdir), "user")
    return sorted(out.values(), key=lambda e: e["id"])


def resolve_genre(workdir: Path, genre_id: str) -> Path | None:
    """Lookup a genre file: user override first, then bundled."""
    candidates = [
        user_genres_dir(workdir) / f"{genre_id}.md",
        BUNDLED_GENRES_DIR / f"{genre_id}.md",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


# --------------------------- list ------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    rows = discover_genres(workdir)
    if args.json:
        print(json.dumps({"workdir": str(workdir), "genres": rows},
                         ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("(no genres found — check templates/genres/)")
        return 0
    # text table
    headers = ["id", "name", "lang", "source", "chapterTypes"]
    data = [[
        r["id"],
        str(r.get("name") or ""),
        str(r.get("language") or ""),
        r.get("source") or "",
        ", ".join(str(x) for x in (r.get("chapterTypes") or [])[:3]) +
        ("…" if len(r.get("chapterTypes") or []) > 3 else ""),
    ] for r in rows]
    widths = [len(h) for h in headers]
    for row in data:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    sep = "  "
    out_lines = [sep.join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    out_lines.append(sep.join("-" * widths[i] for i in range(len(headers))))
    for row in data:
        out_lines.append(sep.join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    print(f"Workdir: {workdir}")
    print(f"Genres: {len(rows)}  ({sum(1 for r in rows if r['source'] == 'user')} user override(s))")
    print()
    print("\n".join(out_lines))
    return 0


# --------------------------- show ------------------------------------------

def cmd_show(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    path = resolve_genre(workdir, args.id)
    if path is None:
        print(json.dumps({"error": f"genre not found: {args.id}",
                          "searched": [str(user_genres_dir(workdir)),
                                       str(BUNDLED_GENRES_DIR)]},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    source = "user" if path.is_relative_to(workdir) else "bundled"
    if args.json:
        print(json.dumps({
            "id": fm.get("id") or path.stem,
            "path": str(path),
            "source": source,
            "frontmatter": fm,
            "body": body,
        }, ensure_ascii=False, indent=2))
        return 0
    print(f"Path:   {path}")
    print(f"Source: {source}")
    print()
    print(text)
    return 0


# --------------------------- add -------------------------------------------

def cmd_add(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    if not KEBAB_RE.match(args.id):
        print(json.dumps({"error": "id must be kebab-case"}), file=sys.stderr)
        return 1

    src = resolve_genre(workdir, args.from_id)
    if src is None:
        print(json.dumps({
            "error": f"--from genre not found: {args.from_id}",
            "hint": "run `genre.py list` to see available ids",
        }, ensure_ascii=False), file=sys.stderr)
        return 1

    if args.out:
        dst = Path(args.out).resolve()
    else:
        dst_dir = user_genres_dir(workdir)
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{args.id}.md"

    if dst.exists():
        print(json.dumps({"error": f"target already exists: {dst}"},
                         ensure_ascii=False), file=sys.stderr)
        return 1

    text = src.read_text(encoding="utf-8")
    # We do *not* round-trip frontmatter through our mini-parser for add — some
    # bundled profiles use nested mappings (cadence:) that the parser drops.
    # Instead patch id/name in place with targeted regex replacements; preserve
    # the rest verbatim.
    fm, _body = parse_frontmatter(text)
    if not fm:
        print(json.dumps({
            "error": f"source profile has no YAML frontmatter: {src}",
        }, ensure_ascii=False), file=sys.stderr)
        return 1

    new_name = args.name
    if not new_name:
        # If source name equals source id (or missing), use new id as placeholder
        if not fm.get("name") or fm.get("name") == args.from_id:
            new_name = args.id
        else:
            new_name = fm["name"]

    new_text = _patch_frontmatter_field(text, "id", args.id)
    new_text = _patch_frontmatter_field(new_text, "name", new_name)

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(new_text, encoding="utf-8")

    payload = {
        "ok": True,
        "id": args.id,
        "from": args.from_id,
        "path": str(dst),
        "hint": f"Now edit {dst} to customize fatigueWords / chapterTypes / "
                "satisfactionTypes / pacingRule / body markdown.",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Created genre profile: {dst}")
        print(f"  copied from: {src}")
        print(f"  id: {args.id}")
        print(f"  name: {fm.get('name')}")
        print(f"\n{payload['hint']}")
    return 0


# --------------------------- validate --------------------------------------

def _validate_one(path: Path) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return {"path": str(path), "ok": False, "issues": [f"read error: {e}"]}

    fm, _body = parse_frontmatter(text)
    if not fm:
        return {"path": str(path), "ok": False,
                "issues": ["no YAML frontmatter found"]}

    for f in REQUIRED_FIELDS:
        if f not in fm:
            issues.append(f"missing required field: {f}")
    for f in RECOMMENDED_FIELDS:
        if f not in fm:
            warnings.append(f"missing recommended field: {f}")

    file_id = path.stem
    fm_id = fm.get("id")
    if fm_id and fm_id != file_id:
        issues.append(f"id mismatch: frontmatter id={fm_id!r} != filename stem={file_id!r}")

    if "id" in fm and not isinstance(fm_id, str):
        issues.append(f"id must be a string, got {type(fm_id).__name__}")
    elif fm_id and not KEBAB_RE.match(str(fm_id)):
        warnings.append(f"id should be kebab-case: {fm_id!r}")

    for list_field in ("chapterTypes", "fatigueWords", "satisfactionTypes"):
        v = fm.get(list_field)
        if v is not None and not isinstance(v, list):
            issues.append(f"{list_field} must be a list, got {type(v).__name__}")
        elif v is not None and not all(isinstance(x, str) for x in v):
            issues.append(f"{list_field} entries must all be strings")

    if "auditDimensions" in fm:
        ad = fm["auditDimensions"]
        if not isinstance(ad, list) or not all(isinstance(x, int) for x in ad):
            issues.append("auditDimensions must be a list of ints")

    for bool_field in ("numericalSystem", "powerScaling", "eraResearch"):
        v = fm.get(bool_field)
        if v is not None and not isinstance(v, bool):
            issues.append(f"{bool_field} must be a boolean")

    return {
        "path": str(path),
        "id": fm.get("id") or file_id,
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
    }


def cmd_validate(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    if args.id:
        path = resolve_genre(workdir, args.id)
        if path is None:
            print(json.dumps({"error": f"genre not found: {args.id}"},
                             ensure_ascii=False), file=sys.stderr)
            return 1
        results = [_validate_one(path)]
    else:
        paths: list[Path] = []
        if BUNDLED_GENRES_DIR.is_dir():
            paths.extend(sorted(BUNDLED_GENRES_DIR.glob("*.md")))
        udir = user_genres_dir(workdir)
        if udir.is_dir():
            paths.extend(sorted(udir.glob("*.md")))
        results = [_validate_one(p) for p in paths]

    failed = [r for r in results if not r["ok"]]
    payload = {
        "checked": len(results),
        "failed": len(failed),
        "warnings": sum(len(r.get("warnings") or []) for r in results),
        "results": results,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in results:
            tag = "[ok]  " if r["ok"] else "[FAIL]"
            print(f"{tag} {r.get('id', '?'):<24} {r['path']}")
            for issue in r.get("issues") or []:
                print(f"       ! {issue}")
            for w in r.get("warnings") or []:
                print(f"       ~ {w}")
        print()
        print(f"Summary: {payload['checked']} checked, "
              f"{payload['failed']} fail, {payload['warnings']} warning(s)")
    return 0 if not failed else 1


# --------------------------- CLI -------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="genre.py",
        description="Genre catalog: list / show / add / validate.",
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    sp = sub.add_parser("list", help="List bundled + user genre profiles")
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="Show one genre profile (frontmatter + body)")
    sp.add_argument("id")
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("add", help="Copy an existing genre profile to user dir as a starting point")
    sp.add_argument("id", help="new genre id (kebab-case, must equal filename stem)")
    sp.add_argument("--from", dest="from_id", required=True,
                    help="existing genre id to copy from (e.g. xianxia)")
    sp.add_argument("--name", default=None,
                    help="display name (default: same as id)")
    sp.add_argument("--out", default=None,
                    help="explicit output path (default: <workdir>/genres/<id>.md)")
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("validate", help="Schema-check one or all genre profiles")
    sp.add_argument("id", nargs="?", default=None,
                    help="genre id; omit to validate all bundled+user profiles")
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_validate)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
