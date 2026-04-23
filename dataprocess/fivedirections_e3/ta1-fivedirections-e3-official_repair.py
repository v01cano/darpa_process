#!/usr/bin/env python3
"""
Repair a line-delimited JSON-like file whose records incorrectly end with a
trailing comma.

Example:
    python ta1-fivedirections-e3-official_repair.py input.json
    python ta1-fivedirections-e3-official_repair.py input.json output.json
"""

from __future__ import annotations

import sys
from pathlib import Path


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_fixed{input_path.suffix}")


def repair_file(input_path: Path, output_path: Path) -> None:
    total_lines = 0
    changed_lines = 0

    with input_path.open("r", encoding="utf-8") as src, output_path.open(
        "w", encoding="utf-8"
    ) as dst:
        for line in src:
            total_lines += 1
            stripped_newline = line.rstrip("\n")
            trimmed = stripped_newline.rstrip()

            if trimmed.endswith(","):
                comma_index = stripped_newline.rfind(",")
                stripped_newline = (
                    stripped_newline[:comma_index] + stripped_newline[comma_index + 1 :]
                )
                changed_lines += 1

            dst.write(stripped_newline + "\n")

    print(f"Input : {input_path}")
    print(f"Output: {output_path}")
    print(f"Total lines  : {total_lines}")
    print(f"Changed lines: {changed_lines}")


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(
            "Usage: python ta1-fivedirections-e3-official_repair.py <input_file> [output_file]",
            file=sys.stderr,
        )
        return 1

    input_path = Path(argv[1])
    output_path = Path(argv[2]) if len(argv) == 3 else default_output_path(input_path)

    if not input_path.is_file():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    if input_path.resolve() == output_path.resolve():
        print("Output file must be different from input file.", file=sys.stderr)
        return 1

    repair_file(input_path, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
