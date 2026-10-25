"""Bridge to the CRM's contact-draft API (POST /api/assistant/proposals and
POST /api/assistant/proposals/{id}/confirm, in sheepsidebay_crm's
backend/app/routers/assistant.py).

Structured fields only - no AI, no free-text extraction. The CRM owns
permissions, organization scoping, validation, saving and idempotency; this
module only forwards the request and maps the CRM's answer to a result the
client can act on.

The CRM address comes from CRM_API_URL (no default, no production fallback).
The caller's CRM bearer token is forwarded per request to that one origin
only: redirects are never followed, and the token is never logged, stored,
returned or sent anywhere else (no AI provider sees it).

LOCALHOST ONLY. This service has no authentication of its own: anyone who can
reach it can use it with their own CRM token. Production authentication is
not settled and must be before deployment.
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
