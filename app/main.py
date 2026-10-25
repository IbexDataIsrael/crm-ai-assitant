"""FastAPI app: serves the chat page and the /chat endpoint."""

import json
from contextlib import asynccontextmanager
from pathlib import Path

from typing import Literal, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app import contact_chat, crm, crm_api, generation, knowledge, planner
from app.contact_chat import ContactDraftFields, ConversationState

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

    if route == "action" and plan["action"] == "add":
        reply = crm.ADD_CONTACT_POINTER
    elif route == "action":
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


# --- Login proxy for the chat page -------------------------------------------
# The page posts the form here (same origin); this forwards it once to the
# CRM's POST /api/auth/token at CRM_API_URL, without following redirects. The
# password is never logged, stored or sent anywhere else; the token goes back
# to the page, which keeps it in memory only. The body is parsed by hand so a
# malformed request is refused without ever echoing what was sent.

NO_STORE = {"Cache-Control": "no-store"}


def _login_refused(message: str) -> JSONResponse:
    return JSONResponse(status_code=422, content={"status": "invalid_request", "message": message},
                        headers=NO_STORE)


@app.post("/auth/login")
async def login(request: Request):
    """Log in to the CRM. Answers: logged_in (with access_token), choose_organization, or a failure."""
    try:
        body = await request.json()
    except ValueError:
        return _login_refused("Send a JSON object with username and password.")
    if not isinstance(body, dict) or set(body) - {"username", "password", "organization_id"}:
        return _login_refused("Send only username, password and, if asked, organization_id.")
    username, password = body.get("username"), body.get("password")
    organization_id = body.get("organization_id")
    if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
        return _login_refused("Enter a username (or email) and a password.")
    if organization_id is not None and (not isinstance(organization_id, int) or isinstance(organization_id, bool)):
        return _login_refused("organization_id must be a number.")
    result = await run_in_threadpool(crm_api.login, username.strip(), password, organization_id)
    return JSONResponse(status_code=result.http_status, content=result.body, headers=NO_STORE)


# --- The conversation: quick-menu intent + one turn ---------------------------

UPDATE_NOT_AVAILABLE = {
    "en": "Updating a contact isn't available yet - it's coming later. Nothing was changed.",
    "he": "עדכון איש קשר עדיין לא זמין - הוא יגיע בהמשך. לא שונה דבר.",
}
ASK_A_QUESTION = {
    "en": "What would you like to know? I'll search the CRM user guides.",
    "he": "מה תרצו לדעת? אחפש במדריכים למשתמש של ה-CRM.",
}


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Set by the menu choice, never read from free text.
    intent: Literal["create_contact", "update_contact", "ask_question"]
    # Empty when the menu choice was just made.
    message: str = ""
    # Held by the page between turns (create_contact only). Contains no token.
    state: Optional[ConversationState] = None
    language: Optional[Literal["en", "he"]] = None


@app.post("/assistant/turn")
def assistant_turn(
    request: TurnRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(crm_bearer),
):
    """One step of the conversation. The CRM token (create_contact) goes only to the bridge."""
    language = request.language or (request.state.language if request.state else "en")

    if request.intent == "update_contact":
        # Not built: never calls the bridge or the CRM.
        return {"intent": "update_contact", "outcome": "not_available",
                "reply": UPDATE_NOT_AVAILABLE[language], "show_menu": True}

    if request.intent == "ask_question":
        if not request.message.strip():
            return {"intent": "ask_question", "outcome": "ask", "reply": ASK_A_QUESTION[language]}
        return {"intent": "ask_question", "outcome": "answered", **chat(ChatRequest(message=request.message))}

    token = _bearer_token(credentials)
    if token is None:
        return JSONResponse(status_code=401, headers={"WWW-Authenticate": "Bearer"}, content={
            "intent": "create_contact", "outcome": "unauthorized", "logout": True,
            "reply": contact_chat.say(language, "unauthorized"),
        })
    if not request.message.strip():
        return contact_chat.start(language)
    state = request.state or ConversationState(language=language)
    return contact_chat.handle_turn(token, request.message, state)
