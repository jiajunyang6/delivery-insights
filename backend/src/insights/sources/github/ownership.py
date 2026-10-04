"""Parse CODEOWNERS and area-owners files into ownership rules; no I/O."""

import re

from insights.domain import OwnershipRule

AREA = re.compile(r"^area-[A-Za-z0-9._-]+$")
OWNER = re.compile(r"@[A-Za-z0-9._/-]+")


def parse_codeowners(text: str) -> list[OwnershipRule]:
    text = text.replace("\x00", "")
    rules = []
    for number, raw in enumerate(text.splitlines(), 1):
        escaped = False
        end = len(raw)
        for i, char in enumerate(raw):
            if char == "#" and not escaped:
                end = i
                break
            escaped = char == "\\" and not escaped
        tokens = raw[:end].split()
        if tokens:
            owners = tuple(dict.fromkeys(t for t in tokens[1:] if t.startswith("@")))
            rules.append(OwnershipRule("codeowners", tokens[0], owners, number))
    return rules


def parse_area_owners(text: str) -> list[OwnershipRule]:
    text = text.replace("\x00", "")
    rules = []
    for number, raw in enumerate(text.splitlines(), 1):
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if len(cells) >= 2 and AREA.fullmatch(cells[0]):
            owners = tuple(dict.fromkeys(OWNER.findall(" ".join(cells[1:3]))))
            rules.append(OwnershipRule("area_owners", cells[0], owners, number))
    return rules
