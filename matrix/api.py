"""The web door n8n knocks on. POST /inbound = new message, POST /approve = owner's yes/no."""
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import config, runner
from .graph import build_graph

app = FastAPI(title="Cross Coat Matrix", version="0.1.0")
graph = build_graph()


class Inbound(BaseModel):
    text: str
    source: str = "api"          # telegram / slack / api
    chat_id: str = ""
    user_id: str = ""


class Approve(BaseModel):
    thread_id: str
    decision: str                # "yes" or "no"
    approver_id: str = ""        # Telegram user id of whoever pressed the button


def check_key(key: str | None):
    if config.MATRIX_API_KEY and key != config.MATRIX_API_KEY:
        raise HTTPException(status_code=401, detail="Wrong or missing X-Matrix-Key header")


@app.get("/health")
def health():
    return {"ok": True, "llm_provider": config.LLM_PROVIDER}


@app.post("/inbound")
def inbound(body: Inbound, x_matrix_key: str | None = Header(default=None)):
    check_key(x_matrix_key)
    return runner.start(graph, body.text, source=body.source, chat_id=body.chat_id)


@app.post("/approve")
def approve(body: Approve, x_matrix_key: str | None = Header(default=None)):
    check_key(x_matrix_key)
    if config.OWNER_TELEGRAM_ID and body.approver_id != config.OWNER_TELEGRAM_ID:
        raise HTTPException(status_code=403, detail="Only the owner can approve")
    decision = body.decision.strip().lower()
    if decision not in {"yes", "no", "y", "n", "true", "false"}:
        raise HTTPException(status_code=400, detail="decision must be yes or no")
    try:
        return runner.resume(graph, body.thread_id, decision in {"yes", "y", "true"},
                             body.approver_id or "owner")
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
