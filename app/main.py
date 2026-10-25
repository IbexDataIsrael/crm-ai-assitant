"""FastAPI app: serves the chat page and the /chat endpoint."""

import json
from contextlib import asynccontextmanager
from pathlib import Path

from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app import crm, crm_api, generation, knowledge, planner

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the embedding model and index knowledge/ once, before serving requests.
    knowledge.get_store()
    yield


app = FastAPI(title="CRM Assistant (demo skeleton)", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/chat")
def chat(request: ChatRequest):
    plan = planner.plan(request.message)
    route = plan["route"]
    passages = []
    generation_info = None

    if route == "action":
        function_name, result = crm.pick_placeholder(request.message, plan["action"])
        reply = (
            f"Placeholder function that would run: {function_name}()\n"
            f"Demo result:\n{json.dumps(result, indent=2)}"
        )
    elif route == "knowledge":
        passages = knowledge.search(request.message)
        generated = generation.generate_answer(request.message, passages)
        generation_info = generated.as_dict()
        reply = knowledge.format_reply(request.message, passages, generated)
    else:  # clarify: empty message
        reply = (
            "What would you like to do? You can ask me to find, add or update "
            "a contact, or ask how to do something."
        )

    return {
        "reply": reply,
        "route": route,
        "route_source": plan["route_source"],
        "passages": passages,
        "generation": generation_info,
    }


# --- CRM contact-draft bridge (structured fields, no AI) -------------------
# LOCALHOST ONLY: this service has no authentication of its own. The caller's
# CRM bearer token is forwarded to CRM_API_URL only; the CRM decides who the
# user is, their organization and what they may do. See app/crm_api.py.

crm_bearer = HTTPBearer(auto_error=False, description="The user's own CRM access token (JWT).")


class ContactDraftFields(BaseModel):
    """Same fields as the CRM's ContactDraftFields. Extra keys (organization_id, user_id ...) are refused."""

    model_config = ConfigDict(extra="forbid")

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None


class ContactDraftProposalRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"fields": {"first_name": "Dana", "last_name": "Levi", "phone": "0521234567"}}]},
    )

    fields: ContactDraftFields


class ContactDraftConfirmRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"payload_hash": "3f1c...e9 (from the proposal)"}]},
    )

    payload_hash: str = Field(min_length=1)
    # Optional: the details the user saw; the CRM refuses them if they differ from the proposal.
    fields: Optional[ContactDraftFields] = None


def _bearer_token(credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    token = credentials.credentials.strip()
    return token or None


def _missing_token() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"status": "unauthorized", "message": "Send the user's CRM token as 'Authorization: Bearer <token>'."},
        headers={"WWW-Authenticate": "Bearer"},
    )


@app.post("/crm/contact-drafts/proposals")
def propose_contact_draft(
    request: ContactDraftProposalRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(crm_bearer),
):
    """Ask the CRM for a contact-draft proposal. Nothing is saved.

    Answers: needs_information (200), proposal (201), invalid (422), or a failure.
    """
    token = _bearer_token(credentials)
    if token is None:
        return _missing_token()
    result = crm_api.request_proposal(token, request.fields.model_dump())
    return JSONResponse(status_code=result.http_status, content=result.body)


@app.post("/crm/contact-drafts/proposals/{proposal_id}/confirm")
def confirm_contact_draft(
    proposal_id: str,
    request: ContactDraftConfirmRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(crm_bearer),
):
    """Explicitly confirm one proposal; the CRM saves the draft at most once.

    Answers: created (201, or 200 with replayed=true on a retry), uncertain
    (504/502, confirm the same proposal again), or a failure.
    """
    token = _bearer_token(credentials)
    if token is None:
        return _missing_token()
    fields = request.fields.model_dump() if request.fields is not None else None
    result = crm_api.confirm_proposal(token, proposal_id, request.payload_hash, fields)
    return JSONResponse(status_code=result.http_status, content=result.body)
