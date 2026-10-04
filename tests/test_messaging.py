import json
import sqlite3
import time

import pytest

import roles as R
from app.messaging import RateLimiter
from app.redaction import redaction_marker
from tests.test_api import H
from tests.test_documents import World


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path)
    world.add("dev2", "developer", manager="lead")
    world.add("legal", "legal_counsel", manager="root")
    world.add("sen", "senior_eng", manager="root")
    world.messaging = world.app.state.messaging
    world.messaging.limiter = RateLimiter(1000, 1.0)           # off unless a test turns it on
    return world


def api(w, who, method, path, **kw):
    return getattr(w.client, method)(path, headers=H(w.tok[who]), **kw)


def ping(w, who="rep", to="engineering", title="Why does checkout 500?", body="Customers see a 500 at checkout since noon.", **kw):
    r = api(w, who, "post", "/pings", json={"to_department": to, "title": title, "body": body, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def msg(w, who, pid, body, kind="answer", **kw):
    return api(w, who, "post", f"/pings/{pid}/messages", json={"kind": kind, "body": body, **kw})


def ids(items):
    return [i["id"] for i in items]


# ----------------------------------------------------------------------------- chat
def test_you_see_the_company_channel_and_your_own_department_only(w):
    names = lambda who: {c["id"] for c in api(w, who, "get", "/channels").json()}
    assert names("rep") == {"company", "dept-support"}
    assert names("dev") == {"company", "dept-engineering"}
    assert names("exec") == {"company", "dept-executive"}
    assert all(c["can_post"] for c in api(w, "exec", "get", "/channels").json())     # viewers can chat


def test_department_channels_are_private_to_the_department(w):
    assert api(w, "dev", "post", "/channels/dept-engineering/messages", json={"body": "standup in 5"}).status_code == 201
    assert [m["body"] for m in api(w, "dev2", "get", "/channels/dept-engineering/messages").json()] == ["standup in 5"]
    for who in ("rep", "exec", "legal"):
        assert api(w, who, "get", "/channels/dept-engineering/messages").status_code == 404
        assert api(w, who, "post", "/channels/dept-engineering/messages", json={"body": "hi"}).status_code == 404
    assert api(w, "rep", "get", "/channels/nonexistent/messages").status_code == 404      # same answer as off-limits


def test_company_channel_is_open_to_everyone_and_history_pages(w):
    for i in range(5):
        assert api(w, ["rep", "dev", "exec", "legal", "lead"][i], "post", "/channels/company/messages", json={"body": f"m{i}"}).status_code == 201
    hist = api(w, "rep", "get", "/channels/company/messages").json()
    assert [m["body"] for m in hist] == ["m0", "m1", "m2", "m3", "m4"] and hist[0]["author"] == "rep"
    older = api(w, "dev", "get", f"/channels/company/messages?limit=2&before={hist[3]['id']}").json()
    assert [m["body"] for m in older] == ["m1", "m2"]


def test_chat_validation_and_rate_limit(w):
    assert api(w, "rep", "post", "/channels/company/messages", json={"body": "   "}).status_code == 422
    assert api(w, "rep", "post", "/channels/company/messages", json={"body": "x" * 4001}).status_code == 422
    assert api(w, "rep", "post", "/channels/company/messages").status_code == 422
    w.messaging.limiter = RateLimiter(3, 60.0)
    codes = [api(w, "rep", "post", "/channels/company/messages", json={"body": f"spam{i}"}).status_code for i in range(5)]
    assert codes == [201, 201, 201, 429, 429]
    assert api(w, "dev", "post", "/channels/company/messages", json={"body": "unaffected"}).status_code == 201   # per user


def test_chat_text_is_encrypted_at_rest(w):
    api(w, "dev", "post", "/channels/company/messages", json={"body": "the launch codenamed bluefin is on"})
    raw = sqlite3.connect(w.settings.db_path).execute("SELECT body FROM chat_messages").fetchall()
    assert raw and all("bluefin" not in r[0] for r in raw)


def test_rate_limiter_window():
    t = [0.0]
    lim = RateLimiter(2, 10.0, clock=lambda: t[0])
    lim.check("a"); lim.check("a")
    with pytest.raises(Exception):
        lim.check("a")
    t[0] = 11.0
    lim.check("a")                                              # the window slid


# ------------------------------------------------------------------------- pings: who
def test_a_ping_goes_to_a_department_and_any_qualified_member_sees_it(w):
    p = ping(w)
    inbox = lambda who: ids(api(w, who, "get", "/pings").json())
    assert p["status"] == "open" and p["asker"] == "rep" and p["to_department"] == "engineering"
    assert p["messages"][0]["kind"] == "question" and p["min_role"] == "public"        # default: capped by the rep's clearance
    assert inbox("dev") == inbox("dev2") == inbox("lead") == inbox("sen") == [p["id"]]
    assert inbox("slead") == inbox("exec") == inbox("legal") == inbox("rep") == []
    assert ids(api(w, "rep", "get", "/pings?box=sent").json()) == [p["id"]]
    assert ids(api(w, "dev", "get", "/pings?box=sent").json()) == []


def test_only_participants_can_open_a_thread(w):
    p = ping(w)
    for who in ("dev", "dev2", "rep"):
        assert api(w, who, "get", f"/pings/{p['id']}").status_code == 200
    for who in ("slead", "exec", "legal", "root"):
        assert api(w, who, "get", f"/pings/{p['id']}").status_code == 404
    assert api(w, "dev", "get", "/pings/99999").status_code == 404                        # same answer as off-limits


def test_ping_validation(w):
    bad = lambda **kw: api(w, "rep", "post", "/pings", json={"to_department": "engineering", "title": "t", "body": "b", **kw}).status_code
    assert bad(to_department="nowhere") == 422
    assert bad(title="  ") == 422 and bad(body="") == 422 and bad(title="x" * 201) == 422
    assert bad(min_role="made_up") == 422
    assert bad(min_role="confidential") == 403                                            # above the rep's clearance
    assert bad(min_role="public") == 201
    assert api(w, "rep", "post", "/pings", json={"to_department": "engineering"}).status_code == 422


def test_default_classification_is_internal_when_the_asker_may_use_it(w):
    assert ping(w, "sen", to="support")["min_role"] == "internal"
    assert ping(w, "rep")["min_role"] == "public"


def test_a_classified_ping_only_reaches_members_cleared_for_it(w):
    p = ping(w, "sen", to="engineering", min_role="confidential", title="rotate prod keys", body="steps inside")   # needs 60
    assert ids(api(w, "lead", "get", "/pings").json()) == [p["id"]]                       # senior eng (70) sees it
    assert ids(api(w, "dev", "get", "/pings").json()) == [] and ids(api(w, "dev2", "get", "/pings").json()) == []
    assert api(w, "dev", "get", f"/pings/{p['id']}").status_code == 404
    assert api(w, "dev", "post", f"/pings/{p['id']}/claim").status_code == 404


def test_revoking_the_right_to_answer_cuts_access_immediately(w):
    p = ping(w)
    assert ids(api(w, "dev", "get", "/pings").json()) == [p["id"]]
    r = api(w, "lead", "post", f"/users/{w.row['dev']['id']}/rights", json={"cap": "answer_ping", "action": "revoke"})
    assert r.status_code == 200
    assert api(w, "dev", "get", "/pings").json() == []
    assert api(w, "dev", "get", f"/pings/{p['id']}").status_code == 404
    assert api(w, "dev", "post", f"/pings/{p['id']}/claim").status_code == 404


def test_lowering_clearance_mid_thread_hides_the_ping(w):
    p = ping(w, "sen", to="engineering", min_role="internal")                              # needs 30
    assert api(w, "dev", "get", f"/pings/{p['id']}").status_code == 200
    w.client.put(f"/users/{w.row['dev']['id']}/clearance", headers=H(w.tok["root"]), json={"level": 10})
    assert api(w, "dev", "get", f"/pings/{p['id']}").status_code == 404


# -------------------------------------------------------------------- pings: lifecycle
def test_claim_is_exclusive_and_the_asker_cannot_claim_their_own(w):
    p = ping(w)
    r = api(w, "dev", "post", f"/pings/{p['id']}/claim")
    assert r.status_code == 200 and r.json()["status"] == "claimed" and r.json()["claimed_by"] == "dev"
    assert api(w, "dev2", "post", f"/pings/{p['id']}/claim").status_code == 409
    assert api(w, "rep", "post", f"/pings/{p['id']}/claim").status_code == 403
    assert api(w, "slead", "post", f"/pings/{p['id']}/claim").status_code == 404


def test_only_the_claimer_or_a_senior_can_release(w):
    p = ping(w)
    api(w, "dev", "post", f"/pings/{p['id']}/claim")
    assert api(w, "dev2", "post", f"/pings/{p['id']}/release").status_code == 403
    assert api(w, "rep", "post", f"/pings/{p['id']}/release").status_code == 403
    assert api(w, "lead", "post", f"/pings/{p['id']}/release").json()["status"] == "open"       # a senior unblocks it
    api(w, "dev", "post", f"/pings/{p['id']}/claim")
    assert api(w, "dev", "post", f"/pings/{p['id']}/release").json()["claimed_by"] is None
    assert api(w, "dev", "post", f"/pings/{p['id']}/release").status_code == 409                 # not claimed any more


def test_an_abandoned_claim_expires_so_a_ping_cannot_get_stuck(w):
    p = ping(w)
    api(w, "dev", "post", f"/pings/{p['id']}/claim")
    with sqlite3.connect(w.settings.db_path) as c:
        c.execute("UPDATE pings SET claimed_at = ? WHERE id = ?", (time.time() - 3600, p["id"]))
    assert api(w, "rep", "get", f"/pings/{p['id']}").json()["status"] == "open"                 # shown as open again
    assert api(w, "dev2", "post", f"/pings/{p['id']}/claim").json()["claimed_by"] == "dev2"      # and anyone can pick it up


def test_answering_flow_and_who_may_speak(w):
    p = ping(w)
    pid = p["id"]
    api(w, "dev", "post", f"/pings/{pid}/claim")
    assert msg(w, "dev2", pid, "I think it is the cache").status_code == 409          # claimed by dev: comment instead
    assert msg(w, "dev2", pid, "which region?", kind="comment").status_code == 201
    assert msg(w, "rep", pid, "I am answering my own question", kind="answer").status_code == 403
    assert msg(w, "slead", pid, "hello", kind="comment").status_code == 404
    assert msg(w, "dev", pid, "Cache was stale; flushed it. Checkout works now.").status_code == 201
    view = api(w, "rep", "get", f"/pings/{pid}").json()
    assert view["status"] == "answered" and [m["kind"] for m in view["messages"]] == ["question", "comment", "answer"]
    assert msg(w, "dev2", pid, "adding: also bump the TTL").status_code == 201          # others may add once answered
    assert msg(w, "rep", pid, "thanks, confirming it is fixed", kind="comment").status_code == 201
    assert msg(w, "dev", pid, "x", kind="shout").status_code == 422


def test_a_first_answer_claims_an_unclaimed_ping(w):
    p = ping(w)
    assert msg(w, "dev", p["id"], "restart the worker").status_code == 201
    view = api(w, "dev", "get", f"/pings/{p['id']}").json()
    assert view["status"] == "answered" and view["claimed_by"] == "dev"


def test_resolve_close_and_the_end_states(w):
    a, b = ping(w, title="a"), ping(w, title="b")
    assert api(w, "rep", "post", f"/pings/{a['id']}/resolve").status_code == 409          # nothing answered yet
    msg(w, "dev", a["id"], "done")
    assert api(w, "dev", "post", f"/pings/{a['id']}/resolve").status_code == 403          # only the asker resolves
    assert api(w, "rep", "post", f"/pings/{a['id']}/resolve").json()["status"] == "resolved"
    assert msg(w, "dev", a["id"], "late", kind="comment").status_code == 409              # resolved threads are closed to new messages
    assert api(w, "rep", "post", f"/pings/{a['id']}/close").status_code == 409            # answered: resolve, not withdraw
    assert api(w, "dev", "post", f"/pings/{b['id']}/close").status_code == 403
    assert api(w, "rep", "post", f"/pings/{b['id']}/close").json()["status"] == "closed"
    assert msg(w, "dev", b["id"], "x").status_code == 409
    assert api(w, "dev", "get", "/pings").json() == []                                    # finished threads leave the inbox
    assert set(ids(api(w, "dev", "get", "/pings?include_closed=true").json())) == {a["id"], b["id"]}


def test_open_ping_cap_and_creation_rate_limit(w):
    w.messaging.open_ping_limit = 2
    ping(w, title="1"); ping(w, title="2")
    r = api(w, "rep", "post", "/pings", json={"to_department": "engineering", "title": "3", "body": "b"})
    assert r.status_code == 429 and "open pings" in r.json()["detail"]
    w.messaging.open_ping_limit = 20
    w.messaging.limiter = RateLimiter(2, 60.0)
    codes = [api(w, "dev", "post", "/pings", json={"to_department": "support", "title": str(i), "body": "b"}).status_code for i in range(3)]
    assert codes == [201, 201, 429]


def test_ping_text_is_encrypted_at_rest(w):
    ping(w, title="secret-title-zebra", body="secret-body-giraffe")
    with sqlite3.connect(w.settings.db_path) as c:
        raw = " ".join(str(v) for row in c.execute("SELECT title FROM pings UNION ALL SELECT body FROM ping_messages") for v in row)
    assert "zebra" not in raw and "giraffe" not in raw


def test_audit_events_carry_ids_never_text(w):
    p = ping(w, title="title-with-secret-xylophone", body="body-with-secret-yodel")
    api(w, "dev", "post", f"/pings/{p['id']}/claim")
    msg(w, "dev", p["id"], "answer-with-secret-zither")
    api(w, "rep", "post", f"/pings/{p['id']}/resolve")
    kinds = [e["kind"] for e in w.store.list_events() if e["kind"].startswith("PING_")]
    assert kinds == ["PING_CREATED", "PING_CLAIMED", "PING_ANSWERED", "PING_RESOLVED"]
    dump = json.dumps(w.store.list_events())
    for secret in ("xylophone", "yodel", "zither"):
        assert secret not in dump
    ev = [e for e in w.store.list_events() if e["kind"] == "PING_CREATED"][0]
    assert ev["department"] == "engineering" and ev["min_clearance"] == 0


# ------------------------------------------------- per-message classification (the key rule)
def test_an_answer_above_the_askers_clearance_is_redacted_for_them(w):
    p = ping(w, "rep")                                                            # rep: clearance 20, public ping
    secret = "prod db password is hunter2-orchid"
    r = msg(w, "sen", p["id"], secret, min_role="confidential")                   # senior writes a 60-level answer
    assert r.status_code == 201
    msg(w, "sen", p["id"], "I have fixed it; please retry in five minutes.")      # and a cleared version
    for_rep = api(w, "rep", "get", f"/pings/{p['id']}")
    assert "hunter2-orchid" not in for_rep.text and "password" not in for_rep.text
    answers = [m for m in for_rep.json()["messages"] if m["kind"] == "answer"]
    assert answers[0]["redacted"] and answers[0]["body"] == redaction_marker(60) and answers[0]["min_role"] is None
    assert not answers[1]["redacted"] and "retry" in answers[1]["body"]
    as_sen = api(w, "sen", "get", f"/pings/{p['id']}").json()["messages"]
    assert any(secret in m["body"] for m in as_sen)                               # cleared readers see the original
    assert any(secret in m["body"] for m in api(w, "lead", "get", f"/pings/{p['id']}").json()["messages"])
    assert not any(secret in m["body"] for m in api(w, "dev", "get", f"/pings/{p['id']}").json()["messages"])   # dev is 40


def test_you_cannot_classify_a_message_above_your_own_clearance(w):
    p = ping(w)
    assert msg(w, "dev", p["id"], "x", min_role="confidential").status_code == 403      # dev is 40, needs 60
    assert msg(w, "dev", p["id"], "x", min_role="bogus").status_code == 422
    assert msg(w, "dev", p["id"], "x", min_role="internal").status_code == 201


def test_a_comment_above_an_answerers_clearance_is_hidden_from_them(w):
    p = ping(w, "exec", to="engineering", min_role="internal", title="board q", body="board question")   # exec clearance 80
    msg(w, "dev", p["id"], "answer")
    assert msg(w, "exec", p["id"], "background: acquisition target is X", kind="comment", min_role="confidential").status_code == 201
    seen = api(w, "dev", "get", f"/pings/{p['id']}")
    assert "acquisition" not in seen.text


# --------------------------------------------------------------------------- real time
def ws_auth(client, token):
    ws = client.websocket_connect("/ws")
    sock = ws.__enter__()
    sock.send_json({"type": "auth", "token": token})
    return ws, sock


def test_websocket_requires_a_valid_session(w):
    from starlette.websockets import WebSocketDisconnect
    with w.client.websocket_connect("/ws") as sock:
        sock.send_json({"type": "auth", "token": "not-a-token"})
        with pytest.raises(WebSocketDisconnect) as exc:
            sock.receive_json()
        assert exc.value.code == 4401
    with w.client.websocket_connect("/ws") as sock:
        sock.send_json({"hello": "world"})
        with pytest.raises(WebSocketDisconnect):
            sock.receive_json()
    # a token for the wrong purpose is not a session
    from app import security
    enroll_token, _, _ = security.issue_token(w.settings, w.row["dev"]["id"], "enroll", 60)
    with w.client.websocket_connect("/ws") as sock:
        sock.send_json({"type": "auth", "token": enroll_token})
        with pytest.raises(WebSocketDisconnect):
            sock.receive_json()


def test_new_pings_are_pushed_only_to_those_who_may_answer(w):
    c1, dev = ws_auth(w.client, w.tok["dev"])
    c2, slead = ws_auth(w.client, w.tok["slead"])
    c3, rep = ws_auth(w.client, w.tok["rep"])
    try:
        assert dev.receive_json()["type"] == "ready" and slead.receive_json()["type"] == "ready" and rep.receive_json()["type"] == "ready"
        p = ping(w)
        got = dev.receive_json()
        assert got["type"] == "ping.created" and got["ping"]["id"] == p["id"] and got["ping"]["can"]["claim"] is True
        assert rep.receive_json()["ping"]["id"] == p["id"]                       # the asker's other devices stay in sync
        # the support lead is not in engineering: the very next thing they get is the chat message, not the ping
        api(w, "dev", "post", "/channels/company/messages", json={"body": "hello all"})
        assert slead.receive_json()["type"] == "chat.message"
    finally:
        for c in (c1, c2, c3):
            c.__exit__(None, None, None)


def test_pushed_answers_are_redacted_per_recipient(w):
    p = ping(w, "rep")
    c1, rep = ws_auth(w.client, w.tok["rep"])
    c2, sen2 = ws_auth(w.client, w.tok["lead"])
    try:
        rep.receive_json(); sen2.receive_json()
        msg(w, "sen", p["id"], "the vault root token is s3cr3t-pelican", min_role="confidential")
        to_rep, to_senior = rep.receive_json(), sen2.receive_json()
        assert to_rep["type"] == "ping.message" and to_rep["message"]["redacted"] and "pelican" not in json.dumps(to_rep)
        assert "pelican" in to_senior["message"]["body"] and not to_senior["message"]["redacted"]
    finally:
        c1.__exit__(None, None, None); c2.__exit__(None, None, None)


def test_chat_is_pushed_only_to_those_who_may_read_the_channel(w):
    c1, dev2 = ws_auth(w.client, w.tok["dev2"])
    c2, rep = ws_auth(w.client, w.tok["rep"])
    try:
        dev2.receive_json(); rep.receive_json()
        api(w, "dev", "post", "/channels/dept-engineering/messages", json={"body": "internal eng chatter"})
        api(w, "dev", "post", "/channels/company/messages", json={"body": "all hands at 3"})
        assert dev2.receive_json()["message"]["body"] == "internal eng chatter"
        assert dev2.receive_json()["message"]["body"] == "all hands at 3"
        assert rep.receive_json()["message"]["body"] == "all hands at 3"          # the eng message never reached support
    finally:
        c1.__exit__(None, None, None); c2.__exit__(None, None, None)


def test_a_revoked_right_stops_pushes_at_once(w):
    c1, dev = ws_auth(w.client, w.tok["dev"])
    try:
        dev.receive_json()
        api(w, "lead", "post", f"/users/{w.row['dev']['id']}/rights", json={"cap": "answer_ping", "action": "revoke"})
        ping(w, title="after revocation")
        api(w, "rep", "post", "/channels/company/messages", json={"body": "marker"})
        assert dev.receive_json()["type"] == "chat.message"                        # the ping was not delivered
    finally:
        c1.__exit__(None, None, None)


def test_logging_out_ends_the_live_connection_on_the_next_event(w):
    from starlette.websockets import WebSocketDisconnect
    c1, dev = ws_auth(w.client, w.tok["dev"])
    try:
        dev.receive_json()
        assert api(w, "dev", "post", "/auth/logout").status_code == 200
        api(w, "rep", "post", "/channels/company/messages", json={"body": "anyone there?"})
        with pytest.raises(WebSocketDisconnect) as exc:
            dev.receive_json()
        assert exc.value.code == 4401
    finally:
        c1.__exit__(None, None, None)


def test_the_connection_closes_when_its_token_expires(w):
    from starlette.websockets import WebSocketDisconnect
    from app import security
    tok, jti, exp = security.issue_token(w.settings, w.row["dev"]["id"], "access", 2)
    w.store.create_session(jti, w.row["dev"]["id"], None, None, exp)
    c1, sock = ws_auth(w.client, tok)
    try:
        assert sock.receive_json()["type"] == "ready"
        with pytest.raises(WebSocketDisconnect) as exc:
            sock.receive_json()                                                    # blocks ~2s, then the server closes
        assert exc.value.code == 4401
    finally:
        c1.__exit__(None, None, None)


def test_hub_with_no_listeners_is_a_no_op(w):
    assert w.app.state.hub.publish(lambda u: {"x": 1}) == 0 and w.app.state.hub.count() == 0
