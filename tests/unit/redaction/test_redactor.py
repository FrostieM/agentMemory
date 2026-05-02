from __future__ import annotations

from agent_memory_lite.redaction import REDACTION_MARKER_PREFIX, redact


def test_empty_string_passes_through() -> None:
    out = redact("")
    assert out.text == ""
    assert out.spans == []
    assert out.kinds_seen == []


def test_plain_text_unchanged() -> None:
    out = redact("Hello, world. The agent runs locally.")
    assert out.text == "Hello, world. The agent runs locally."
    assert out.kinds_seen == []


def test_openai_key_redacted() -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"
    out = redact(f"key={secret} done")
    assert secret not in out.text
    assert "openai_key" in out.kinds_seen or "api_key_kv" in out.kinds_seen


def test_anthropic_key_redacted() -> None:
    secret = "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"
    out = redact(f"the value is {secret}.")
    assert secret not in out.text
    assert "anthropic_key" in out.kinds_seen


def test_github_pat_redacted() -> None:
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    out = redact(f"token={secret}")
    assert secret not in out.text


def test_jwt_redacted() -> None:
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    out = redact(f"Bearer issued: {jwt}")
    assert jwt not in out.text
    assert "jwt" in out.kinds_seen


def test_password_keyword_redacted() -> None:
    out = redact("Config: password=hunter2 timeout=10")
    assert "hunter2" not in out.text
    assert "password_kv" in out.kinds_seen


def test_authorization_header_redacted() -> None:
    out = redact("Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz01234567")
    assert "ghp_" not in out.text
    assert any(k in out.kinds_seen for k in ("bearer_header", "github_pat"))


def test_db_url_password_redacted() -> None:
    out = redact("DATABASE_URL=postgres://app:hunter2@db.local:5432/main")
    assert "hunter2" not in out.text
    assert "db_url_password" in out.kinds_seen


def test_private_key_block_redacted() -> None:
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1example...redacted_body\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = redact(text)
    assert "1example" not in out.text
    assert "private_key_block" in out.kinds_seen


def test_pii_email_only_redacted_when_opted_in() -> None:
    out_default = redact("contact: test@example.com")
    out_pii = redact("contact: test@example.com", include_pii=True)
    assert "test@example.com" in out_default.text
    assert "test@example.com" not in out_pii.text


def test_redaction_marker_format() -> None:
    out = redact("token=ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    assert REDACTION_MARKER_PREFIX in out.text


def test_spans_reference_original_offsets() -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz01234567"
    text = f"prefix {secret} suffix"
    out = redact(text)
    assert len(out.spans) == 1
    span = out.spans[0]
    assert text[span.start : span.end] == secret


def test_multiple_kinds_accumulate() -> None:
    text = (
        "password=hunter2; token=ghp_abcdefghijklmnopqrstuvwxyz01234567; "
        "api_key=sk-abcdefghijklmnopqrstuvwxyz0123456789"
    )
    out = redact(text)
    assert "hunter2" not in out.text
    assert "ghp_" not in out.text
    assert "sk-" not in out.text
