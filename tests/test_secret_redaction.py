"""Tests for R-3: consolidated secret redaction.

Two consumers with deliberately different aggressiveness:

* sanitize_log_output()        -- logs/diagnostics. Over-redaction is harmless, so any
                                  value assigned to a secret-sounding key is masked.
* redact_secrets_for_context() -- source code bound for an LLM prompt. Over-redaction
                                  mangles the code the model must reason about, so only
                                  high-confidence secrets are removed and line numbering
                                  is preserved.

A large share of these tests are false-positive guards: a scrubber that destroys ordinary
code would make CodePilot's fixes worse, which is the real risk in this change.
"""

from pathlib import Path

import pytest

from codepilot.config import load_config
from codepilot.core.context.engine import ProjectContextEngine
from codepilot.core.context.prompt import build_model_request
from codepilot.core.fixer import generate_preview_patch
from codepilot.core.parser.models import InstructionTask
from codepilot.core.security import (
    PRIVATE_KEY_PLACEHOLDER,
    REDACTION_PLACEHOLDER,
    redact_secrets_for_context,
    sanitize_log_output,
)
from codepilot.exceptions import FixError

OPENAI_KEY = "sk-" + "a1b2c3d4e5" * 3
GITHUB_PAT = "ghp_" + "b" * 36
# Assembled at runtime rather than written as one literal. A contiguous, realistically
# shaped token in the source tripped GitHub's push protection and blocked the push, even
# though the value is fabricated. Building it from parts keeps the test meaningful while
# leaving no token-shaped string in the file for a scanner to match.
SLACK_TOKEN = "xoxb" + "-" + "0" * 12 + "-" + "f4k3SlackTokenValue"
PEM_BLOCK = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA1234567890\n"
    "abcdefghijklmnopqrstuvwxyz\n"
    "-----END RSA PRIVATE KEY-----"
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# ==============================================================================
# sanitize_log_output -- aggressive redaction for logs and diagnostics
# ==============================================================================


@pytest.mark.parametrize(
    "raw,secret",
    [
        # The JSON form that the previous unquoted-key-only pattern missed (S-20)
        ('{"api_key": "plain-nonvendor-value-1234"}', "plain-nonvendor-value-1234"),
        ("{'secret': 'topsecret-value-here'}", "topsecret-value-here"),
        ('{"password": "hunter2hunter2"}', "hunter2hunter2"),
        # Pre-existing forms must keep working
        ("api_key='" + OPENAI_KEY + "'", OPENAI_KEY),
        ("password=hunter2", "hunter2"),
        ("token: abc123def456", "abc123def456"),
        # Prefixed identifiers: '_' is a word character, so these previously escaped
        ('db_password = "SuperSecretDbPassword123"', "SuperSecretDbPassword123"),
        ('openai_api_key = "nonvendorsecretvalue99"', "nonvendorsecretvalue99"),
        ('MY_SECRET = "alongsecretvaluehere"', "alongsecretvaluehere"),
    ],
)
def test_log_sanitizer_redacts_key_value_secrets(raw: str, secret: str) -> None:
    sanitized = sanitize_log_output(raw)

    assert secret not in sanitized
    assert REDACTION_PLACEHOLDER in sanitized


@pytest.mark.parametrize(
    "secret",
    [
        OPENAI_KEY,
        GITHUB_PAT,
        "AKIAIOSFODNN7EXAMPLE",  # AWS's own published example key ID
        SLACK_TOKEN,
        "AIza" + "B" * 35,
        "glpat-" + "c" * 20,
        "sk_live_" + "d" * 24,
    ],
)
def test_log_sanitizer_redacts_vendor_tokens_anywhere(secret: str) -> None:
    sanitized = sanitize_log_output(f"request failed carrying {secret} in the header")

    assert secret not in sanitized
    assert REDACTION_PLACEHOLDER in sanitized


def test_log_sanitizer_redacts_private_key_blocks() -> None:
    sanitized = sanitize_log_output(f"loaded key:\n{PEM_BLOCK}\ndone")

    assert "MIIEowIBAAKCAQEA1234567890" not in sanitized
    assert PRIVATE_KEY_PLACEHOLDER in sanitized


def test_log_sanitizer_preserves_the_key_name_for_readability() -> None:
    """A redacted log line must still say which setting was involved."""
    sanitized = sanitize_log_output('{"api_key": "somethingsecret1234"}')

    assert "api_key" in sanitized


def test_log_sanitizer_handles_empty_input() -> None:
    assert sanitize_log_output("") == ""


def test_log_sanitizer_leaves_ordinary_text_alone() -> None:
    message = "Patch applied to calculator.py with 3 modified lines."

    assert sanitize_log_output(message) == message


# ==============================================================================
# redact_secrets_for_context -- conservative redaction for model prompts
# ==============================================================================


@pytest.mark.parametrize(
    "source,secret",
    [
        ('API_KEY = "' + OPENAI_KEY + '"\n', OPENAI_KEY),
        ('client = OpenAI(api_key="' + OPENAI_KEY + '")\n', OPENAI_KEY),
        ('TOKEN = "' + GITHUB_PAT + '"\n', GITHUB_PAT),
        ('API_KEY = "Zx81kdlwoeApQm39xKd"\n', "Zx81kdlwoeApQm39xKd"),
        ('db_password = "SuperSecretDbPassword123"\n', "SuperSecretDbPassword123"),
        ('auth_token = "Zx81kdlwoeApQm39xKdLm22"\n', "Zx81kdlwoeApQm39xKdLm22"),
    ],
)
def test_context_redaction_removes_high_confidence_secrets(source: str, secret: str) -> None:
    redacted, findings = redact_secrets_for_context(source)

    assert secret not in redacted
    assert findings


@pytest.mark.parametrize(
    "source",
    [
        # Runtime indirections: the value is not in the file at all
        'API_KEY = os.environ["OPENAI_API_KEY"]\n',
        'token = os.getenv("TOKEN")\n',
        "api_key = process.env.API_KEY\n",
        # Obvious placeholders
        'API_KEY = "your-api-key-here"\n',
        'API_KEY = "changeme"\n',
        'API_KEY = "<your-key>"\n',
        'API_KEY = ""\n',
        "api_key = None\n",
        # Too short to be a credential
        'token = "abc"\n',
        # Unquoted: an identifier or expression, not a literal
        "api_key = user_supplied_key\n",
        "secret = config['secret']\n",
        # Names that merely contain a secret-ish word
        'token_count = "1234567890123456"\n',
        'tokenizer = "bert-base-uncased-model"\n',
        # Signatures, comments and docstrings
        "def connect(api_key, token):\n    return api_key\n",
        "# remember to pass the api_key to the client\n",
        '"""Reads the api_key from the environment."""\n',
        # Ordinary code
        "def divide(a, b):\n    return a / b\n",
        "token = parse_next_token(stream)\n",
    ],
)
def test_context_redaction_leaves_ordinary_code_untouched(source: str) -> None:
    """Over-redaction would corrupt the code the model has to modify."""
    redacted, findings = redact_secrets_for_context(source)

    assert redacted == source
    assert findings == []


def test_context_redaction_preserves_line_count_for_private_keys() -> None:
    """Line numbers reported for a snippet must keep matching the real file."""
    source = (
        "import os\n"
        "KEY = '''" + PEM_BLOCK + "'''\n"
        "\n"
        "def f():\n"
        "    return 1\n"
    )

    redacted, findings = redact_secrets_for_context(source)

    assert source.count("\n") == redacted.count("\n")
    assert "MIIEowIBAAKCAQEA1234567890" not in redacted
    assert findings == ["1 private key block(s)"]


def test_context_redaction_preserves_surrounding_structure() -> None:
    source = (
        "def connect():\n"
        '    api_key = "' + OPENAI_KEY + '"\n'
        "    return Client(api_key)\n"
    )

    redacted, _ = redact_secrets_for_context(source)

    assert "def connect():" in redacted
    assert "return Client(api_key)" in redacted
    assert redacted.count("\n") == source.count("\n")


def test_context_redaction_reports_what_it_removed() -> None:
    source = 'API_KEY = "' + OPENAI_KEY + '"\ndb_password = "SuperSecretDbPassword123"\n'

    _, findings = redact_secrets_for_context(source)

    joined = " ".join(findings)
    assert "provider API token" in joined
    assert "hardcoded credential" in joined


def test_context_redaction_handles_empty_input() -> None:
    assert redact_secrets_for_context("") == ("", [])


def test_context_redaction_is_idempotent() -> None:
    source = 'API_KEY = "' + OPENAI_KEY + '"\n'

    once, _ = redact_secrets_for_context(source)
    twice, findings = redact_secrets_for_context(once)

    assert twice == once
    assert findings == []


# ==============================================================================
# Integration: secrets must not reach the prompt (S-26)
# ==============================================================================


def _build_prompt(project_root: Path, file_path: str, line_number: int, symbol: str) -> str:
    engine = ProjectContextEngine(project_root=project_root)
    task = InstructionTask(
        id="t1",
        file_path=file_path,
        line_number=line_number,
        instruction_type="AI-FIX",
        description="handle division by zero",
        target_symbol=symbol,
        target_symbol_type="function",
    )
    bundle = engine.build_context(task, token_budget=4096)
    request = build_model_request(bundle)
    return (request.system_prompt or "") + "\n" + request.prompt, bundle


SOURCE_WITH_SECRETS = (
    "import os\n"
    "\n"
    "def connect(a, b):\n"
    '    api_key = "' + OPENAI_KEY + '"\n'
    '    db_password = "SuperSecretDbPassword123"\n'
    '    safe = os.environ["SAFE"]\n'
    "    # AI-FIX: handle division by zero\n"
    "    return a / b\n"
)


def test_hardcoded_secret_does_not_reach_the_model_prompt(tmp_path: Path) -> None:
    _write(tmp_path / "app.py", SOURCE_WITH_SECRETS)

    prompt, _ = _build_prompt(tmp_path, "app.py", 7, "connect")

    assert OPENAI_KEY not in prompt
    assert "SuperSecretDbPassword123" not in prompt


def test_real_code_survives_in_the_model_prompt(tmp_path: Path) -> None:
    _write(tmp_path / "app.py", SOURCE_WITH_SECRETS)

    prompt, _ = _build_prompt(tmp_path, "app.py", 7, "connect")

    assert "def connect(a, b)" in prompt
    assert "return a / b" in prompt
    assert 'os.environ["SAFE"]' in prompt


def test_developer_is_warned_that_context_was_redacted(tmp_path: Path) -> None:
    _write(tmp_path / "app.py", SOURCE_WITH_SECRETS)

    _, bundle = _build_prompt(tmp_path, "app.py", 7, "connect")

    assert any("Redacted from model context" in w for w in bundle.warnings)


def test_snippet_lines_still_map_to_the_real_file(tmp_path: Path) -> None:
    _write(tmp_path / "app.py", SOURCE_WITH_SECRETS)

    _, bundle = _build_prompt(tmp_path, "app.py", 7, "connect")

    real_lines = SOURCE_WITH_SECRETS.splitlines()
    snippet = bundle.target_snippet
    for offset, line in enumerate(snippet.content.splitlines()):
        line_number = snippet.start_line + offset
        if REDACTION_PLACEHOLDER in line or PRIVATE_KEY_PLACEHOLDER in line:
            continue
        assert line == real_lines[line_number - 1], f"line {line_number} drifted"


def test_context_without_secrets_produces_no_warning(tmp_path: Path) -> None:
    _write(tmp_path / "app.py", "def divide(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")

    _, bundle = _build_prompt(tmp_path, "app.py", 2, "divide")

    assert not any("Redacted" in w for w in bundle.warnings)


# ==============================================================================
# A placeholder echoed back by the model must never be written to disk
# ==============================================================================


class _PlaceholderEchoClient:
    def health_check(self):
        return True, "stub runtime ready"

    def generate(self, request, model_name=None):
        from codepilot.core.models.base import ModelResponse

        return ModelResponse(
            content=f'```python\nAPI_KEY = "{REDACTION_PLACEHOLDER}"\nreturn a / b\n```',
            model_name="stub",
            latency_seconds=0.0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            finish_reason="stop",
        )


def test_model_echoing_a_redaction_placeholder_is_refused(tmp_path: Path, monkeypatch) -> None:
    """Applying an echoed placeholder would overwrite a real credential with mask text."""
    import codepilot.core.models.factory as factory

    source = 'API_KEY = "' + OPENAI_KEY + '"\n\n\ndef divide(a, b):\n    # AI-FIX: guard zero\n    return a / b\n'
    _write(tmp_path / "m.py", source)
    monkeypatch.setattr(
        factory, "create_model_client", lambda **kwargs: _PlaceholderEchoClient()
    )

    with pytest.raises(FixError, match="redaction placeholder"):
        generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    assert (tmp_path / "m.py").read_text(encoding="utf-8") == source


def test_real_secret_is_never_replaced_by_a_placeholder_on_disk(
    tmp_path: Path, monkeypatch
) -> None:
    """A normal fix on a file holding a secret must leave the secret byte-identical."""
    import codepilot.core.models.factory as factory

    class _GoodClient:
        def health_check(self):
            return True, "ok"

        def generate(self, request, model_name=None):
            from codepilot.core.models.base import ModelResponse

            return ModelResponse(
                content="```python\nif b == 0:\n    return 0\nreturn a / b\n```",
                model_name="stub",
                latency_seconds=0.0,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                finish_reason="stop",
            )

    source = 'API_KEY = "' + OPENAI_KEY + '"\n\n\ndef divide(a, b):\n    # AI-FIX: guard zero\n    return a / b\n'
    _write(tmp_path / "m.py", source)
    monkeypatch.setattr(factory, "create_model_client", lambda **kwargs: _GoodClient())

    bundle = generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    proposed = bundle.file_patches[0].proposed_content
    assert OPENAI_KEY in proposed, "the real credential must be preserved in the patch"
    assert REDACTION_PLACEHOLDER not in proposed
