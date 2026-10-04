import datetime as dt
import time

import pytest

from app.messaging import RateLimiter
from app.routing import availability, terms
from tests.test_api import H
from tests.test_documents import World


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path)
    world.app.state.messaging.limiter = RateLimiter(1000, 1.0)
    return world


def api(w, who, method, path, **kw):
    return getattr(w.client, method)(path, headers=H(w.tok[who]), **kw)


def answered(w, asker, dept, title, body, answerer, answer):
    p = api(w, asker, "post", "/pings", json={"to_department": dept, "title": title, "body": body}).json()
    assert api(w, answerer, "post", f"/pings/{p['id']}/messages", json={"kind": "answer", "body": answer}).status_code == 201
    return p


def test_terms_drop_filler_words():
    assert terms("How do I fix the checkout 500 error?") == {"fix", "checkout", "500", "error"}


def test_availability_uses_local_working_hours():
    noon_utc_monday = dt.datetime(2026, 10, 5, 16, 0, tzinfo=dt.timezone.utc)          # 12:00 in New York
    ny = availability("America/New_York", noon_utc_monday)
    assert ny["local_time"] == "12:00" and ny["working"] is True
    assert availability("Asia/Singapore", noon_utc_monday)["working"] is False          # midnight there
    assert availability(None)["working"] is None and availability("Not/AZone")["timezone"] is None


def test_people_who_answered_similar_requests_are_suggested_with_reasons(w):
    answered(w, "rep", "engineering", "Checkout returns a 500 error", "Customers see a checkout 500 error since noon",
             "dev", "Flush the cart cache and restart the checkout worker.")
    r = api(w, "slead", "post", "/route", json={"question": "Why does checkout return a 500 error?"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["departments"][0]["department"] == "engineering"
    top = out["experts"][0]
    assert top["username"] == "dev" and "Answered 1 similar request" in top["reasons"]
    assert all(e["username"] != "slead" for e in out["experts"])                          # never yourself
    assert "Flush" not in r.text                                                          # evidence is counted, not quoted


def test_working_experts_rank_ahead_of_sleeping_ones(w):
    for name in ("dev", "lead"):
        answered(w, "rep", "engineering", "Checkout returns a 500 error", "checkout 500 error at noon", name, "Restart it.")
    w.store.update_user(w.row["dev"]["id"], timezone="Pacific/Kiritimati")               # UTC+14
    w.store.update_user(w.row["lead"]["id"], timezone="Pacific/Pago_Pago")               # UTC-11: opposite side of the day
    out = api(w, "slead", "post", "/route", json={"question": "checkout 500 error"}).json()
    by = {e["username"]: e for e in out["experts"]}
    awake = [n for n in ("dev", "lead") if by[n]["working"]]
    if len(awake) == 1:                                                                   # at most one is in office hours
        assert out["experts"][0]["username"] == awake[0]


def test_asking_a_specific_expert_reaches_their_department_and_tops_their_inbox(w):
    r = api(w, "rep", "post", "/pings", json={"to_user": "dev", "title": "Cart cache?", "body": "Is the cart cache stale?"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["to_department"] == "engineering" and p["requested"] == "dev"
    api(w, "rep", "post", "/pings", json={"to_department": "engineering", "title": "Other", "body": "Something else"})
    assert api(w, "dev", "get", "/pings?box=inbox").json()[0]["id"] == p["id"]           # addressed to me first
    assert any(x["id"] == p["id"] for x in api(w, "lead", "get", "/pings?box=inbox").json())   # teammates can still help


def test_you_cannot_direct_a_request_at_someone_who_cannot_answer_it(w):
    for target in ("exec", "rep", "nobody"):                                              # viewer, yourself, unknown
        r = api(w, "rep", "post", "/pings", json={"to_user": target, "title": "t", "body": "b"})
        assert r.status_code == 422 and r.json()["detail"] == "that person can't answer this request"
    assert api(w, "rep", "post", "/pings", json={"title": "t", "body": "b"}).status_code == 422


def test_timezone_and_directory(w):
    assert api(w, "dev", "put", "/auth/me/timezone", json={"timezone": "Europe/London"}).status_code == 200
    assert api(w, "dev", "put", "/auth/me/timezone", json={"timezone": "Mars/Olympus"}).status_code == 422
    assert api(w, "dev", "get", "/auth/me").json()["timezone"] == "Europe/London"
    people = {p["username"]: p for p in api(w, "rep", "get", "/directory").json()}
    assert people["dev"]["timezone"] == "Europe/London" and people["dev"]["department"] == "engineering"
    assert "password_hash" not in str(people) and "totp" not in str(people)


def test_resolving_a_request_drafts_knowledge_for_review(tmp_path):
    w = World(tmp_path, auto_learn=True)
    w.app.state.messaging.limiter = RateLimiter(1000, 1.0)
    p = answered(w, "rep", "engineering", "Checkout returns a 500", "Since noon checkout fails", "dev",
                 "Flush the cart cache with cache flush carts, then restart the checkout worker.")
    assert api(w, "rep", "post", f"/pings/{p['id']}/resolve").status_code == 200
    for _ in range(50):
        draft = api(w, "dev", "get", f"/pings/{p['id']}").json()["draft"]
        if draft:
            break
        time.sleep(0.1)
    assert draft and draft["state"] == "pending"                                          # waits for a second person
    pending = api(w, "lead", "get", "/documents/pending").json()
    assert any(i["source"].startswith(f"thread:{p['id']} ") for i in pending)
    insights = api(w, "rep", "get", "/insights").json()
    assert insights["resolved"] == 1 and insights["median_first_answer_min"] == 0
