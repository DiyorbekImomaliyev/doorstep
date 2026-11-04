"""Address check at checkout (CK-01). Deterministic; no model call.

Flags the problems behind most avoidable failures: a building or block with no flat/unit number,
a postcode in the wrong format, and anything the carrier's validator (Shippo) reports.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from app.checkout.ports import AddressValidator
from app.orders.schemas import Address

# Words that name a multi-unit building. If one appears and no unit is given, a flat is missing.
_BUILDING = re.compile(
    r"\b(court|house|tower|towers|apartments?|mansions?|building|block|lofts?|residences?|"
    r"heights|plaza|wharf|quay|point|complex|condos?|suites)\b",
    re.IGNORECASE,
)
_UNIT = re.compile(
    r"\b(flat|apt|apartment|unit|suite|ste|room|floor|fl)\b\.?\s*\w+|#\s*\w+|^\s*\d+[a-z]\b",
    re.IGNORECASE,
)
_POSTCODE = {
    "GB": re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.IGNORECASE),
    "US": re.compile(r"^\d{5}(-\d{4})?$"),
}
_UNIT_WORD = {"GB": "flat", "US": "apartment or unit"}


class AddressIssue(BaseModel):
    code: str  # missing_unit | invalid_postcode | carrier_validation
    message: str


class AddressCheckResult(BaseModel):
    valid: bool
    issues: list[AddressIssue] = []
    suggestion: Address | None = None


class AddressChecker:
    def __init__(self, validator: AddressValidator | None = None) -> None:
        self.validator = validator

    def check(self, address: Address) -> AddressCheckResult:
        country = address.country.upper()
        issues: list[AddressIssue] = []
        lines = f"{address.line1} {address.line2}"
        if _BUILDING.search(lines) and not (
            _UNIT.search(address.line1) or _UNIT.search(address.line2)
        ):
            word = _UNIT_WORD.get(country, "unit")
            issues.append(
                AddressIssue(
                    code="missing_unit",
                    message=f"This looks like a building with several homes. Add your {word} "
                    "number so the driver can reach your door.",
                )
            )
        pattern = _POSTCODE.get(country)
        if pattern is not None and not pattern.match(address.postal_code.strip()):
            issues.append(
                AddressIssue(
                    code="invalid_postcode",
                    message=f"The postcode {address.postal_code!r} does not look complete.",
                )
            )
        if self.validator is not None:
            issues.extend(
                AddressIssue(code="carrier_validation", message=problem)
                for problem in self.validator.validate(address)
            )
        return AddressCheckResult(valid=not issues, issues=issues)
