"""Inventory assistant endpoint (Claude with permission-checked tools, deterministic fallback) and its proposals."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import Principal, require
from app.core import rate_limit
from app.core.security import Perm
from app.db.session import get_db
from app.services import assistant_actions, assistant_service, audit

router = APIRouter(tags=["assistant"])


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=10)


class ToolCall(BaseModel):
    tool: str
    ok: bool


class ProposalOut(BaseModel):
    id: str
    kind: str
    summary: str
    status: str
    result: dict[str, Any] | None = None
    created_at: str
    expires_at: str
    decided_at: str | None = None


class ChatOut(BaseModel):
    answer: str
    mode: Literal["claude", "fallback"]
    tool_calls: list[ToolCall]
    notice: str | None = None
    proposals: list[ProposalOut] = Field(default_factory=list)


@router.post("/chat", response_model=ChatOut)
def chat(body: ChatIn, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    rate_limit.hit(f"chat:{p.user_id}", 20, 60)
    # History must alternate and start with the user, as the Messages API requires.
    history = [t.model_dump() for t in body.history]
    while history and history[0]["role"] != "user":
        history.pop(0)
    if history and history[-1]["role"] == "user":
        history.pop()
    merged: list[dict] = []
    for t in history:
        if merged and merged[-1]["role"] == t["role"]:
            merged[-1]["content"] += "\n\n" + t["content"]
        else:
            merged.append(dict(t))
    history = merged
    res = assistant_service.answer(db, p, body.message.strip(), history)
    # Audit what was accessed, not what was asked: questions can contain personal data.
    audit.record_for(
        db,
        p,
        "assistant.chat",
        resource_type="assistant",
        details={"mode": res["mode"], "tools": [c["tool"] for c in res["tool_calls"]]},
    )
    db.commit()
    return res


# --- proposals (ADR-046): the assistant proposes, the person decides ----------------------------------------------


@router.get("/assistant/proposals", response_model=list[ProposalOut])
def pending_proposals(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> list[dict]:
    """The caller's own unconfirmed proposals."""
    return [assistant_actions.out(r) for r in assistant_actions.list_pending(db, p)]


@router.post("/assistant/proposals/{proposal_id}/apply", response_model=ProposalOut)
def apply_proposal(
    proposal_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> dict:
    """Confirm a proposal. The role needed for the change itself is checked inside, at confirmation time."""
    row = assistant_actions.apply(db, p, proposal_id)
    db.commit()
    return assistant_actions.out(row)


@router.post("/assistant/proposals/{proposal_id}/dismiss", response_model=ProposalOut)
def dismiss_proposal(
    proposal_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> dict:
    row = assistant_actions.dismiss(db, p, proposal_id)
    db.commit()
    return assistant_actions.out(row)
