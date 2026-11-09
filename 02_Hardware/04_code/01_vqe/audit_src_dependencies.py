#!/usr/bin/env python3
"""Read-only audit for legacy Study B src dependencies. Does not change files."""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

PATTERNS = [
    ("legacy_HEA_path", re.compile(r"src[/\\]HEA_VQE|['\"]src['\"]\s*/\s*['\"]HEA_VQE['\"]|['\"]src['\"]\s*,\s*['\"]HEA_VQE['\"]", re.I)),
    ("legacy_common_path", re.compile(r"src[/\\]common|['\"]src['\"]\s*/\s*['\"]common['\"]", re.I)),
    ("source_import", re.compile(r"\b(?:from|import)\s+src(?:\.|\b)")),
    ("other_src_reference", re.compile(r"(?<![a-zA-Z0-9_])src[/\\]|['\"]src['\"]\s*/", re.I)),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    root = args.root.resolve()
    hardware = root / "02_Hardware" if (root / "02_Hardware").is_dir() else root
    code_dir = hardware / "04_code"
    if not code_dir.is_dir():
        raise FileNotFoundError(f"Code directory not found: {code_dir}")

    rows = []
    for source in sorted(code_dir.rglob("*.py")):
        # Do not scan the audit script itself: its regex patterns contain src paths.
        if source.resolve() == Path(__file__).resolve():
            continue
        relative = source.relative_to(hardware).as_posix()
        for line_number, line in enumerate(source.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for category, pattern in PATTERNS:
                if pattern.search(line):
                    rows.append({
                        "file": relative,
                        "line": line_number,
                        "category": category,
                        "code": line.strip()[:350],
                    })
                    break

    output = args.out or hardware / "src_dependency_audit.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file", "line", "category", "code"])
        writer.writeheader()
        writer.writerows(rows)

    active = [r for r in rows if not r["file"].startswith("04_code/00_original_scripts_reference/")]
    print(f"Scanned: {code_dir}")
    print(f"References found: {len(rows)} ({len(active)} outside original-scripts reference)")
    print(f"Report: {output}")
    for row in active:
        print(f"{row['file']}:{row['line']} [{row['category']}] {row['code'][:115]}")
    if active:
        print("WARNING: Active static src references remain; do not delete src yet.")
    else:
        print("No matching active static src references found.")
        print("This does NOT prove dynamic imports or runtime dependencies are safe.")


if __name__ == "__main__":
    main()
