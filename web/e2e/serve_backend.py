"""Starts a throwaway Alexandria backend for the browser tests: fresh database, a small seeded
organisation, the real local model and embeddings. Port 8123."""
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
state = pathlib.Path(os.environ.get("E2E_STATE", ROOT / ".e2e-state"))
shutil.rmtree(state, ignore_errors=True)
state.mkdir()
os.environ.update(
    ALEXANDRIA_DB=str(state / "app.db"), ALEXANDRIA_SECRETS=str(state / "secrets"),
    ALEXANDRIA_REGISTRY=str(state / "registry.db"), ALEXANDRIA_CHROMA=str(state / "chroma"),
    ALEXANDRIA_CORS_ORIGINS="http://localhost:3100,http://127.0.0.1:3100", ALEXANDRIA_CHAIN="off",
)

import uvicorn  # noqa: E402
from app import security  # noqa: E402
from app.config import Settings  # noqa: E402
from app.server import build_app  # noqa: E402
from app.store import Store  # noqa: E402

PW = "correct horse battery"
settings = Settings()
store = Store(settings.db_path, settings.cipher)
mk = lambda name, role, mgr=None: store.create_user(name, security.hash_password(PW), role, manager_id=mgr["id"] if mgr else None)
admin = mk("admin", "security_admin")
lead = mk("lead", "senior_eng", admin)
lead2 = mk("lead2", "senior_eng", admin)
store.update_user(lead2["id"], timezone="Europe/London")
mk("dev", "developer", lead)
slead = mk("slead", "support_lead", admin)
mk("rep", "support_rep", slead)
mk("exec", "executive", admin)
mk("qa", "developer", lead)

uvicorn.run(build_app(), host="127.0.0.1", port=8123, log_level="warning")
