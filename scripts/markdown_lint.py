import os
import re
import sys
import urllib.parse


LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
HEADER_RE = re.compile(r"^(#{1,6})(\s+)(.*)$")
FENCE_RE = re.compile(r"^(?P<indent>\s*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
LIST_ITEM_RE = re.compile(r"^(?P<indent>[ ]*)(?P<marker>(?:[-+*]|\d+\.))\s+.+$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?[-]{3,}:?(\s*\|\s*:?[-]{3,}:?)+\s*\|?\s*$")


def iter_markdown_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git"}]
        for name in filenames:
            if name.lower().endswith(".md"):
                yield os.path.join(dirpath, name)


def normalize_md_target(target: str) -> str:
    t = target.strip()
    if (t.startswith("\"") and t.endswith("\"")) or (t.startswith("'") and t.endswith("'")):
        t = t[1:-1].strip()
    return t


def is_web_url(target: str) -> bool:
    t = target.lower()
    return t.startswith("http://") or t.startswith("https://")


def resolve_local_path(md_file: str, target: str) -> str | None:
    t = normalize_md_target(target)
    if not t or is_web_url(t) or t.startswith("mailto:"):
        return None

    t = urllib.parse.unquote(t)
    t = t.split("#", 1)[0].split("?", 1)[0]
    if not t:
        return None

    if os.path.isabs(t):
        return os.path.normpath(t)

    base = os.path.dirname(md_file)
    return os.path.normpath(os.path.join(base, t))


def count_table_cols(line: str) -> int:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return len([c for c in s.split("|")])


def lint_file(path: str) -> list[tuple[int, str, str]]:
    errors: list[tuple[int, str, str]] = []

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except UnicodeDecodeError:
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()

    in_fenced = False
    fence_token = ""
    fence_line = 0

    h1_count = 0
    last_header_level = 0

    i = 0
    while i < len(lines):
        line_no = i + 1
        line = lines[i]

        if "\t" in line:
            errors.append((line_no, "indent", "Tab character found; use spaces"))

        fence_m = FENCE_RE.match(line)
        if fence_m:
            token = fence_m.group("fence")
            if not in_fenced:
                in_fenced = True
                fence_token = token[0] * 3
                fence_line = line_no
            else:
                if token[0] == fence_token[0] and len(token) >= 3:
                    in_fenced = False
                    fence_token = ""
                    fence_line = 0
            i += 1
            continue

        if not in_fenced:
            header_m = HEADER_RE.match(line)
            if header_m:
                level = len(header_m.group(1))
                title = header_m.group(3)

                if title.strip() == "":
                    errors.append((line_no, "header", "Header has no text"))

                if line.startswith("#") and not line.startswith("# ") and line.startswith("#"):
                    # detect missing space after hashes (e.g. #Title)
                    if not re.match(r"^#{1,6}\s", line):
                        errors.append((line_no, "header", "Missing space after '#' in header"))

                if level == 1:
                    h1_count += 1
                    if h1_count > 1:
                        errors.append((line_no, "header", "Multiple H1 headings found"))

                if last_header_level and level > last_header_level + 1:
                    errors.append((line_no, "header", f"Header level jumps from H{last_header_level} to H{level}"))
                last_header_level = level

            list_m = LIST_ITEM_RE.match(line)
            if list_m:
                indent = len(list_m.group("indent"))
                if indent % 2 != 0:
                    errors.append((line_no, "indent", "List indentation is not a multiple of 2 spaces"))
                if indent > 0:
                    prev = lines[i - 1] if i > 0 else ""
                    if prev.strip() == "" and not prev.startswith(" "):
                        # blank line before an indented list often breaks rendering
                        errors.append((line_no, "indent", "Indented list item preceded by blank line; may break nesting"))

            for m in LINK_RE.finditer(line):
                target = m.group(1)
                local = resolve_local_path(path, target)
                if local is not None and not os.path.exists(local):
                    errors.append((line_no, "link", f"Broken link target: {target}"))

            for m in IMAGE_RE.finditer(line):
                target = m.group(1)
                local = resolve_local_path(path, target)
                if local is not None and not os.path.exists(local):
                    errors.append((line_no, "image", f"Missing image file: {target}"))

            # Table check: header row + separator row pattern
            if "|" in line and i + 1 < len(lines):
                next_line = lines[i + 1]
                if TABLE_SEP_RE.match(next_line):
                    cols = count_table_cols(line)
                    if cols < 2:
                        errors.append((line_no, "table", "Table header appears to have fewer than 2 columns"))
                    j = i + 2
                    while j < len(lines) and "|" in lines[j] and lines[j].strip() != "":
                        row_cols = count_table_cols(lines[j])
                        if row_cols != cols:
                            errors.append((j + 1, "table", f"Table row has {row_cols} columns; expected {cols}"))
                        j += 1

        i += 1

    if in_fenced:
        errors.append((fence_line or 1, "syntax", "Fenced code block is not closed"))

    return errors


def main(argv: list[str]) -> int:
    root = os.getcwd()
    md_files = list(iter_markdown_files(root))

    if not md_files:
        print("No .md files found")
        return 0

    all_errors: list[tuple[str, int, str, str]] = []
    for f in md_files:
        for (line_no, rule, msg) in lint_file(f):
            all_errors.append((f, line_no, rule, msg))

    if all_errors:
        for f, line_no, rule, msg in sorted(all_errors):
            rel = os.path.relpath(f, root)
            print(f"{rel}:{line_no}: [{rule}] {msg}")
        print(f"\nMarkdown lint failed: {len(all_errors)} issue(s) found")
        return 1

    print(f"Markdown lint passed: {len(md_files)} file(s) checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
