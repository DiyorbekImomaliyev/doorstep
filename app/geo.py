"""Postcode areas. Minimum data (hard rule 9): code compares and shows areas, never addresses.

- `postcode_district` is the display form: UK outward code ('N1'), US three-digit area ('941xx').
- `delivery_area` is the comparison form for the delivered-scan audit (SA-02): UK outward code,
  US ZIP5. A postcode that does not parse gives None, which every caller treats as unknown.
"""

from __future__ import annotations

import re

_UK_POSTCODE = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?)(?: ?\d[A-Z]{2})?$")
_US_ZIP = re.compile(r"^(\d{5})(?:-?\d{4})?$")
_UK_COUNTRIES = frozenset({"GB", "UK"})


def postcode_district(postcode: str) -> str:
    """'N1 9GU' -> 'N1'; US ZIP '94107' -> '941xx'. Minimum data (hard rule 9)."""
    text = postcode.strip().upper()
    if not text:
        return ""
    if text[:5].isdigit():
        return text[:3] + "xx"
    return text.split()[0] if " " in text else text[:-3] or text


def delivery_area(postcode: str, country: str) -> str | None:
    """'N1 9GU', GB -> 'N1'; '94107-1234', US -> '94107'; anything unreadable -> None."""
    text = " ".join(postcode.upper().split())
    code = country.strip().upper()
    if code in _UK_COUNTRIES:
        uk = _UK_POSTCODE.match(text)
        return uk.group(1) if uk else None
    if code == "US":
        us = _US_ZIP.match(text.replace(" ", ""))
        return us.group(1) if us else None
    return None
