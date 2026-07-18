from mc_panel_api.redaction import redact, redact_text


def test_redacts_secrets_and_network_identifiers() -> None:
    value = (
        "api_key=sk-example-secret-123456789 password=hunter2 host=203.0.113.9"  # gitleaks:allow
    )
    redacted = redact_text(value)
    assert "sk-example" not in redacted
    assert "hunter2" not in redacted
    assert "203.0.113.9" not in redacted


def test_redacts_nested_secret_keys() -> None:
    data = redact({"safe": "ok", "token": "secret", "nested": {"password": "secret"}})
    assert data == {
        "safe": "ok",
        "token": "[REDACTED]",
        "nested": {"password": "[REDACTED]"},
    }


def test_redacts_ipv6_and_url_credentials() -> None:
    value = "connect 2001:db8:85a3::8a2e:370:7334 via https://admin:secret@example.test/api"
    redacted = redact_text(value)
    assert "2001:db8" not in redacted
    assert "admin:secret" not in redacted
