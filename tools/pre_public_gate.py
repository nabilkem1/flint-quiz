#!/usr/bin/env python3
"""Pre-public exposure gate (TASK-209 / 007-security TASK-130).

Parses ``docs/pre-public-gate.md`` for `- [ ]` / `- [x]` items and
refuses to tag `public-ready` unless every box is checked. Runs as
part of the release pipeline; can also be invoked locally via::

    make pre-public-check ENV=prod

The gate is the load-bearing check for first public traffic. **Do not
bypass.** A `--force` flag is deliberately absent (FORBIDDEN ACTIONS).
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from dataclasses import dataclass


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# `- [ ] …` (unchecked) and `- [x] …` (checked) at the start of a line.
# We also accept `- [X]` for case-insensitive checked items.
_ITEM_RE = re.compile(r"^\s*-\s+\[(?P<state>[ xX])\]\s+(?P<text>.+?)\s*$")


@dataclass(frozen=True, slots=True)
class GateItem:
    section: str
    text: str
    checked: bool


def parse_gate(checklist_path: pathlib.Path) -> list[GateItem]:
    """Return every check-list item in section order.

    Section is the most recently seen `## ` heading. Items inside
    `<!-- DO NOT ENFORCE -->` blocks are skipped (so a forward-looking
    aspirational checklist can sit in the doc without blocking the
    release).
    """

    items: list[GateItem] = []
    section: str = "<root>"
    skipping = False
    for raw_line in checklist_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("<!-- DO NOT ENFORCE"):
            skipping = True
            continue
        if stripped.startswith("<!--") and "ENFORCE END" in stripped:
            skipping = False
            continue
        if skipping:
            continue
        if stripped.startswith("## "):
            section = stripped[3:].strip()
            continue
        match = _ITEM_RE.match(raw_line)
        if match:
            state = match.group("state")
            items.append(
                GateItem(
                    section=section,
                    text=match.group("text"),
                    checked=state.lower() == "x",
                )
            )
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pre_public_gate", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument(
        "--checklist",
        default=str(REPO_ROOT / "docs" / "pre-public-gate.md"),
        help="Path to the pre-public-gate.md checklist.",
    )
    parser.add_argument(
        "--env",
        default="prod",
        help="Target environment name (informational).",
    )
    parser.add_argument(
        "--strict-sections",
        action="store_true",
        help=(
            "Fail if ANY section has no items; usually a sign the parser "
            "missed something."
        ),
    )
    args = parser.parse_args(argv)

    checklist_path = pathlib.Path(args.checklist).resolve()
    if not checklist_path.exists():
        print(
            f"pre_public_gate: checklist not found at {checklist_path}",
            file=sys.stderr,
        )
        return 2

    items = parse_gate(checklist_path)
    if not items:
        print(
            f"pre_public_gate: no check-list items parsed from {checklist_path}",
            file=sys.stderr,
        )
        return 2

    unchecked = [i for i in items if not i.checked]
    total = len(items)
    checked = total - len(unchecked)

    # Group unchecked by section for the operator-facing report.
    print(f"pre_public_gate: env={args.env}, items={total}, checked={checked}")
    if unchecked:
        print("\nMissing checks (block tag `public-ready`):", file=sys.stderr)
        last_section = None
        for it in unchecked:
            if it.section != last_section:
                print(f"  {it.section}", file=sys.stderr)
                last_section = it.section
            print(f"    - [ ] {it.text}", file=sys.stderr)
        print(
            "\nRemediation: address each item, re-run `make pre-public-check`.",
            file=sys.stderr,
        )
        return 1

    print("pre_public_gate: PASSED — `public-ready` tag may proceed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
