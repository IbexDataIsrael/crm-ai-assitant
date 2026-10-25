"""Claude Haiku 4.5 provider tests - all mocked, no live API calls.

Same contract as Gemini: grounded answer with [n] citations, insufficient
sources, missing key, redacted errors. Plus the paid-provider guards: the full
input is counted before sending and refused over MAX_INPUT_TOKENS, output is
capped at MAX_OUTPUT_TOKENS, and the client never retries.
"""

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from app import generation
from app.main import app
from tests.conftest import REAL_MAKE_ANTHROPIC_CLIENT, TEST_AUTH, FakeMessage

PASSAGES = [
    {"filename": "donations.md", "heading": "Accepting a cash donation", "text": "Count the cash with a second volunteer.", "score": 0.5},
    {"filename": "donations.md", "heading": "Receipts", "text": "Every donor can ask for a receipt.", "score": 0.4},
]
QUESTION = "What do I do with money someone gives me?"
FAKE_KEY = "sk-ant-test-placeholder-not-a-key"


def anthropic_error(cls, code, error_type, message):
    body = {"type": "error", "error": {"type": error_type, "message": message}}
    request = httpx2.Request("POST", "https://example.invalid/v1/messages", headers={"x-api-key": FAKE_KEY})
    response = httpx2.Response(code, request=request, headers={"request-id": "req_test"}, json=body)
    return cls(f"Error code: {code} - {body!r}", response=response, body=body)


# --- answers ------------------------------------------------------------------

def test_answer_with_citations(fake_anthropic):
    fake_anthropic.reply = FakeMessage("Count it with a second volunteer [1] and offer a receipt [2].", input_tokens=410, output_tokens=25)
    result = generation.generate_answer(QUESTION, PASSAGES)
    assert result.status == "answered"
    assert result.provider == "Claude"
    assert result.model == "claude-haiku-4-5-20251001"
    assert result.citations == [
        {"number": 1, "filename": "donations.md", "heading": "Accepting a cash donation"},
        {"number": 2, "filename": "donations.md", "heading": "Receipts"},
    ]
    assert result.usage == {"counted_input_tokens": 400, "input_tokens": 410, "output_tokens": 25}


def test_request_sends_only_question_and_passages_with_output_cap(fake_anthropic):
    fake_anthropic.reply = FakeMessage("Answer [1].")
    generation.generate_answer(QUESTION, PASSAGES)
    request = fake_anthropic.creates[0]
    assert request == {
        "model": "claude-haiku-4-5-20251001",
        "system": generation.SYSTEM_INSTRUCTION,
        "messages": [{"role": "user", "content": generation.build_input(QUESTION, PASSAGES)}],
        "max_tokens": 1000,
    }
    assert FAKE_KEY not in str(request)


def test_whole_input_is_counted_before_sending(fake_anthropic):
    fake_anthropic.reply = FakeMessage("Answer [1].")
    generation.generate_answer(QUESTION, PASSAGES)
    counted = fake_anthropic.counts[0]
    sent = dict(fake_anthropic.creates[0])
    sent.pop("max_tokens")
    assert counted == sent  # instructions, question and passages: exactly what is sent


def test_model_is_configurable(fake_anthropic, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
    fake_anthropic.reply = FakeMessage("Answer [1].")
    assert generation.generate_answer(QUESTION, PASSAGES).model == "claude-haiku-4-5"
    assert fake_anthropic.creates[0]["model"] == "claude-haiku-4-5"


def test_insufficient_sources(fake_anthropic):
    fake_anthropic.reply = FakeMessage(generation.INSUFFICIENT_MARKER)
    result = generation.generate_answer("How do I reset the office alarm?", PASSAGES)
    assert result.status == "insufficient_sources"
    assert result.answer is None


@pytest.mark.parametrize(
    "reply",
    [
        FakeMessage("Just keep it safe."),  # no citation
        FakeMessage("Put it in the box [3]."),  # cites a passage that doesn't exist
        FakeMessage(None),  # no text block
        FakeMessage("   "),
    ],
)
def test_bad_replies_are_not_shown(fake_anthropic, reply):
    fake_anthropic.reply = reply
    result = generation.generate_answer(QUESTION, PASSAGES)
    assert result.status == "error"
    assert result.answer is None


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal", "pause_turn"])
def test_cut_off_or_refused_replies_are_not_shown(fake_anthropic, stop_reason):
    fake_anthropic.reply = FakeMessage("Count it [1] and then", stop_reason=stop_reason)
    result = generation.generate_answer(QUESTION, PASSAGES)
    assert result.status == "error"
    assert result.answer is None
    assert result.error == f"Claude stopped with '{stop_reason}', so the answer is not shown"


# --- cost guards ----------------------------------------------------------------

def test_input_over_limit_is_not_sent(fake_anthropic):
    fake_anthropic.counted_tokens = 2001
    result = generation.generate_answer(QUESTION, PASSAGES)
    assert fake_anthropic.creates == []
    assert result.status == "error"
    assert result.error == "Input is 2001 tokens, over the 2000-token limit - not sent"


def test_input_at_limit_is_sent(fake_anthropic):
    fake_anthropic.counted_tokens = 2000
    fake_anthropic.reply = FakeMessage("Answer [1].")
    assert generation.generate_answer(QUESTION, PASSAGES).status == "answered"
    assert len(fake_anthropic.creates) == 1


def test_token_count_failure_sends_nothing(fake_anthropic):
    fake_anthropic.count_error = anthropic_error(anthropic.AuthenticationError, 401, "authentication_error", "invalid x-api-key")
    result = generation.generate_answer(QUESTION, PASSAGES)
    assert fake_anthropic.creates == []
    assert result.error == "Token count failed, nothing was sent - Claude API error 401 authentication_error: invalid x-api-key"


def test_worst_case_cost_of_two_calls_is_within_budget():
    per_call = generation.MAX_INPUT_TOKENS * 1 / 1_000_000 + generation.MAX_OUTPUT_TOKENS * 5 / 1_000_000
    assert per_call == pytest.approx(0.007)
    assert 2 * per_call <= 0.05


def test_real_client_has_retries_off_and_sends_one_request(monkeypatch):
    """Offline: the network layer is faked, nothing leaves the machine."""
    sent = []

    def fake_send(self, request, **kwargs):
        sent.append(request.url.path)
        if len(sent) > 5:
            raise AssertionError("retry loop")
        return httpx2.Response(429, request=request, json={"type": "error", "error": {"type": "rate_limit_error", "message": "fake"}})

    monkeypatch.setattr(httpx2.Client, "send", fake_send)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    client = REAL_MAKE_ANTHROPIC_CLIENT(FAKE_KEY)
    assert client.max_retries == 0
    assert client.timeout == generation.TIMEOUT_SECONDS

    monkeypatch.setattr(generation, "make_anthropic_client", REAL_MAKE_ANTHROPIC_CLIENT)
    result = generation.generate_answer(QUESTION, PASSAGES, provider="anthropic")
    assert sent == ["/v1/messages/count_tokens"]  # one request; the message itself was never sent
    assert result.status == "error"
    assert result.error.startswith("Token count failed, nothing was sent - Claude API error 429 rate_limit_error: fake")


# --- missing key, provider choice ------------------------------------------------

def test_missing_key_makes_no_call(monkeypatch):
    monkeypatch.setenv("ANSWER_PROVIDER", "anthropic")
    result = generation.generate_answer(QUESTION, PASSAGES)  # autouse fixture: no key, clients refused
    assert result.status == "no_api_key"
    assert result.error == "ANTHROPIC_API_KEY is not set"
    assert result.provider == "Claude"


def test_gemini_stays_the_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)  # present, but not selected
    result = generation.generate_answer(QUESTION, PASSAGES)
    assert result.provider == "Gemini"
    assert result.error == "GEMINI_API_KEY is not set"


def test_unknown_provider_is_an_error(monkeypatch):
    monkeypatch.setenv("ANSWER_PROVIDER", "openai")
    result = generation.generate_answer(QUESTION, PASSAGES)
    assert result.status == "error"
    assert result.error == "Unknown ANSWER_PROVIDER 'openai' (use gemini or anthropic)"


# --- errors ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "error, expected",
    [
        (anthropic_error(anthropic.RateLimitError, 429, "rate_limit_error", "Number of request tokens has exceeded your per-minute rate limit"),
         "Claude API error 429 rate_limit_error: Number of request tokens has exceeded your per-minute rate limit - rate limit reached, try again later"),
        (anthropic_error(anthropic.BadRequestError, 400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API."),
         "Claude API error 400 invalid_request_error: Your credit balance is too low to access the Anthropic API."),
        (anthropic_error(anthropic.NotFoundError, 404, "not_found_error", "model: claude-haiku-4-5-20251001"),
         "Claude API error 404 not_found_error: model: claude-haiku-4-5-20251001"),
        (anthropic_error(anthropic.InternalServerError, 529, "overloaded_error", "Overloaded"),
         "Claude API error 529 overloaded_error: Overloaded"),
        (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://example.invalid")),
         f"Claude did not respond within {generation.TIMEOUT_SECONDS} s (APITimeoutError)"),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://example.invalid")),
         "Could not reach Claude (APIConnectionError)"),
    ],
)
def test_send_errors_are_reported_not_answered(fake_anthropic, error, expected):
    fake_anthropic.error = error
    result = generation.generate_answer(QUESTION, PASSAGES)
    assert result.status == "error"
    assert result.answer is None
    assert result.error == expected


def test_anthropic_keys_are_redacted(fake_anthropic):
    other_key = "sk-ant-api03-" + "Z" * 40
    fake_anthropic.error = anthropic_error(
        anthropic.AuthenticationError, 401, "authentication_error", f"bad keys {FAKE_KEY} and {other_key}; x-api-key: abc123secret"
    )
    result = generation.generate_answer(QUESTION, PASSAGES)
    for leaked in (FAKE_KEY, other_key, "abc123secret"):
        assert leaked not in result.error
    assert "[REDACTED]" in result.error


def test_log_line_has_no_key_headers_or_body(fake_anthropic, caplog):
    fake_anthropic.error = anthropic_error(anthropic.AuthenticationError, 401, "authentication_error", f"bad key {FAKE_KEY}")
    with caplog.at_level("DEBUG"):
        generation.generate_answer(QUESTION, PASSAGES)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Answer generation failed: Claude API error 401 authentication_error" in logged
    for forbidden in (FAKE_KEY, "x-api-key", "req_test", "Error code:", "'type': 'error'"):
        assert forbidden not in logged, forbidden


# --- through /chat --------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _logged_in_for_chat(request):
    # Only the /chat tests below need the fake CRM's login check.
    if "client" in request.fixturenames:
        request.getfixturevalue("logged_in")


def test_chat_with_claude_answer(client, sample_knowledge, fake_anthropic):
    fake_anthropic.reply = FakeMessage("Count it with a second volunteer and lock it in the office box [1].")
    data = client.post("/chat", headers=TEST_AUTH, json={"message": "How do I deal with money a supporter gives me?"}).json()
    assert data["generation"]["status"] == "answered"
    assert data["generation"]["provider"] == "Claude"
    assert data["reply"].startswith("AI-generated answer (Claude, claude-haiku-4-5-20251001) - based only on the passages below:")
    assert "Cited: [1] donations.md > Accepting a cash donation" in data["reply"]


def test_chat_with_claude_insufficient_sources(client, sample_knowledge, fake_anthropic):
    fake_anthropic.reply = FakeMessage("INSUFFICIENT_SOURCES")
    data = client.post("/chat", headers=TEST_AUTH, json={"message": "How do I reset the office alarm?"}).json()
    assert data["reply"].startswith(
        "No answer: Claude (claude-haiku-4-5-20251001) found that the passages below don't answer this question."
    )


def test_chat_with_claude_over_limit(client, sample_knowledge, fake_anthropic):
    fake_anthropic.counted_tokens = 5000
    data = client.post("/chat", headers=TEST_AUTH, json={"message": "How do I deal with money a supporter gives me?"}).json()
    assert fake_anthropic.creates == []
    assert data["reply"].startswith(
        "No AI-written answer: answer generation failed (Input is 5000 tokens, over the 2000-token limit - not sent)."
    )
