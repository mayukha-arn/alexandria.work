"""Production wiring: the real local model, the vector store and (optionally) the blockchain.

    .venv/bin/uvicorn app.server:build_app --factory --port 8000

Environment (all optional):
    OLLAMA_URL                 default http://127.0.0.1:11434
    ALEXANDRIA_LLM_PROVIDER    "ollama" (default, local) or "claude" (hosted; needs ANTHROPIC_API_KEY)
    ALEXANDRIA_MODEL           default llama3:8b          (Ollama model for answers and drafts)
    ALEXANDRIA_CLAUDE_MODEL    default claude-opus-5-5;  ALEXANDRIA_CLAUDE_EFFORT default low
    ALEXANDRIA_EMBED_MODEL     default nomic-embed-text
    ALEXANDRIA_CHAIN           "solana" to anchor the audit trail (default: off, events just queue)
    SOLANA_RPC_URLS            comma-separated, e.g. http://127.0.0.1:8899 for a local validator
    ALEXANDRIA_CORS_ORIGINS    comma-separated web origins (default: localhost:3000)
    ALEXANDRIA_CHROMA          where the vector index lives (default: ./chroma_db)
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from .chain import chain_from_env
from .config import ROOT, Settings
from .llm import OllamaLLM
from .main import create_app
from .vectorstore import OllamaEmbedder, VectorStore


def build_app() -> FastAPI:
    ollama = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    vectors = VectorStore(OllamaEmbedder(ollama, os.getenv("ALEXANDRIA_EMBED_MODEL", "nomic-embed-text")),
                          path=os.getenv("ALEXANDRIA_CHROMA", str(ROOT / "chroma_db")))
    provider = os.getenv("ALEXANDRIA_LLM_PROVIDER", "ollama").lower()
    if provider == "claude":
        from .claude_llm import ClaudeLLM
        llm = ClaudeLLM()
    else:
        llm = OllamaLLM(ollama, os.getenv("ALEXANDRIA_MODEL", "llama3:8b"))
    chain = chain_from_env() if os.getenv("ALEXANDRIA_CHAIN", "off").lower() == "solana" else None
    return create_app(Settings(), chain=chain, vectors=vectors, llm=llm)
