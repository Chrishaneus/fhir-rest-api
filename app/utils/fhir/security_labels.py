"""FHIR meta.security label filtering.

Resources carry zero or more Coding elements in meta.security.
This module checks whether the requesting user's role has sufficient
clearance to read or write a resource tagged with a confidentiality code.

Supported system: http://terminology.hl7.org/CodeSystem/v3-Confidentiality
Codes in ascending clearance order: U < L < M < N < R < V

Role clearance ceilings
  viewer    → N  (normal)
  clinician → R  (restricted)
  admin     → V  (very restricted)

Resources with no confidentiality label are treated as N (normal).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.enums import UserRole

if TYPE_CHECKING:
    from app.db.auth_models import User
    from app.store import ResourceVersion

_CONFIDENTIALITY_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-Confidentiality"

_CLEARANCE_ORDER: list[str] = ["U", "L", "M", "N", "R", "V"]
_CLEARANCE_RANK: dict[str, int] = {code: rank for rank, code in enumerate(_CLEARANCE_ORDER)}

_ROLE_CEILING: dict[UserRole, str] = {
    UserRole.VIEWER: "N",
    UserRole.CLINICIAN: "R",
    UserRole.ADMIN: "V",
}


def _confidentiality_codes(resource: dict[str, Any]) -> list[str]:
    """Extract HL7 v3 confidentiality codes from resource.meta.security."""
    security: list[Any] = (resource.get("meta") or {}).get("security") or []
    codes: list[str] = []
    for coding in security:
        if not isinstance(coding, dict):
            continue
        system = coding.get("system", "")
        code = coding.get("code", "")
        # Accept both a matching system URI and a bare code with no system
        if system in ("", _CONFIDENTIALITY_SYSTEM) and code in _CLEARANCE_RANK:
            codes.append(code)
    return codes


def user_can_see_resource(user: User | None, resource: dict[str, Any]) -> bool:
    """Return True iff *user* has clearance for every security label on *resource*.

    Resources with no confidentiality label are treated as N (normal); all
    roles qualify for N.  user=None (dev mode / no JWT_SECRET) bypasses checks.
    Unknown role codes are denied by default.
    """
    if user is None:
        return True
    try:
        role = UserRole(user.role)
    except ValueError:
        return False
    ceiling_rank = _CLEARANCE_RANK[_ROLE_CEILING.get(role, "N")]
    codes = _confidentiality_codes(resource)
    if not codes:
        return True
    return max(_CLEARANCE_RANK.get(code, 0) for code in codes) <= ceiling_rank


def filter_for_user(user: User | None, versions: list[ResourceVersion]) -> list[ResourceVersion]:
    """Return only the versions the user has clearance to read.

    Tombstone versions (resource=None, deleted=True) are always passed through
    since they carry no content; the caller decides whether to expose them.
    """
    if user is None:
        return versions
    return [v for v in versions if v.resource is None or user_can_see_resource(user, v.resource)]
