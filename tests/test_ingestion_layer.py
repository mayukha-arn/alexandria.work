import json
import sqlite3

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

import ingestion_layer as il

BASE_LINES = [
    f"Section {i}: The payroll service processes employee records for department {i} "
    f"and reconciles them nightly against the ledger before reporting totals."
    for i in range(1, 41)
]


def make_pdf(path, lines):
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 750
    for line in lines:
        if y < 50:
            c.showPage()
            y = 750
        c.drawString(40, y, line)
        y -= 14
    c.save()
    return str(path)


def html_page(lines):
    body = "".join(f"<p>{l}</p>" for l in lines)
    return (
        "<html><head><title>Doc</title><style>.x{}</style><script>var a=1;</script></head>"
        "<body><header>Site Header</header><nav>Home | About</nav>"
        f"<main>{body}</main><footer>Copyright footer</footer></body></html>"
    )


class FakeResp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200

    def raise_for_status(self):
        pass


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "registry.db")


def test_new_pdf_ingested_with_chunks(tmp_path, db):
    pdf = make_pdf(tmp_path / "a.pdf", BASE_LINES)
    r = il.process_incoming_source("pdf", pdf, "junior", "developer", db_path=db)

    assert r["status"] == "new_document_ingested"
    assert r["chunk_count"] == len(r["chunks"]) > 0
    first = r["chunks"][0]
    assert first["chunk_id"] == f"{r['doc_hash']}_0"
    assert first["min_role"] == "developer"
    assert first["status"] == "active"
    assert first["timestamp"].endswith("Z")
    json.dumps(r)  # serializable


def test_exact_hash_duplicate_skipped(tmp_path, db):
    pdf = make_pdf(tmp_path / "a.pdf", BASE_LINES)
    il.process_incoming_source("pdf", pdf, "senior", "developer", db_path=db)
    r = il.process_incoming_source("pdf", pdf, "senior", "developer", db_path=db)
    assert r["status_code"] == 200
    assert r["status"] == "duplicate_skipped"


def test_web_strips_boilerplate(monkeypatch, db):
    monkeypatch.setattr(il.requests, "get", lambda *a, **k: FakeResp(html_page(BASE_LINES)))
    r = il.process_incoming_source("web", "https://docs.example/payroll", "junior", "employee", db_path=db)
    text = " ".join(c["text"] for c in r["chunks"])
    assert r["status"] == "new_document_ingested"
    assert "Section 1:" in text
    for leaked in ("Site Header", "Home | About", "Copyright footer", "var a", ".x{}"):
        assert leaked not in text


def test_near_duplicate_discarded(monkeypatch, db):
    pages = iter([html_page(BASE_LINES), html_page(BASE_LINES + ["  "]).replace("<p>", "<p> ")])
    # whitespace-only differences normalize to the same hash -> duplicate_skipped
    monkeypatch.setattr(il.requests, "get", lambda *a, **k: FakeResp(next(pages)))
    il.process_incoming_source("web", "https://docs.example/u1", "senior", "employee", db_path=db)
    assert il.process_incoming_source("web", "https://docs.example/u2", "senior", "employee", db_path=db)["status"] == "duplicate_skipped"

    # a one-word change in a long document is a near-exact duplicate
    long_lines = BASE_LINES * 5
    tweaked = list(long_lines)
    tweaked[-1] = tweaked[-1].replace("totals", "sums")
    pages = iter([html_page(long_lines), html_page(tweaked)])
    db2 = db + "2"
    il.process_incoming_source("web", "https://docs.example/u1", "senior", "employee", db_path=db2)
    r = il.process_incoming_source("web", "https://docs.example/u2", "senior", "employee", db_path=db2)
    assert r["status"] == "near_duplicate_discarded"
    assert r["similarity"] >= il.EXACT_THRESHOLD


def _delta_lines():
    lines = list(BASE_LINES)
    for i in range(0, 40, 4):
        lines[i] = f"Section {i + 1}: Rotate the admin password every 30 days per the new policy."
    return lines


def test_junior_delta_staged_high_risk_then_senior_approves(tmp_path, db):
    v1 = make_pdf(tmp_path / "v1.pdf", BASE_LINES)
    v2 = make_pdf(tmp_path / "v2.pdf", _delta_lines())
    orig = il.process_incoming_source("pdf", v1, "senior", "developer", db_path=db)

    r = il.process_incoming_source("pdf", v2, "junior", "developer", db_path=db)
    assert r["status"] == "pending_approval"
    assert r["status_code"] == 202
    assert il.DELTA_THRESHOLD <= r["similarity"] < il.EXACT_THRESHOLD
    assert r["risk_level"] == "high"
    assert "chunks" not in r
    # the diff quotes the existing document, so the uploader must not receive it
    assert "diff" not in r and "risk_reasons" not in r and "matched_doc_hash" not in r
    assert il.list_pending_updates(db)[0]["diff"].startswith("---")

    # resubmitting the same pending text is skipped, not double-staged
    again = il.process_incoming_source("pdf", v2, "junior", "developer", db_path=db)
    assert again["status"] == "duplicate_skipped"

    assert [p["id"] for p in il.list_pending_updates(db)] == [r["staged_id"]]
    assert il.review_staged_update(r["staged_id"], "junior", True, db_path=db)["status_code"] == 403

    ok = il.review_staged_update(r["staged_id"], "senior", True, reviewer="alice", db_path=db)
    assert ok["status"] == "version_update_ingested"
    assert ok["deprecated_doc_hash"] == orig["doc_hash"]
    assert ok["chunks"][0]["chunk_id"] == f"{r['doc_hash']}_0"
    assert il.list_pending_updates(db) == []

    with sqlite3.connect(db) as conn:
        statuses = dict(conn.execute("SELECT doc_hash, status FROM processed_docs"))
    assert statuses == {orig["doc_hash"]: "deprecated", r["doc_hash"]: "active"}


def test_senior_delta_auto_approved(tmp_path, db):
    v1 = make_pdf(tmp_path / "v1.pdf", BASE_LINES)
    v2 = make_pdf(tmp_path / "v2.pdf", _delta_lines())
    orig = il.process_incoming_source("pdf", v1, "senior", "developer", db_path=db)
    r = il.process_incoming_source("pdf", v2, "senior", "developer", db_path=db)
    assert r["status"] == "version_update_ingested"
    assert r["deprecated_doc_hash"] == orig["doc_hash"]
    assert r["chunks"]


def test_rejected_update_not_ingested(tmp_path, db):
    il.process_incoming_source("pdf", make_pdf(tmp_path / "v1.pdf", BASE_LINES), "senior", "dev", db_path=db)
    r = il.process_incoming_source("pdf", make_pdf(tmp_path / "v2.pdf", _delta_lines()), "junior", "dev", db_path=db)
    rej = il.review_staged_update(r["staged_id"], "senior", False, db_path=db)
    assert rej["status"] == "rejected"
    assert il.review_staged_update(r["staged_id"], "senior", True, db_path=db)["status_code"] == 409
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM processed_docs WHERE status='active'").fetchone()[0] == 1


def test_unrelated_document_is_new(tmp_path, db):
    il.process_incoming_source("pdf", make_pdf(tmp_path / "a.pdf", BASE_LINES), "junior", "dev", db_path=db)
    other = [f"Kubernetes cluster {i} autoscaling guidance for staging namespace workloads." for i in range(40)]
    r = il.process_incoming_source("pdf", make_pdf(tmp_path / "b.pdf", other), "junior", "dev", db_path=db)
    assert r["status"] == "new_document_ingested"
    assert r.get("similarity", 0) < il.DELTA_THRESHOLD


def test_pdf_table_extracted_once(tmp_path, db):
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    path = tmp_path / "t.pdf"
    doc = SimpleDocTemplate(str(path))
    t = Table([["Plan", "Rate"], ["Gold", "4.5%"], ["Silver", "3.1%"]])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    doc.build([Paragraph("Benefit rates below.", getSampleStyleSheet()["Normal"]), t])

    text, _ = il.extract_pdf(str(path))
    assert "Benefit rates below." in text
    assert "Gold | 4.5%" in text
    assert text.count("Gold") == 1


def test_bad_inputs(tmp_path, db):
    assert il.process_incoming_source("docx", "x", "senior", "dev", db_path=db)["status_code"] == 400
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    assert il.process_incoming_source("pdf", str(bad), "senior", "dev", db_path=db)["status_code"] == 422


def test_risk_terms_match_whole_words_only():
    benign = il.compute_diff("Our support team will report to the author on Friday.",
                             "Our support team will report to the author on Monday.", "a", "b")
    assert il.assess_risk(benign) == ("normal", [])

    risky = il.compute_diff("Rotate the admin password every 30 days.",
                            "Rotate the admin password every 90 days.", "a", "b")
    level, reasons = il.assess_risk(risky)
    assert level == "high"
    assert any("numeric" in r for r in reasons)
    assert any("admin" in r and "password" in r for r in reasons)

    enc = il.compute_diff("Data is stored.", "Data is encrypted and stored.", "a", "b")
    assert il.assess_risk(enc)[0] == "high"  # stem match: encrypted


def test_web_url_validation(db):
    for bad in ("file:///etc/passwd", "ftp://host/x", "http://169.254.169.254/latest/meta-data/"):
        r = il.process_incoming_source("web", bad, "senior", "employee", db_path=db)
        assert r["status_code"] == 400, bad


def test_uploader_and_department_recorded(tmp_path, db):
    v1 = make_pdf(tmp_path / "v1.pdf", BASE_LINES)
    v2 = make_pdf(tmp_path / "v2.pdf", _delta_lines())
    first = il.process_incoming_source("pdf", v1, "senior", "developer", db_path=db,
                                       uploader_id="u-senior", department="engineering")
    assert first["chunks"][0]["department"] == "engineering"

    staged = il.process_incoming_source("pdf", v2, "junior", "developer", db_path=db,
                                        uploader_id="u-junior", department="engineering")
    pending = il.list_pending_updates(db)[0]
    assert (pending["uploader_id"], pending["department"]) == ("u-junior", "engineering")

    ok = il.review_staged_update(staged["staged_id"], "senior", True, reviewer="u-senior", db_path=db)
    assert ok["chunks"][0]["department"] == "engineering"
    with sqlite3.connect(db) as conn:
        who = conn.execute("SELECT uploader_id FROM processed_docs WHERE status='active'").fetchone()[0]
    assert who == "u-junior"


def test_old_registry_is_migrated(tmp_path):
    db = str(tmp_path / "old.db")
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE processed_docs (doc_hash TEXT PRIMARY KEY, source TEXT NOT NULL, "
                     "source_type TEXT NOT NULL, min_role TEXT NOT NULL, uploader_role TEXT NOT NULL, "
                     "status TEXT NOT NULL, raw_text TEXT NOT NULL, minhash BLOB NOT NULL, "
                     "parent_hash TEXT, superseded_by TEXT, chunk_count INTEGER NOT NULL DEFAULT 0, "
                     "timestamp TEXT NOT NULL)")
    il.init_db(db)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(processed_docs)")}
    assert {"department", "uploader_id"} <= cols


def test_pending_update_leaves_current_version_live(tmp_path, db):
    v1 = make_pdf(tmp_path / "v1.pdf", BASE_LINES)
    v2 = make_pdf(tmp_path / "v2.pdf", _delta_lines())
    orig = il.process_incoming_source("pdf", v1, "senior", "developer", db_path=db)
    il.process_incoming_source("pdf", v2, "junior", "developer", db_path=db)
    with sqlite3.connect(db) as conn:
        rows = dict(conn.execute("SELECT doc_hash, status FROM processed_docs"))
    assert rows == {orig["doc_hash"]: "active"}   # nothing new live, nothing deprecated


def test_rejections_flag_account_and_source(tmp_path, db):
    il.process_incoming_source("pdf", make_pdf(tmp_path / "base.pdf", BASE_LINES), "senior", "dev",
                               db_path=db, department="engineering")
    last = None
    for n in range(il.FLAG_THRESHOLD):
        lines = list(BASE_LINES)
        lines[0] = f"Section 1: attempt {n} changes the admin port to {8000 + n}."
        r = il.process_incoming_source("pdf", make_pdf(tmp_path / f"bad{n}.pdf", lines), "junior",
                                       "dev", db_path=db, uploader_id="mallory",
                                       department="engineering")
        assert r["status"] == "pending_approval"
        last = il.review_staged_update(r["staged_id"], "senior", False, db_path=db, note="suspicious")
        assert last["audit_alert"] is True
    # three different files -> the account hits the threshold, no single source does
    assert {(f["kind"], f["flagged"]) for f in last["flags"]} == {("account", True), ("source", False)}
    flagged = {(f["kind"], f["key"]) for f in il.list_flags(db)}
    assert ("account", "mallory") in flagged
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT review_note FROM staged_updates LIMIT 1").fetchone()[0] == "suspicious"


def test_one_rejection_is_not_yet_flagged(tmp_path, db):
    il.process_incoming_source("pdf", make_pdf(tmp_path / "base.pdf", BASE_LINES), "senior", "dev", db_path=db)
    r = il.process_incoming_source("pdf", make_pdf(tmp_path / "v2.pdf", _delta_lines()), "junior", "dev",
                                   db_path=db, uploader_id="newbie")
    rej = il.review_staged_update(r["staged_id"], "senior", False, db_path=db)
    assert all(not f["flagged"] for f in rej["flags"])
    assert il.list_flags(db) == []


def test_review_queue_filters_by_department_and_orders_by_risk(tmp_path, db):
    il.process_incoming_source("pdf", make_pdf(tmp_path / "e.pdf", BASE_LINES), "senior", "dev",
                               db_path=db, department="engineering")
    other = [f"Kubernetes cluster {i} autoscaling guidance for staging namespace workloads." for i in range(40)]
    il.process_incoming_source("pdf", make_pdf(tmp_path / "s.pdf", other), "senior", "dev",
                               db_path=db, department="support")
    quiet = [l.replace("nightly", "daily").replace("reconciles", "checks") if i % 2 == 0 else l
             for i, l in enumerate(BASE_LINES)]
    il.process_incoming_source("pdf", make_pdf(tmp_path / "q.pdf", quiet), "junior", "dev",
                               db_path=db, department="engineering")
    il.process_incoming_source("pdf", make_pdf(tmp_path / "r.pdf", _delta_lines()), "junior", "dev",
                               db_path=db, department="engineering")
    queue = il.list_pending_updates(db, department="engineering")
    assert [q["risk_level"] for q in queue] == ["high", "normal"]
    assert il.list_pending_updates(db, department="support") == []
