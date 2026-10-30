"""Bridge to the CRM's contact-draft API (POST /api/assistant/proposals and
POST /api/assistant/proposals/{id}/confirm, in sheepsidebay_crm's
backend/app/routers/assistant.py), plus the login proxy for the chat page
(POST /api/auth/token, backend/app/routers/auth.py).

Structured fields only - no AI, no free-text extraction. The CRM owns
permissions, organization scoping, validation, saving and idempotency; this
module only forwards the request and maps the CRM's answer to a result the
client can act on.

The CRM address comes from CRM_API_URL (no default, no production fallback).
The caller's CRM bearer token is forwarded per request to that one origin
only: redirects are never followed, and the token is never logged, stored,
returned or sent anywhere else (no AI provider sees it).

Login enforcement (docs/instructions17.md): the chat routes check every
request's token with the CRM's GET /api/auth/me (verify_login) before any AI
work. This service still holds no secret of its own.
"""

import logging
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

ACTION_CREATE_CONTACT_DRAFT = "create_contact_draft"
PROPOSALS_PATH = "/api/assistant/proposals"
# Seconds. A timeout after sending is an uncertain result, never "not saved".
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
# The CRM's proposal ids are uuid4 strings; anything else never reaches the CRM.
PROPOSAL_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# The CRM's 409 messages (routers/assistant.py), used to tell the two cases apart.
CRM_MISMATCH_DETAIL = "The confirmed details do not match this proposal"
MAX_MESSAGE_LENGTH = 200
LOGIN_PATH = "/api/auth/token"
# The CRM's "who am I" (backend/app/routers/auth.py me()); used to check a login.
ME_PATH = "/api/auth/me"
# sheepsidebay_crm backend/app/routers/preferences.py (fixes116): per-user preferences;
# the assistant only reads the "locale" key ("he" or "en") and never changes it (docs/instructions20.md).
PREFERENCES_PATH = "/api/preferences"
LOCALES = ("he", "en")
UNAVAILABLE_MESSAGE = "The service is temporarily unavailable. Please try again in a moment."
# The CRM's 403 login codes (auth.py auth_error) -> what the chat page shows.
LOGIN_REFUSALS = {
    "auth.error.not_verified": "not_verified",
    "auth.error.org_pending": "org_pending",
    "auth.error.org_disabled": "org_disabled",
}

logger = logging.getLogger(__name__)


class CRMConfigError(Exception):
    """CRM_API_URL is missing or not acceptable. The message never contains the value."""


@dataclass
class BridgeResult:
    """What the AI service returns: an HTTP status and a JSON body."""

    http_status: int
    body: dict


def crm_base_url() -> str:
    """CRM_API_URL, checked and without trailing slashes ("http://host:8001/" -> "http://host:8001")."""
    raw = os.environ.get("CRM_API_URL", "").strip()
    if not raw:
        raise CRMConfigError("CRM_API_URL is not set")
    try:
        parts = urlsplit(raw)
        parts.port  # raises ValueError on a bad port
    except ValueError:
        raise CRMConfigError("CRM_API_URL is not a valid address") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise CRMConfigError("CRM_API_URL must be an http:// or https:// address with a host")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise CRMConfigError("CRM_API_URL must not contain credentials")
    if parts.query or parts.fragment:
        raise CRMConfigError("CRM_API_URL must not contain a query or fragment")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def crm_web_url() -> str | None:
    """CRM_WEB_URL: the CRM web app's address, for "open in the CRM" links (docs/instructions20.md).

    Optional and separate from CRM_API_URL. Missing or not a plain http(s) address -> None,
    and no link is shown. Only ever put into links; never called.
    """
    raw = os.environ.get("CRM_WEB_URL", "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        parts.port
    except ValueError:
        return None
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None
            or parts.password is not None or "@" in parts.netloc or parts.query or parts.fragment):
        return None
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def contact_page_url(contact_id: int) -> str | None:
    """The person's full page in the CRM web app (/dashboard/contacts/{id}), or None."""
    base = crm_web_url()
    return f"{base}/dashboard/contacts/{contact_id}" if base and _is_id(contact_id) else None


def make_http_client(base_url: str) -> httpx.Client:
    """Replaced in tests with a client on a mock transport."""
    return httpx.Client(base_url=base_url, timeout=TIMEOUT, follow_redirects=False)


def is_valid_proposal_id(proposal_id: str) -> bool:
    return bool(PROPOSAL_ID_PATTERN.match(proposal_id))


def _short(text) -> str:
    return str(text)[:MAX_MESSAGE_LENGTH]


def _crm_detail(response: httpx.Response):
    try:
        return response.json().get("detail")
    except (ValueError, AttributeError):
        return None


def _failure(http_status: int, status: str, message: str, **extra) -> BridgeResult:
    return BridgeResult(http_status, {"status": status, "message": message, **extra})


def _send(path: str, token: str, body: dict) -> httpx.Response:
    """POST to the configured CRM origin with the caller's token. Raises CRMConfigError or httpx errors."""
    base_url = crm_base_url()
    with make_http_client(base_url) as client:
        return client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})


def _common_failure(response: httpx.Response) -> BridgeResult | None:
    """Answers both operations map the same way: redirects, auth, CRM-side validation."""
    code = response.status_code
    if 300 <= code < 400:
        # Not followed: the token must never go to another address.
        return _failure(502, "crm_redirect_refused",
                        f"The CRM answered with a redirect ({code}); it was not followed.")
    if code == 401:
        return _failure(401, "unauthorized", "The CRM did not accept the bearer token.")
    if code == 403:
        return _failure(403, "forbidden", _short(_crm_detail(response) or "The CRM refused this action."))
    if code == 422:
        detail = _crm_detail(response)
        if isinstance(detail, dict) and detail.get("status") == "invalid":
            return BridgeResult(422, {
                "status": "invalid",
                "invalid_fields": detail.get("invalid_fields", []),
                "missing_fields": detail.get("missing_fields", []),
            })
        # A schema error from the CRM means the two services disagree on the contract.
        return _failure(502, "crm_contract_error", "The CRM rejected the request format.")
    return None


def request_proposal(token: str, fields: dict) -> BridgeResult:
    """Ask the CRM for a contact-draft proposal. Never creates a contact.

    Returns needs_information (missing fields + the normalized partial fields,
    so the caller can resubmit everything), the exact proposal, or a failure.
    """
    body = {"action": ACTION_CREATE_CONTACT_DRAFT, "fields": fields}
    try:
        response = _send(PROPOSALS_PATH, token, body)
    except CRMConfigError as error:
        return _failure(503, "crm_not_configured", str(error))
    except httpx.TimeoutException:
        # A proposal never creates a contact, so asking again is safe.
        logger.warning("CRM proposal request timed out")
        return _failure(504, "crm_timeout",
                        "The CRM did not answer in time. Nothing is saved by a proposal; it is safe to submit again.",
                        retry_safe=True)
    except httpx.HTTPError as error:
        logger.warning("CRM proposal request failed: %s", type(error).__name__)
        return _failure(502, "crm_unreachable",
                        "Could not reach the CRM. Nothing is saved by a proposal; it is safe to submit again.",
                        retry_safe=True)

    logger.info("CRM proposal answer: HTTP %s", response.status_code)
    failure = _common_failure(response)
    if failure:
        return failure
    try:
        data = response.json()
    except ValueError:
        data = None
    if response.status_code == 200 and isinstance(data, dict) and data.get("status") == "needs_information":
        return BridgeResult(200, {
            "status": "needs_information",
            "missing_fields": data.get("missing_fields", []),
            "invalid_fields": data.get("invalid_fields", []),
            "fields": data.get("fields", {}),
        })
    if response.status_code == 201 and isinstance(data, dict) and data.get("status") == "proposal":
        return BridgeResult(201, {
            "status": "proposal",
            "proposal_id": data.get("proposal_id"),
            "payload_hash": data.get("payload_hash"),
            "expires_at": data.get("expires_at"),
            "draft": data.get("draft"),
        })
    return _failure(502, "crm_error", f"Unexpected CRM answer (HTTP {response.status_code}).")


def login(username: str, password: str, organization_id: int | None = None) -> BridgeResult:
    """Log in at the CRM's POST /api/auth/token (form fields username, password,
    optional organization_id; backend/app/routers/auth.py) and return the token
    or the organizations to pick from.

    The password is sent once, to CRM_API_URL only, and never logged, stored or
    returned. The token is returned to the caller (the chat page keeps it in
    memory) and logged nowhere.
    """
    form = {"username": username, "password": password}
    if organization_id is not None:
        form["organization_id"] = str(organization_id)
    try:
        base_url = crm_base_url()
        with make_http_client(base_url) as client:
            response = client.post(LOGIN_PATH, data=form)
    except CRMConfigError as error:
        return _failure(503, "crm_not_configured", str(error))
    except httpx.TimeoutException:
        logger.warning("CRM login timed out")
        return _failure(504, "crm_timeout", "The CRM did not answer in time.")
    except httpx.HTTPError as error:
        logger.warning("CRM login failed: %s", type(error).__name__)
        return _failure(502, "crm_unreachable", "Could not reach the CRM.")

    code = response.status_code
    logger.info("CRM login answer: HTTP %s", code)
    if 300 <= code < 400:
        return _failure(502, "crm_redirect_refused",
                        f"The CRM answered with a redirect ({code}); it was not followed.")
    detail = _crm_detail(response)
    crm_code = detail.get("code") if isinstance(detail, dict) else None
    if code == 401:
        return _failure(401, "invalid_credentials", "Incorrect username or password.")
    if code == 403:
        status = LOGIN_REFUSALS.get(crm_code, "login_refused")
        return _failure(403, status, "The CRM refused this login.")
    if code == 429:
        return _failure(429, "rate_limited", "Too many login attempts. Wait a little and try again.")
    if code == 422:
        return _failure(502, "crm_contract_error", "The CRM rejected the login request format.")
    try:
        data = response.json()
    except ValueError:
        data = None
    if code == 200 and isinstance(data, dict):
        if data.get("requires_org_selection"):
            organizations = [
                {"id": org["id"], "name": str(org.get("name") or org["id"])}
                for org in data.get("organizations") or []
                if isinstance(org, dict) and isinstance(org.get("id"), int)
            ]
            if organizations:
                return BridgeResult(200, {"status": "choose_organization", "organizations": organizations})
        token = data.get("access_token")
        if isinstance(token, str) and token:
            # The user's language (or None); login succeeds either way.
            return BridgeResult(200, {"status": "logged_in", "access_token": token,
                                      "language": read_language(token)})
        return _failure(502, "crm_contract_error", "The CRM's login answer had no token.")
    return _failure(502, "crm_error", f"Unexpected CRM answer (HTTP {code}).")


def verify_login(token: str) -> BridgeResult:
    """Ask the CRM whether this token is a valid login right now (GET /api/auth/me).

    The CRM decodes the token and looks the user up (backend/app/deps.py
    get_current_user): a missing, disabled or deleted user, or a disabled
    organization, gets 401. Only the CRM knows that, so every protected request
    asks it - no cache, and no copy of the CRM's signing secret here.

    Fails closed: only a 200 with the expected user data is "valid". 401 ->
    the login is no longer valid; 403 -> a permission problem; anything else
    (timeout, no connection, 5xx, redirect, unreadable 200) -> "unavailable",
    which never logs the user out. The token goes to CRM_API_URL only, in the
    Authorization header; redirects are not followed; it is never logged.
    """
    try:
        base_url = crm_base_url()
        with make_http_client(base_url) as client:
            response = client.get(ME_PATH, headers={"Authorization": f"Bearer {token}"})
    except CRMConfigError:
        logger.warning("Login check: CRM_API_URL is not usable")
        return _failure(503, "unavailable", UNAVAILABLE_MESSAGE)
    except httpx.HTTPError as error:
        logger.warning("Login check failed: %s", type(error).__name__)
        return _failure(503, "unavailable", UNAVAILABLE_MESSAGE)

    code = response.status_code
    logger.info("Login check answer: HTTP %s", code)
    if code == 401:
        return _failure(401, "unauthorized", "Your login is no longer valid. Please log in again.")
    if code == 403:
        return _failure(403, "forbidden", "The CRM refused this request (permission).")
    if code != 200:
        return _failure(503, "unavailable", UNAVAILABLE_MESSAGE)
    try:
        data = response.json()
    except ValueError:
        data = None
    if not _is_user(data):
        logger.warning("Login check: unexpected 200 body from the CRM")
        return _failure(503, "unavailable", UNAVAILABLE_MESSAGE)
    # The role decides what the chat offers (instructions20: no contacts for an activist or
    # a campaign manager). Not a string -> None, which the chat treats as the narrowest role.
    role = data.get("role") if isinstance(data.get("role"), str) else None
    return BridgeResult(200, {"status": "valid", "role": role})


def _is_user(data) -> bool:
    """The fields of the CRM's UserOut that a real login always has."""
    def is_id(value):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    return (isinstance(data, dict) and is_id(data.get("id")) and is_id(data.get("organization_id"))
            and isinstance(data.get("username"), str) and bool(data["username"])
            and data.get("is_active") is True)


def confirm_proposal(token: str, proposal_id: str, payload_hash: str, fields: dict | None = None) -> BridgeResult:
    """Explicitly confirm one proposal; the CRM creates the draft at most once.

    "created" comes only from the CRM's success answer. A retry of a confirmed
    proposal returns the CRM's original result (replayed: true). When the
    answer is lost (timeout, dropped connection, CRM/proxy 5xx) the outcome is
    "uncertain": the draft may or may not be saved, and the same proposal is
    returned so the caller can confirm again safely - never a new proposal.
    """
    if not is_valid_proposal_id(proposal_id):
        return _failure(404, "unknown_proposal", "No such proposal.")
    body: dict = {"payload_hash": payload_hash}
    if fields is not None:
        body["fields"] = fields
    retry = {"proposal_id": proposal_id, "payload_hash": payload_hash, "retry_safe": True}
    uncertain_message = ("The CRM's answer was lost, so it is not known whether the draft was saved. "
                         "Confirm the same proposal again: the CRM saves it at most once and replays the result.")
    try:
        response = _send(f"{PROPOSALS_PATH}/{proposal_id}/confirm", token, body)
    except CRMConfigError as error:
        return _failure(503, "crm_not_configured", str(error))
    except (httpx.ConnectError, httpx.ConnectTimeout):
        # No connection was made, so the request never reached the CRM.
        logger.warning("CRM confirm: could not connect")
        return _failure(502, "crm_unreachable", "Could not reach the CRM; the draft was not sent.", **retry)
    except httpx.HTTPError as error:
        logger.warning("CRM confirm: outcome uncertain (%s)", type(error).__name__)
        return _failure(504, "uncertain", uncertain_message, **retry)

    code = response.status_code
    logger.info("CRM confirm answer: HTTP %s", code)
    failure = _common_failure(response)
    if failure:
        return failure
    if code >= 500:
        return _failure(502, "uncertain", uncertain_message, **retry)
    if code == 404:
        return _failure(404, "unknown_proposal", "The CRM has no such proposal for this user.")
    if code == 410:
        return _failure(410, "expired", "The proposal has expired. Request a new proposal.")
    if code == 409:
        if _crm_detail(response) == CRM_MISMATCH_DETAIL:
            return _failure(409, "payload_mismatch",
                            "The confirmed details do not match this proposal. Request a new proposal.")
        return _failure(409, "conflict", "The CRM could not confirm the proposal right now; confirm it again.",
                        **retry)
    try:
        data = response.json()
    except ValueError:
        data = None
    if code in (200, 201) and isinstance(data, dict) and data.get("status") == "created":
        return BridgeResult(code, {
            "status": "created",
            "replayed": bool(data.get("replayed")),
            "proposal_id": data.get("proposal_id"),
            "contact_id": data.get("contact_id"),
            "contact": data.get("contact"),
        })
    # A 2xx we cannot read: the CRM may have saved, so do not claim either way.
    return _failure(502, "uncertain", uncertain_message, **retry)


# --- Apartment edit (sheepsidebay_crm docs/fixes115.md) ------------------------
# The CRM's GET /api/assistant/apartments/search, /getapartment?id= and
# POST /{id}/update (backend/app/routers/assistant_apartments.py). Every
# apartment comes with its `editable_fields`, decided by the CRM per user and
# apartment. Same rules as the contact bridge: the user's token only, to
# CRM_API_URL only, no redirects. Reads are safe to repeat. An update whose
# answer is lost is "uncertain" and is NEVER resent by this module: the caller
# re-reads the lineage instead.

APARTMENTS_PATH = "/api/assistant/apartments"
OPTION_KINDS = ("single_option", "multi_option")


def _get(path: str, token: str, params: dict | None = None) -> httpx.Response:
    base_url = crm_base_url()
    with make_http_client(base_url) as client:
        return client.get(path, params=params, headers={"Authorization": f"Bearer {token}"})


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_label(value) -> bool:
    return value is None or isinstance(value, str)


def _is_option(option) -> bool:
    """value is a string, or a number (report numbers); labels may be missing."""
    if not isinstance(option, dict):
        return False
    value = option.get("value")
    return ((isinstance(value, str) or _is_int(value))
            and _is_label(option.get("label")) and _is_label(option.get("label_en")))


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_editable_field(entry) -> bool:
    """One editable_fields entry. The kind is only checked to be a string: a kind
    the chat doesn't know is left out by the conversation, not an error here."""
    if not (isinstance(entry, dict) and isinstance(entry.get("field_key"), str) and entry["field_key"]
            and isinstance(entry.get("kind"), str)
            and _is_label(entry.get("label")) and _is_label(entry.get("label_en"))):
        return False
    options = entry.get("options")
    if entry["kind"] in OPTION_KINDS:
        return isinstance(options, list) and all(_is_option(o) for o in options)
    return options is None or (isinstance(options, list) and all(_is_option(o) for o in options))


def _is_apartment(data) -> bool:
    """The fields of a CRM apartment result that the chat relies on."""
    return (isinstance(data, dict) and _is_id(data.get("apartment_id")) and _is_id(data.get("lineage_id"))
            and isinstance(data.get("editable_fields"), list)
            and all(_is_editable_field(f) for f in data["editable_fields"]))


def _read(path: str, token: str, params: dict | None = None) -> tuple[BridgeResult | None, object]:
    """(failure, None) or (None, the decoded 200 body). A read never changes anything."""
    try:
        response = _get(path, token, params)
    except CRMConfigError as error:
        return _failure(503, "crm_not_configured", str(error)), None
    except httpx.TimeoutException:
        logger.warning("CRM apartment read timed out")
        return _failure(504, "crm_timeout", "The CRM did not answer in time. Nothing was changed."), None
    except httpx.HTTPError as error:
        logger.warning("CRM apartment read failed: %s", type(error).__name__)
        return _failure(502, "crm_unreachable", "Could not reach the CRM. Nothing was changed."), None
    code = response.status_code
    logger.info("CRM apartment read answer: HTTP %s", code)
    failure = _common_failure(response)
    if failure:
        return failure, None
    if code == 404:
        return _failure(404, "not_found", "No such apartment for this user."), None
    try:
        data = response.json()
    except ValueError:
        data = None
    if code != 200 or data is None:
        return _failure(502, "crm_error", f"Unexpected CRM answer (HTTP {code})."), None
    return None, data


def search_apartments(token: str, query: str) -> BridgeResult:
    failure, data = _read(f"{APARTMENTS_PATH}/search", token, {"q": query})
    if failure:
        return failure
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not all(_is_apartment(r) for r in results):
        return _failure(502, "crm_contract_error", "The CRM's search answer had an unexpected shape.")
    return BridgeResult(200, {"status": "ok", "results": results, "truncated": bool(data.get("truncated"))})


def get_apartment(token: str, lineage_id: int) -> BridgeResult:
    """The lineage's current version (GET /getapartment?id=<lineage id>, sheepsidebay_crm
    fixes118): opening an apartment picked from a search, and every re-read after it."""
    if not _is_id(lineage_id):
        return _failure(404, "not_found", "No such apartment for this user.")
    failure, data = _read(f"{APARTMENTS_PATH}/getapartment", token, {"id": lineage_id})
    if failure:
        return failure
    if not _is_apartment(data):
        return _failure(502, "crm_contract_error", "The CRM's apartment answer had an unexpected shape.")
    return BridgeResult(200, {"status": "ok", "apartment": data})


# --- The combined search (sheepsidebay_crm fixes117, docs/instructions20.md) ----------
# GET /api/assistant/search?q=: people and apartments in one answer. The CRM decides
# who sees what (people only for admin and staff; apartments as in the apartment
# search). The chat additionally drops people for any other role.

SEARCH_PATH = "/api/assistant/search"
MIN_SEARCH_LENGTH = 2  # the CRM refuses shorter text (400)


def _is_text(value) -> bool:
    return value is None or isinstance(value, str)


def _is_person(data) -> bool:
    return (isinstance(data, dict) and _is_id(data.get("id"))
            and all(_is_text(data.get(k)) for k in ("name", "last_name", "phone", "address",
                                                    "status", "status_label")))


def search_everything(token: str, query: str) -> BridgeResult:
    """People and apartments matching `query`. A read: safe to repeat."""
    failure, data = _read(SEARCH_PATH, token, {"q": query})
    if failure:
        if failure.body["status"] == "not_found":
            # The route itself is missing: this CRM doesn't have fixes117 yet.
            return _failure(502, "search_missing", "This CRM does not offer the assistant search.")
        return failure
    if not (isinstance(data, dict) and isinstance(data.get("people"), list)
            and isinstance(data.get("apartments"), list)
            and all(_is_person(p) for p in data["people"])
            and all(_is_apartment(a) for a in data["apartments"])):
        return _failure(502, "crm_contract_error", "The CRM's search answer had an unexpected shape.")
    return BridgeResult(200, {
        "status": "ok",
        "people": data["people"],
        "apartments": data["apartments"],
        "people_truncated": data.get("people_truncated") is True,
        "apartments_truncated": data.get("apartments_truncated") is True,
        # Only an explicit true allows offering to add someone.
        "can_add_person": data.get("can_add_person") is True,
    })


def update_apartment_field(token: str, apartment_id: int, field_key: str, value) -> BridgeResult:
    """One field of one apartment version. "updated"/"unchanged" come only from the
    CRM's success answer, with the value and the apartment the CRM read back."""
    if not _is_id(apartment_id):
        return _failure(404, "not_found", "No such apartment for this user.")
    uncertain_message = ("The CRM's answer was lost, so it is not known whether the change was saved. "
                         "It was not resent; read the apartment again to find out.")
    try:
        response = _send(f"{APARTMENTS_PATH}/{apartment_id}/update", token, {"field_key": field_key, "value": value})
    except CRMConfigError as error:
        return _failure(503, "crm_not_configured", str(error))
    except (httpx.ConnectError, httpx.ConnectTimeout):
        logger.warning("CRM apartment update: could not connect")
        return _failure(502, "crm_unreachable", "Could not reach the CRM; the change was not sent.")
    except httpx.HTTPError as error:
        logger.warning("CRM apartment update: outcome uncertain (%s)", type(error).__name__)
        return _failure(504, "uncertain", uncertain_message)

    code = response.status_code
    logger.info("CRM apartment update answer: HTTP %s", code)
    failure = _common_failure(response)
    if failure:
        return failure
    if code >= 500:
        return _failure(502, "uncertain", uncertain_message)
    if code == 404:
        return _failure(404, "not_found", "No such apartment for this user.")
    if code == 409:
        return _failure(409, "stale", "The apartment was changed meanwhile; nothing was saved.")
    try:
        data = response.json()
    except ValueError:
        data = None
    if (code == 200 and isinstance(data, dict) and data.get("status") in ("updated", "unchanged")
            and _is_id(data.get("apartment_id")) and _is_apartment(data.get("apartment"))):
        return BridgeResult(200, {
            "status": data["status"],
            "field_key": data.get("field_key"),
            "value": data.get("value"),
            "apartment_id": data["apartment_id"],
            "previous_apartment_id": data.get("previous_apartment_id"),
            "lineage_id": data.get("lineage_id"),
            "apartment": data["apartment"],
        })
    # A 2xx we cannot read: the CRM may have saved, so do not claim either way.
    return _failure(502, "uncertain", uncertain_message)


# --- The user's language (docs/instructions19.md) ------------------------------
# The CRM's preference "locale" is the only stored copy. Only that key is read,
# never written; the rest of the preference map never leaves this module.

def read_language(token: str) -> str | None:
    """The CRM's locale for this user: "he", "en", or None (unset, odd value, or any failure)."""
    try:
        base_url = crm_base_url()
        with make_http_client(base_url) as client:
            response = client.get(PREFERENCES_PATH, headers={"Authorization": f"Bearer {token}"})
    except (CRMConfigError, httpx.HTTPError) as error:
        logger.warning("CRM preferences read failed: %s", type(error).__name__)
        return None
    if response.status_code != 200:
        logger.warning("CRM preferences read answer: HTTP %s", response.status_code)
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    locale = data.get("locale") if isinstance(data, dict) else None
    return locale if locale in LOCALES else None

