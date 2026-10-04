from types import SimpleNamespace

from app.claude_llm import DECLINED, FALLBACK_BETA, ClaudeLLM


class FakeStream:
    def __init__(self, pieces, stop="end_turn", usage=(120, 30)):
        self.pieces, self.stop, self.usage = pieces, stop, usage

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        return iter(self.pieces)

    def get_final_message(self):
        return SimpleNamespace(stop_reason=self.stop, usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1]))


class FakeClient:
    def __init__(self, stream):
        self.calls = []
        outer = self

        class _Messages:
            def stream(self, **kw):
                outer.calls.append(kw)
                return stream

        self.beta = SimpleNamespace(messages=_Messages())


MSGS = [{"role": "system", "content": "persona"}, {"role": "system", "content": "rules"}, {"role": "user", "content": "q?"}]


def test_streams_text_and_reports_usage():
    client = FakeClient(FakeStream(["Hel", "lo [1]"]))
    llm = ClaudeLLM(client=client)
    s = llm.stream(MSGS)
    assert "".join(s) == "Hello [1]" and s.usage == {"prompt_tokens": 120, "completion_tokens": 30}
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["system"] == "persona\n\nrules"
    assert kw["messages"] == [{"role": "user", "content": "q?"}]
    assert kw["betas"] == [FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert kw["output_config"] == {"effort": "low"} and "thinking" not in kw and "temperature" not in kw


def test_complete_returns_a_completion_with_timings():
    llm = ClaudeLLM(client=FakeClient(FakeStream(["Answer [1]."])))
    done = llm.complete(MSGS)
    assert done.text == "Answer [1]." and done.prompt_tokens == 120 and done.ttft_ms is not None


def test_a_full_refusal_yields_a_neutral_sentence_not_an_empty_bubble():
    llm = ClaudeLLM(client=FakeClient(FakeStream([], stop="refusal")))
    assert llm.complete(MSGS).text == DECLINED


def test_model_and_effort_are_configurable(monkeypatch):
    monkeypatch.setenv("ALEXANDRIA_CLAUDE_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("ALEXANDRIA_CLAUDE_EFFORT", "medium")
    client = FakeClient(FakeStream(["x"]))
    "".join(ClaudeLLM(client=client).stream(MSGS))
    assert client.calls[0]["model"] == "claude-sonnet-5-5" and client.calls[0]["output_config"] == {"effort": "medium"}


def test_it_plugs_into_the_answer_pipeline(tmp_path):
    import uuid
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    from app.vectorstore import HashingEmbedder, VectorStore
    from tests.test_api import H, enroll, make_user
    vectors = VectorStore(HashingEmbedder(), path=None, collection=f"t_{uuid.uuid4().hex}")
    vectors.add_chunks([{"chunk_id": "d_0", "doc_hash": "d", "source": "kb", "department": "support", "min_role": "public",
                         "status": "active", "timestamp": "t", "text": "Refunds over $100 need a team lead."}])
    llm = ClaudeLLM(client=FakeClient(FakeStream(["Ask a team lead [1]."])))
    app = create_app(Settings(db_path=str(tmp_path / "a.db"), secrets_dir=tmp_path / "s"), vectors=vectors, llm=llm)
    c = TestClient(app)
    make_user(app.state.store, "rep", "support_rep")
    tok = enroll(c, "rep")[0]
    body = c.post("/ask", headers=H(tok), json={"question": "refund over 100"}).json()
    assert body["answer"] == "Ask a team lead [1]." and body["grounded"]
