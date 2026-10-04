"""Roles, clearance and permissions: the single source of truth for access control.

Three independent axes (the PRD folded them into one ladder, which cannot express
"a support lead may approve support docs but not engineering docs"):

    department  who you work for      -> routing, and whose docs you may approve
    level       member / senior / admin -> what you may do (capabilities)
    clearance   0-100                 -> what you may read

Documents are tagged with a ``min_role`` label in Layer 1. That label resolves to
a numeric minimum clearance here, and Layers 2/3/5 compare numbers, so changing
a user's clearance in the permission matrix takes effect immediately.

Unknown labels fail closed (treated as clearance 100).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Dict, FrozenSet, List, Optional

MIN_CLEARANCE, MAX_CLEARANCE = 0, 100


class Cap(str, Enum):
    CHAT = "chat"                            # post in your department's channel and the company channel
    ASK = "ask"                              # query the knowledge engine
    PING_DEPARTMENT = "ping_department"      # send a question to a department
    ANSWER_PING = "answer_ping"              # answer a ping addressed to your department
    UPLOAD_DOC = "upload_doc"                # submit a document (staged if it changes an existing one)
    PROPOSE_EDIT = "propose_edit"            # flag / propose a correction
    APPROVE_DOC = "approve_doc"              # approve staged updates, commit to the ledger
    VIEW_AUDIT = "view_audit"                # see the audit ledger (fields still redacted by clearance)
    MANAGE_ACCESS = "manage_access"          # edit clearance / grant / revoke rights of your direct reports
    PURGE_DOCUMENT = "purge_document"        # GDPR right-to-be-forgotten purge
    EXPORT_COMPLIANCE = "export_compliance"  # compliance ZIP export


# What each level can do. Levels stack: senior includes member, admin includes senior.
_MEMBER = frozenset({Cap.CHAT, Cap.ASK, Cap.PING_DEPARTMENT, Cap.ANSWER_PING, Cap.UPLOAD_DOC, Cap.PROPOSE_EDIT})
_SENIOR = _MEMBER | {Cap.APPROVE_DOC, Cap.VIEW_AUDIT, Cap.MANAGE_ACCESS}
_ADMIN = _SENIOR | {Cap.PURGE_DOCUMENT, Cap.EXPORT_COMPLIANCE}
# Executives read and ask; they do not write to or approve the source of truth.
_VIEWER = frozenset({Cap.CHAT, Cap.ASK, Cap.PING_DEPARTMENT, Cap.VIEW_AUDIT})

LEVEL_CAPS: Dict[str, FrozenSet[Cap]] = {
    "member": _MEMBER, "senior": _SENIOR, "admin": _ADMIN, "viewer": _VIEWER,
}


@dataclass(frozen=True)
class Role:
    name: str
    label: str
    department: str
    level: str          # key of LEVEL_CAPS
    clearance: int      # default; an admin can override it per user
    persona: str        # LLM answer style (Layer 3): support | developer | executive

    @property
    def capabilities(self) -> FrozenSet[Cap]:
        return LEVEL_CAPS[self.level]


ROLES: Dict[str, Role] = {r.name: r for r in (
    Role("support_rep",      "Support Rep",         "support",     "member", 20, "support"),
    Role("support_lead",     "Support Lead",        "support",     "senior", 50, "support"),
    Role("developer",        "Developer",           "engineering", "member", 40, "developer"),
    Role("senior_eng",       "Senior Engineer",     "engineering", "senior", 70, "developer"),
    Role("product_manager",  "Product Manager",     "product",     "member", 40, "executive"),
    Role("legal_counsel",    "Legal Counsel",       "legal",       "senior", 80, "executive"),
    Role("executive",        "Executive",           "executive",   "viewer", 80, "executive"),
    Role("security_admin",   "Security Admin",      "security",    "admin", 100, "developer"),
)}

DEPARTMENTS: List[str] = sorted({r.department for r in ROLES.values()})

# Classification labels a document can carry instead of a role name.
CLASSIFICATIONS: Dict[str, int] = {"public": 0, "internal": 30, "confidential": 60, "restricted": 90}


@dataclass(frozen=True)
class User:
    id: str
    role: str
    clearance: Optional[int] = None      # per-user override from the permission matrix
    manager_id: Optional[str] = None     # who this person reports to (the org chart)
    granted: FrozenSet[Cap] = frozenset()   # rights added on top of the role
    revoked: FrozenSet[Cap] = frozenset()   # rights removed from the role

    @property
    def role_def(self) -> Role:
        return ROLES[self.role]

    @property
    def department(self) -> str:
        return self.role_def.department

    @property
    def capabilities(self) -> FrozenSet[Cap]:
        return (self.role_def.capabilities | self.granted) - self.revoked

    @property
    def effective_clearance(self) -> int:
        c = self.role_def.clearance if self.clearance is None else self.clearance
        return max(MIN_CLEARANCE, min(MAX_CLEARANCE, c))


def can(user: User, cap: Cap) -> bool:
    return cap in user.capabilities


def min_clearance_for(label: Optional[str]) -> int:
    """Resolve a document's ``min_role`` label to a minimum clearance.

    Accepts a classification ("confidential"), a role name ("senior_eng"), or
    a legacy label (e.g. "employee"). Anything unrecognized fails closed.
    """
    key = (label or "").strip().lower()
    if key in CLASSIFICATIONS:
        return CLASSIFICATIONS[key]
    if key in ROLES:
        return ROLES[key].clearance
    if key == "employee":
        return CLASSIFICATIONS["internal"]
    return MAX_CLEARANCE


def can_read(user: User, min_role: Optional[str]) -> bool:
    return user.effective_clearance >= min_clearance_for(min_role)


def can_approve(user: User, doc_department: Optional[str]) -> bool:
    """Seniors approve documents in their own department; admins approve any.
    A document with no department can be approved by any approver."""
    if not can(user, Cap.APPROVE_DOC):
        return False
    if user.role_def.level == "admin" or not doc_department:
        return True
    return user.department == doc_department


def manages(actor: User, target: User) -> bool:
    """True if ``actor`` may administer ``target``'s access: the target reports
    directly to the actor, or the actor is an admin. Nobody manages themselves,
    and a manager's own manager is not covered (reporting lines only point down)."""
    if actor.id == target.id:
        return False
    return actor.role_def.level == "admin" or target.manager_id == actor.id


def _may_administer(actor: User, target: User) -> bool:
    # Needs the right, the reporting line, and must not be reaching up the ladder.
    return (can(actor, Cap.MANAGE_ACCESS) and manages(actor, target)
            and target.effective_clearance <= actor.effective_clearance)


def can_set_clearance(actor: User, target: User, new_level: int) -> bool:
    """Set a direct report's clearance: integer 0-100, never above the actor's own
    (stops anyone minting a user more privileged than themselves)."""
    if isinstance(new_level, bool) or not isinstance(new_level, int):
        return False
    return (_may_administer(actor, target)
            and MIN_CLEARANCE <= new_level <= min(MAX_CLEARANCE, actor.effective_clearance))


def can_grant(actor: User, target: User, cap: Cap) -> bool:
    """You can only hand out rights you hold yourself."""
    return _may_administer(actor, target) and can(actor, cap)


def can_revoke(actor: User, target: User, cap: Cap) -> bool:
    return _may_administer(actor, target)


def set_clearance(actor: User, target: User, new_level: int) -> User:
    if not can_set_clearance(actor, target, new_level):
        raise PermissionError(f"{actor.id} may not set {target.id}'s clearance to {new_level!r}")
    return replace(target, clearance=new_level)


def grant_right(actor: User, target: User, cap: Cap) -> User:
    if not can_grant(actor, target, cap):
        raise PermissionError(f"{actor.id} may not grant {cap.value} to {target.id}")
    return replace(target, granted=target.granted | {cap}, revoked=target.revoked - {cap})


def revoke_right(actor: User, target: User, cap: Cap) -> User:
    if not can_revoke(actor, target, cap):
        raise PermissionError(f"{actor.id} may not revoke {cap.value} from {target.id}")
    return replace(target, revoked=target.revoked | {cap}, granted=target.granted - {cap})


def ingestion_role(user: User) -> str:
    """Map to Layer 1's two-value uploader role: only approvers auto-commit deltas."""
    return "senior" if can(user, Cap.APPROVE_DOC) else "junior"


def answerers_for(department: str) -> List[str]:
    """Role names that can pick up a ping addressed to ``department``."""
    return [r.name for r in ROLES.values()
            if r.department == department and Cap.ANSWER_PING in r.capabilities]


def reviewers_for(department: Optional[str], users: List[User]) -> List[User]:
    """Who gets a staged update for review: approvers in that department, plus admins."""
    return [u for u in users if can_approve(u, department)]
