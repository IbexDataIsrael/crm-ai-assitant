"""FastAPI app: serves the chat page, /health, the login proxy, and the login-protected chat routes."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from typing import Literal, Optional

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app import apartment_chat, contact_chat, crm, crm_api, generation, knowledge, language as user_language
from app import planner, rate_limit
from app.apartment_chat import ApartmentEditState
from app.contact_chat import ContactDraftFields, ConversationState
from app.language import LanguageState

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the embedding model and index knowledge/ once, before serving requests.
    knowledge.get_store()
    yield


def api_docs_enabled() -> bool:
    """/docs, /redoc and /openapi.json describe the whole API, so they are OFF
    unless API_DOCS=on (docker-compose.yml sets it for local development; the
    server profile does not pass it)."""
    return os.environ.get("API_DOCS", "").strip().lower() == "on"


_docs = api_docs_enabled()
app = FastAPI(
    title="CRM Assistant (demo skeleton)",
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)


class ChatRequest(BaseModel):
    message: str
    # The user's language (docs/instructions19.md); replies and the answer follow it.
    language: Optional[Literal["en", "he"]] = None


# --- Login required (docs/instructions17.md) ----------------------------------
# /chat and /assistant/turn do AI work or reach the CRM, so each request must
# carry the user's CRM token, and the CRM must confirm it (GET /api/auth/me)
# before anything else happens. Open on purpose: GET / (the page), GET /health
# and POST /auth/login (rate limited). Before any of that, the per-client rate
# limit (app/rate_limit.py).

NO_STORE = {"Cache-Control": "no-store"}
crm_bearer = HTTPBearer(auto_error=False, description="The user's own CRM access token (JWT).")

GATE_REPLIES = {
    "unauthorized": {
        "en": "Please log in. Your CRM login is missing or no longer valid.",
        "he": "אנא התחברו. ההתחברות ל-CRM חסרה או שאינה בתוקף עוד.",
    },
    "forbidden": {
        "en": "The CRM refused this request: your account doesn't have permission.",
        "he": "ה-CRM דחה את הבקשה: לחשבון שלכם אין הרשאה.",
    },
    "unavailable": {
        "en": "The service is temporarily unavailable. Please try again in a moment. You are still logged in.",
        "he": "השירות אינו זמין כרגע. נסו שוב בעוד רגע. אתם עדיין מחוברים.",
    },
    "rate_limited": {
        "en": "Too many requests. Wait a little and try again.",
        "he": "יותר מדי בקשות. המתינו מעט ונסו שוב.",
    },
    "bad_client_address": {
        "en": "The request could not be accepted.",
        "he": "לא ניתן היה לקבל את הבקשה.",
    },
}
GATE_HTTP_STATUS = {"unauthorized": 401, "forbidden": 403, "unavailable": 503,
                    "rate_limited": 429, "bad_client_address": 400}


def _gate_refusal(status: str, language: str, retry_after: int = 0, **extra) -> JSONResponse:
    headers = dict(NO_STORE)
    if status == "unauthorized":
        headers["WWW-Authenticate"] = "Bearer"
    if retry_after:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=GATE_HTTP_STATUS[status],
        headers=headers,
        content={"status": status, "outcome": status, "reply": GATE_REPLIES[status][language],
                 # Only a rejected login logs the page out; an outage, a
                 # permission refusal or a rate limit keeps the session.
                 "logout": status == "unauthorized", **extra},
    )


def _rate_limited(http_request: Request, group: str, language: str = "en") -> Optional[JSONResponse]:
    refused = rate_limit.check(http_request, group)
    if refused is None:
        return None
    status, retry_after = refused
    return _gate_refusal(status, language, retry_after)


def _require_login(http_request: Request, credentials: Optional[HTTPAuthorizationCredentials],
                   language: str = "en", **extra) -> tuple[Optional[str], Optional[JSONResponse]]:
    """(token, None) when the CRM confirms the login; (None, refusal) otherwise.

    Order: rate limit, then the token's presence, then the CRM check - so a
    refused request costs no CRM call and no AI work.
    """
    refusal = _rate_limited(http_request, "chat", language)
    if refusal is not None:
        return None, refusal
    token = _bearer_token(credentials)
    if token is None:
        return None, _gate_refusal("unauthorized", language, **extra)
    verdict = crm_api.verify_login(token)
    if verdict.http_status != 200:
        return None, _gate_refusal(verdict.body["status"], language, **extra)
    return token, None


@app.get("/")
def index():
    # no-cache: the browser must revalidate on every load, so it never keeps
    # running an old page against a newer server (an old page that sent the
    # token only for create_contact was refused on "Ask a question").
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health():
    """"ok" or "not-ok", nothing else: no data, versions or error text.

    Serving at all means startup finished (model loaded, guides synced). This
    also checks that the database answers and still holds the guide rows. It
    never calls an AI provider or the CRM.
    """
    try:
        store = knowledge.get_store()
        with psycopg.connect(store.dsn, connect_timeout=3) as conn:
            healthy = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM chunks WHERE collection = %s)", (store.collection,)
            ).fetchone()[0]
    except Exception:
        healthy = False
    if healthy:
        return JSONResponse(status_code=200, content={"status": "ok"}, headers=NO_STORE)
    return JSONResponse(status_code=503, content={"status": "not-ok"}, headers=NO_STORE)


@app.post("/chat")
def chat(
    request: ChatRequest,
    http_request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(crm_bearer),
):
    """A guide question (or a demo action). Login required since instructions17:
    it was open before; the assistant will be public, and each answer can cost
    an AI provider call."""
    language = request.language or "en"
    _, refusal = _require_login(http_request, credentials, language)
    if refusal is not None:
        return refusal
    return answer_message(request.message, language)


PLACEHOLDER_REPLY = {
    "en": "Placeholder function that would run: {function}()\nDemo result:\n{result}",
    "he": "פונקציית הדגמה שהייתה רצה: {function}()\nתוצאת הדגמה:\n{result}",
}
CLARIFY = {
    "en": "What would you like to do? You can ask me to find, add or update a contact, or ask how to do something.",
    "he": "מה תרצו לעשות? אפשר לבקש ממני למצוא, להוסיף או לעדכן איש קשר, או לשאול איך עושים משהו.",
}


def answer_message(message: str, language: str = "en") -> dict:
    """The /chat work, for a request that already passed the login check."""
    plan = planner.plan(message)
    route = plan["route"]
    passages = []
    generation_info = None

    if route == "action" and plan["action"] == "add":
        reply = crm.ADD_CONTACT_POINTER[language]
    elif route == "action":
        function_name, result = crm.pick_placeholder(message, plan["action"], language)
        reply = PLACEHOLDER_REPLY[language].format(
            function=function_name, result=json.dumps(result, indent=2, ensure_ascii=False))
    elif route == "knowledge":
        # Both guides are searched whatever the language; the answer is written in the user's language.
        passages = knowledge.search(message)
        generated = generation.generate_answer(message, passages, language=language)
        generation_info = generated.as_dict()
        reply = knowledge.format_reply(message, passages, generated, language)
    else:  # clarify: empty message
        reply = CLARIFY[language]

    return {
        "reply": reply,
        "route": route,
        "route_source": plan["route_source"],
        "passages": passages,
        "generation": generation_info,
        "language": language,
    }


# --- CRM contact-draft bridge (structured fields, no AI) -------------------
# No AI work here: each call needs the user's CRM bearer token, which is
# forwarded to CRM_API_URL only, and the CRM itself checks it on that same call
# and decides who the user is, their organization and what they may do. A
# request without a token gets 401 and reaches nothing. See app/crm_api.py.

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

def _login_refused(message: str) -> JSONResponse:
    return JSONResponse(status_code=422, content={"status": "invalid_request", "message": message},
                        headers=NO_STORE)


@app.post("/auth/login")
async def login(request: Request):
    """Log in to the CRM. Answers: logged_in (with access_token), choose_organization, or a failure.

    Rate limited per client (app/rate_limit.py) before anything is read or sent:
    the CRM records login attempts but does not slow them down.
    """
    refusal = _rate_limited(request, "login")
    if refusal is not None:
        return refusal
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


@app.post("/auth/session")
async def resume_session(
    http_request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(crm_bearer),
):
    """A page reopened with a remembered login: is the token still valid, and what is the
    user's language now? Same rate limit and CRM login check as the chat; the language is
    read from the CRM again (it may have changed elsewhere). Answers logged_in + language,
    or the gate's refusal (401 -> the page forgets the token)."""
    token, refusal = await run_in_threadpool(_require_login, http_request, credentials, "en")
    if refusal is not None:
        return refusal
    language = await run_in_threadpool(crm_api.read_language, token)
    return JSONResponse(status_code=200, headers=NO_STORE,
                        content={"status": "logged_in", "language": language})


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
    intent: Literal["create_contact", "update_contact", "edit_apartment", "ask_question"]
    # Empty when the menu choice was just made.
    message: str = ""
    # Held by the page between turns (create_contact only). Contains no token.
    state: Optional[ConversationState] = None
    # Held by the page between turns (edit_apartment only). Contains no token.
    apartment_state: Optional[ApartmentEditState] = None
    language: Optional[Literal["en", "he"]] = None
    # The session language and its detection state (app/language.py). Held by the page; no token.
    language_state: Optional[LanguageState] = None


@app.post("/assistant/turn")
def assistant_turn(
    request: TurnRequest,
    http_request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(crm_bearer),
):
    """One step of the conversation. Every intent needs a login the CRM confirms
    first; the token then goes only to the CRM (login check and bridge)."""
    held = request.apartment_state or request.state
    session = request.language_state or LanguageState(
        language=request.language or (held.language if held else "en"))
    before = session.language
    token, refusal = _require_login(http_request, credentials, session.language, intent=request.intent)
    if refusal is not None:
        return refusal
    answer = _turn(token, request, session)
    # After the work: save a language change (or retry a failed save once). Never blocks the answer.
    notice = user_language.settle(token, session, changed=session.language != before)
    answer["language_state"] = session.model_dump()
    answer["language_notice"] = user_language.notice(notice, session.language)
    return answer


def _turn(token: str, request: TurnRequest, session: LanguageState) -> dict:
    """One turn in the session language. Only typed free text may change it (app/language.py):
    an Ask question, or a free message in the contact flow (minus the field values read from it).
    Menu choices (an empty message), button taps and every apartment-flow message never do."""
    message = request.message

    if request.intent == "update_contact":
        # Not built: never calls the bridge or the CRM.
        return {"intent": "update_contact", "outcome": "not_available",
                "reply": UPDATE_NOT_AVAILABLE[session.language], "show_menu": True}

    if request.intent == "ask_question":
        if not message.strip():
            return {"intent": "ask_question", "outcome": "ask", "reply": ASK_A_QUESTION[session.language]}
        user_language.observe(session, message)
        return {"intent": "ask_question", "outcome": "answered", **answer_message(message, session.language)}

    if request.intent == "edit_apartment":
        # sheepsidebay_crm docs/fixes115.md: behind the same login check above. Every message
        # here is an address, a pick, a field value or a confirm word: none changes the language.
        if not message.strip():
            return apartment_chat.start(session.language)
        state = request.apartment_state or ApartmentEditState(language=session.language)
        state.language = session.language
        return apartment_chat.handle_turn(token, message, state)

    # create_contact.
    if not message.strip():
        return contact_chat.start(session.language)
    state = request.state or ConversationState(language=session.language)
    state.language = session.language

    def observe(text: str, values) -> str:
        return user_language.observe(session, text, values).language

    return contact_chat.handle_turn(token, message, state, observe=observe)


# --- The user's language: the toggle's save (docs/instructions19.md) -------------

INVALID_LANGUAGE = {
    "en": "The language must be Hebrew or English.",
    "he": "השפה צריכה להיות עברית או אנגלית.",
}


@app.post("/assistant/language")
async def save_language(
    http_request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(crm_bearer),
):
    """Save the user's language in the CRM (preference "locale"). Body {"language": "he" | "en"}.

    Same rate limit group and login check as the chat; any other value -> 400, nothing called.
    A failed save is an error here, but the page keeps the language for the session and the
    next turn tries the save once more (app/language.py).
    """
    refusal = _rate_limited(http_request, "chat")
    if refusal is not None:
        return refusal
    token = _bearer_token(credentials)
    if token is None:
        return _gate_refusal("unauthorized", "en")
    try:
        body = await http_request.json()
    except ValueError:
        body = None
    wanted = body.get("language") if isinstance(body, dict) and set(body) == {"language"} else None
    if wanted not in user_language.LANGUAGES:
        return JSONResponse(status_code=400, headers=NO_STORE, content={
            "status": "invalid_language", "reply": INVALID_LANGUAGE["en"] + " / " + INVALID_LANGUAGE["he"]})
    verdict = await run_in_threadpool(crm_api.verify_login, token)
    if verdict.http_status != 200:
        return _gate_refusal(verdict.body["status"], wanted)
    result = await run_in_threadpool(user_language.save, token, wanted)
    if result.http_status == 200:
        return JSONResponse(status_code=200, headers=NO_STORE, content={
            "status": "saved", "language": wanted, "reply": user_language.notice("saved", wanted)})
    if result.body.get("status") == "unauthorized":
        return _gate_refusal("unauthorized", wanted)
    return JSONResponse(status_code=502, headers=NO_STORE, content={
        "status": "not_saved", "language": wanted, "reply": user_language.notice("save_failed", wanted)})
