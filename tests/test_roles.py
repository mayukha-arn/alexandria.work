import roles as R
from roles import Cap, User


def test_every_role_is_consistent():
    for r in R.ROLES.values():
        assert R.MIN_CLEARANCE <= r.clearance <= R.MAX_CLEARANCE
        assert r.persona in {"support", "developer", "executive"}
        assert r.level in R.LEVEL_CAPS


def test_prd_hierarchy_preserved():
    # PRD: tier1 support < developer < senior eng
    c = lambda n: R.ROLES[n].clearance
    assert c("support_rep") < c("developer") < c("senior_eng")


def test_read_gating_by_clearance():
    rep, dev, sen = User("a", "support_rep"), User("b", "developer"), User("c", "senior_eng")
    assert R.can_read(rep, "public") and not R.can_read(rep, "developer")
    assert R.can_read(dev, "developer") and not R.can_read(dev, "senior_eng")
    assert R.can_read(sen, "senior_eng") and R.can_read(sen, "developer")


def test_unknown_label_fails_closed():
    assert R.min_clearance_for("typo_role") == 100
    assert R.min_clearance_for(None) == 100
    assert not R.can_read(User("a", "senior_eng"), "typo_role")
    assert R.can_read(User("a", "security_admin"), "typo_role")


def test_clearance_override_takes_effect_and_is_clamped():
    rep = User("a", "support_rep", clearance=75)
    assert R.can_read(rep, "developer")
    assert User("a", "support_rep", clearance=500).effective_clearance == 100
    assert User("a", "support_rep", clearance=-5).effective_clearance == 0


def test_capabilities_by_level():
    assert Cap.APPROVE_DOC not in R.ROLES["developer"].capabilities
    assert Cap.APPROVE_DOC in R.ROLES["senior_eng"].capabilities
    assert Cap.PURGE_DOCUMENT not in R.ROLES["senior_eng"].capabilities
    assert Cap.PURGE_DOCUMENT in R.ROLES["security_admin"].capabilities
    ex = R.ROLES["executive"].capabilities
    assert Cap.ASK in ex and Cap.UPLOAD_DOC not in ex and Cap.APPROVE_DOC not in ex


def test_approval_is_department_scoped():
    sen, lead, admin = User("1", "senior_eng"), User("2", "support_lead"), User("3", "security_admin")
    dev = User("4", "developer")
    assert R.can_approve(sen, "engineering") and not R.can_approve(sen, "support")
    assert R.can_approve(lead, "support") and not R.can_approve(lead, "engineering")
    assert R.can_approve(admin, "engineering") and R.can_approve(admin, "support")
    assert not R.can_approve(dev, "engineering")


def _org():
    boss = User("boss", "senior_eng", manager_id="cto")
    dev = User("dev", "developer", manager_id="boss")
    peer_dev = User("peer", "developer", manager_id="other_boss")
    admin = User("root", "security_admin")
    return boss, dev, peer_dev, admin


def test_manager_can_set_direct_report_clearance():
    boss, dev, peer, _ = _org()
    assert R.can_set_clearance(boss, dev, 55)
    assert R.set_clearance(boss, dev, 55).effective_clearance == 55
    assert not R.can_set_clearance(boss, peer, 55)       # not under this manager


def test_cannot_reach_up_sideways_or_to_self():
    boss, dev, peer, admin = _org()
    assert not R.can_set_clearance(dev, boss, 10)        # report cannot manage manager
    assert not R.can_set_clearance(dev, peer, 10)        # no MANAGE_ACCESS, not their report
    assert not R.can_set_clearance(boss, boss, 10)       # not yourself
    assert R.can_set_clearance(admin, peer, 10)          # admins govern everyone


def test_clearance_changes_cannot_escalate():
    boss, dev, _, admin = _org()
    assert not R.can_set_clearance(boss, dev, 71)        # above the manager's own 70
    assert R.can_set_clearance(boss, dev, 70)
    assert not R.can_set_clearance(boss, dev, 101)
    assert not R.can_set_clearance(boss, dev, -1)
    assert not R.can_set_clearance(boss, dev, True)
    assert not R.can_set_clearance(boss, dev, 50.5)
    lowered = User("l", "security_admin", clearance=50)
    assert not R.can_set_clearance(lowered, dev, 90)
    # cannot modify someone who outranks you in clearance even if they report to you
    over = User("over", "developer", clearance=95, manager_id="boss")
    assert not R.can_set_clearance(boss, over, 10)


def test_grant_and_revoke_rights():
    boss, dev, peer, _ = _org()
    assert Cap.APPROVE_DOC not in dev.capabilities
    granted = R.grant_right(boss, dev, Cap.APPROVE_DOC)
    assert R.can(granted, Cap.APPROVE_DOC) and R.ingestion_role(granted) == "senior"

    revoked = R.revoke_right(boss, dev, Cap.UPLOAD_DOC)
    assert not R.can(revoked, Cap.UPLOAD_DOC) and R.can(revoked, Cap.ASK)
    # revoke then grant restores it, and vice versa
    assert R.can(R.grant_right(boss, revoked, Cap.UPLOAD_DOC), Cap.UPLOAD_DOC)
    assert not R.can(R.revoke_right(boss, granted, Cap.APPROVE_DOC), Cap.APPROVE_DOC)


def test_cannot_grant_rights_you_do_not_hold():
    boss, dev, _, _ = _org()
    for cap in (Cap.PURGE_DOCUMENT, Cap.EXPORT_COMPLIANCE):
        assert not R.can_grant(boss, dev, cap)
    try:
        R.grant_right(boss, dev, Cap.PURGE_DOCUMENT)
        assert False, "should raise"
    except PermissionError:
        pass
    assert not R.can_grant(boss, User("p", "developer", manager_id="other"), Cap.ASK)


def test_revoked_approver_cannot_approve():
    boss, _, _, _ = _org()
    sen = User("s", "senior_eng", manager_id="boss")
    assert R.can_approve(sen, "engineering")
    assert not R.can_approve(R.revoke_right(boss, sen, Cap.APPROVE_DOC), "engineering")


def test_reviewers_for_department():
    users = [User("a", "senior_eng"), User("b", "support_lead"), User("c", "developer"),
             User("d", "security_admin")]
    assert {u.id for u in R.reviewers_for("engineering", users)} == {"a", "d"}
    assert {u.id for u in R.reviewers_for("support", users)} == {"b", "d"}


def test_ingestion_role_and_answerers():
    assert R.ingestion_role(User("1", "senior_eng")) == "senior"
    assert R.ingestion_role(User("1", "developer")) == "junior"
    assert R.ingestion_role(User("1", "executive")) == "junior"
    assert set(R.answerers_for("engineering")) == {"developer", "senior_eng"}
    assert "executive" not in R.answerers_for("executive")
