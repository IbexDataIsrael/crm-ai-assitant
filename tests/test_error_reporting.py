"""Error-reporting tests: every provider error format we have met, all mocked.

Formats seen so far:
- Interactions API errors (status in .status_code) with the body as a
  one-item list - the live 400 "API key not valid" from a fake key.
- Interactions API errors with a dict body, a JSON string body, plain text
  or no body at all (the live "thinking_level: minimal" 400 gave no status,
  so its body shape is unknown - every shape must still give a useful line).
- Older google.genai.errors.APIError classes (.code / .status / .message).
- Timeouts (APITimeoutError, live) and other transport errors.
"""

import logging

import httpx
import pytest
from google.genai import errors
from google.genai._gaos.lib import compat_errors

from app import generation
from tests.conftest import FakeInteraction

PASSAGES = [{"filename": "donations.md", "heading": "Receipts", "text": "Every donor can ask for a receipt.", "score": 0.4}]
FAKE_KEY = "test-placeholder-not-a-key"
LOOKS_LIKE_KEY = "AIza" + "B" * 35  # Google key format, not a real key

# The body shape returned live for a request with an invalid key.
INVALID_KEY_BODY = [{
    "error": {
        "code": 400,
        "message": "API key not valid. Please pass a valid API key.",
        "status": "INVALID_ARGUMENT",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID",
             "domain": "googleapis.com", "metadata": {"service": "generativelanguage.googleapis.com"}},
            {"@type": "type.googleapis.com/google.rpc.LocalizedMessage", "locale": "en-US",
             "message": "API key not valid. Please pass a valid API key."},
        ],
    }
}]


def status_error(cls, code, body):
    request = httpx.Request("POST", "https://example.invalid/v1beta2/interactions", headers={"x-goog-api-key": FAKE_KEY})
    response = httpx.Response(code, request=request, headers={"x-debug": "secret-header"}, content=b"ignored")
    return cls(message=f"Error code: {code} - {body!r}", response=response, body=body)


def fail_with(fake_gemini, error) -> str:
    fake_gemini.error = error
    result = generation.generate_answer("How do I get a receipt?", PASSAGES)
    assert result.status == "error"
    assert result.answer is None
    return result.error


def test_interactions_400_with_list_body(fake_gemini):
    message = fail_with(fake_gemini, status_error(compat_errors.BadRequestError, 400, INVALID_KEY_BODY))
    assert message == (
        "Gemini API error 400 INVALID_ARGUMENT: API key not valid. Please pass a valid API key. (API_KEY_INVALID)"
    )
    assert "generativelanguage" not in message  # metadata and the rest of the body are dropped


def test_interactions_400_with_dict_body(fake_gemini):
    body = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "thinking_level 'minimal' is not supported for this model."}}
    message = fail_with(fake_gemini, status_error(compat_errors.BadRequestError, 400, body))
    assert message == "Gemini API error 400 INVALID_ARGUMENT: thinking_level 'minimal' is not supported for this model."


def test_interactions_400_with_json_string_body(fake_gemini):
    body = '{"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "Bad field."}}'
    message = fail_with(fake_gemini, status_error(compat_errors.BadRequestError, 400, body))
    assert message == "Gemini API error 400 INVALID_ARGUMENT: Bad field."


def test_interactions_400_with_plain_text_body(fake_gemini):
    message = fail_with(fake_gemini, status_error(compat_errors.BadRequestError, 400, "Bad Request"))
    assert message == "Gemini API error 400: Bad Request"


def test_interactions_400_with_bytes_body(fake_gemini):
    body = b'{"error": {"status": "INVALID_ARGUMENT", "message": "From bytes."}}'
    message = fail_with(fake_gemini, status_error(compat_errors.BadRequestError, 400, body))
    assert message == "Gemini API error 400 INVALID_ARGUMENT: From bytes."


def test_interactions_400_with_no_body(fake_gemini):
    message = fail_with(fake_gemini, status_error(compat_errors.BadRequestError, 400, None))
    assert message == "Gemini API error 400"


def test_interactions_429(fake_gemini):
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded for metric: generate_requests_per_minute."}}
    message = fail_with(fake_gemini, status_error(compat_errors.RateLimitError, 429, body))
    assert message == (
        "Gemini API error 429 RESOURCE_EXHAUSTED: Quota exceeded for metric: generate_requests_per_minute."
        " - free-tier rate limit or quota reached, try again later"
    )


def test_interactions_500(fake_gemini):
    body = {"error": {"code": 500, "status": "INTERNAL", "message": "Internal error encountered."}}
    message = fail_with(fake_gemini, status_error(compat_errors.InternalServerError, 500, body))
    assert message == "Gemini API error 500 INTERNAL: Internal error encountered."


def test_older_sdk_error_classes(fake_gemini):
    error = errors.ClientError(404, {"error": {"code": 404, "status": "NOT_FOUND", "message": "models/gemini-x is not found."}})
    assert fail_with(fake_gemini, error) == "Gemini API error 404 NOT_FOUND: models/gemini-x is not found."


def test_interactions_timeout(fake_gemini):
    error = compat_errors.APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
    message = fail_with(fake_gemini, error)
    assert message == f"Gemini did not respond within {generation.TIMEOUT_SECONDS} s (APITimeoutError)"


def test_connection_error(fake_gemini):
    error = compat_errors.APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
    assert fail_with(fake_gemini, error) == "Could not reach Gemini (APIConnectionError)"


def test_client_closed_runtime_error(fake_gemini):
    error = RuntimeError("Cannot send a request, as the client has been closed.")
    assert fail_with(fake_gemini, error) == "Could not reach Gemini (RuntimeError)"


@pytest.mark.parametrize(
    "provider_message, leaked",
    [
        (f"Request had key {FAKE_KEY} which is invalid", FAKE_KEY),  # our own key, whatever its format
        (f"Bad key {LOOKS_LIKE_KEY} supplied", LOOKS_LIKE_KEY),
        ("Rejected url ?key=abc123secret&alt=json", "abc123secret"),
        ("Header x-goog-api-key: abc123secret was rejected", "abc123secret"),
        ("Authorization: Bearer ya29.abc123secret expired", "ya29.abc123secret"),
    ],
)
def test_secrets_are_redacted(fake_gemini, provider_message, leaked):
    body = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": provider_message}}
    message = fail_with(fake_gemini, status_error(compat_errors.BadRequestError, 400, body))
    assert leaked not in message
    assert "[REDACTED]" in message


def test_status_is_redacted_and_shortened(fake_gemini):
    body = {"error": {"code": 400, "status": f"BAD_{FAKE_KEY}_" + "X" * 100, "message": "Bad."}}
    message = fail_with(fake_gemini, status_error(compat_errors.BadRequestError, 400, body))
    assert FAKE_KEY not in message
    status = message.removeprefix("Gemini API error 400 ").split(": ", 1)[0]
    assert len(status) <= 40
    assert status.endswith("...")


def test_older_sdk_status_attribute_is_redacted(fake_gemini):
    error = errors.ClientError(400, {"error": {"code": 400, "status": f"key={FAKE_KEY}", "message": "Bad."}})
    message = fail_with(fake_gemini, error)
    assert FAKE_KEY not in message
    assert "[REDACTED]" in message


@pytest.mark.parametrize(
    "details, expected_suffix",
    [
        ({"reason": "API_KEY_INVALID"}, " (API_KEY_INVALID)"),  # a single dict instead of a list
        ("API_KEY_INVALID", ""),  # a string - must not be read character by character
        (42, ""),
        (None, ""),
        ([{"reason": 7}, "junk", {"reason": "  "}, {"reason": "QUOTA"}], " (QUOTA)"),
    ],
)
def test_unusual_details_do_not_crash(fake_gemini, details, expected_suffix):
    body = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "Bad.", "details": details}}
    message = fail_with(fake_gemini, status_error(compat_errors.BadRequestError, 400, body))
    assert message == "Gemini API error 400 INVALID_ARGUMENT: Bad." + expected_suffix


def test_long_provider_message_is_shortened(fake_gemini):
    body = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "word " * 200}}
    message = fail_with(fake_gemini, status_error(compat_errors.BadRequestError, 400, body))
    detail = message.split(": ", 1)[1]
    assert len(detail) <= 200
    assert detail.endswith("...")


def test_log_line_has_no_key_headers_or_body(fake_gemini, caplog):
    body = [{"error": {**INVALID_KEY_BODY[0]["error"], "message": f"bad key {FAKE_KEY}"}}]
    with caplog.at_level(logging.DEBUG):
        fail_with(fake_gemini, status_error(compat_errors.BadRequestError, 400, body))
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Answer generation failed: Gemini API error 400 INVALID_ARGUMENT" in logged
    for forbidden in (FAKE_KEY, "x-goog-api-key", "secret-header", "generativelanguage", "Error code:", "metadata"):
        assert forbidden not in logged, forbidden


def test_successful_answers_are_unaffected(fake_gemini):
    fake_gemini.reply = FakeInteraction("Ask for one [1].")
    assert generation.generate_answer("How do I get a receipt?", PASSAGES).status == "answered"


def test_thinking_level_stays_low_by_default(fake_gemini):
    fake_gemini.reply = FakeInteraction("Ask for one [1].")
    generation.generate_answer("How do I get a receipt?", PASSAGES)
    assert fake_gemini.requests[0]["generation_config"]["thinking_level"] == "low"
