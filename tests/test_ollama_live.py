"""Live check against the local Ollama server (skipped if it isn't running or models are missing)."""
import uuid

import pytest
import requests

from app import guardrails
from app.llm import INSUFFICIENT, OllamaLLM, build_messages, cited_sources
from app.vectorstore import OllamaEmbedder, VectorStore


def _ready():
    try:
        tags = requests.get("http://127.0.0.1:11434/api/tags", timeout=2).json()["models"]
        names = {m["name"] for m in tags}
        return any(n.startswith("llama3") for n in names) and any(n.startswith("nomic-embed-text") for n in names)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="Ollama with llama3 + nomic-embed-text not available")


def chunk(doc, text, min_role="public", source="kb"):
    return {"chunk_id": f"{doc}_0", "doc_hash": doc, "source": source, "department": "support",
            "min_role": min_role, "status": "active", "timestamp": "t", "text": text}


@pytest.fixture(scope="module")
def vs():
    store = VectorStore(OllamaEmbedder(), path=None, collection=f"live_{uuid.uuid4().hex}")
    store.add_chunks([
        chunk("refunds", "Refund policy: customers may request a refund within 30 days of purchase. "
                         "Support reps approve refunds under $100; larger refunds need a team lead.", "public"),
        chunk("outage", "Payments outage runbook: if the payments-api returns HTTP 503, restart the "
                        "payments-worker deployment, then check the queue depth in the ops dashboard.", "developer"),
        chunk("keys", "The production signing key rotation runbook: the HSM admin credentials are stored in vault path "
                      "secret/hsm-admin. Rotate every 90 days.", "senior_eng"),
    ])
    return store


def test_real_embeddings_rank_the_right_document(vs):
    assert vs.query("how long do customers have to ask for a refund", 100, k=1)[0].doc_hash == "refunds"
    assert vs.query("payments api returning 503 what do I do", 100, k=1)[0].doc_hash == "outage"


def test_real_embeddings_respect_clearance(vs):
    hits = vs.query("where are the HSM admin credentials for key rotation", clearance=40, k=5)
    assert all(h.doc_hash != "keys" for h in hits)


def test_real_llm_answers_from_context_with_citations(vs):
    hits = vs.query("how long do customers have to ask for a refund", 20, k=3)
    done = OllamaLLM().complete(build_messages("support", "How long do customers have to request a refund?", hits))
    assert "30" in done.text and cited_sources(done.text, len(hits)), done.text
    assert done.ttft_ms and done.total_ms


def test_real_llm_refuses_without_support_in_context(vs):
    hits = vs.query("what is the capital of france", 20, k=3)
    done = OllamaLLM().complete(build_messages("developer", "What is the capital of France?", hits))
    assert INSUFFICIENT.lower()[:40] in done.text.lower() or "paris" not in done.text.lower(), done.text
