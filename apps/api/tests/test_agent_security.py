from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from mc_panel_api.adapters.mock import MockMinecraftAdapter
from mc_panel_api.agent import AgentError, AgentService
from mc_panel_api.config import Settings
from mc_panel_api.database import Database
from mc_panel_api.tools import ToolRegistry


def _service(settings: Settings) -> AgentService:
    configured = replace(
        settings,
        ai_enabled=True,
        ai_api_base_url="https://provider.example/v1",
        ai_api_key="test-provider-key",
        ai_model="test-model",
    )
    database = Database(configured.database_path)
    database.migrate()
    adapter = MockMinecraftAdapter(configured.mock_root)
    return AgentService(configured, database, adapter, ToolRegistry(adapter))


def test_user_secrets_are_redacted_before_the_provider_call(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(settings)
    calls: list[list[dict[str, Any]]] = []

    def fake_call(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        calls.append(messages)
        provider_message = (
            "请不要显示 203.0.113.9 或 "
            "sk-example-secret-123456789"  # gitleaks:allow
        )
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": provider_message,
                    }
                }
            ]
        }

    monkeypatch.setattr(service, "_call", fake_call)
    user_message = (
        "password=hunter2 "  # gitleaks:allow
        "api_key=sk-user-secret-123456789 "  # gitleaks:allow
        "host=198.51.100.4"
    )
    result = service.chat(user_message)

    sent = str(calls[0][-1]["content"])
    assert "hunter2" not in sent
    assert "sk-user" not in sent
    assert "198.51.100.4" not in sent
    assert "203.0.113.9" not in result["answer"]
    assert "sk-example" not in result["answer"]


def test_provider_network_errors_are_normalized(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(settings)

    def fail_post(*args: Any, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.Client, "post", fail_post)
    with pytest.raises(AgentError) as error:
        service._call([{"role": "user", "content": "status"}], [])
    assert error.value.code == "provider_unavailable"


def test_provider_authentication_errors_are_actionable_without_exposing_response(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(settings)

    def reject_post(*args: Any, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
        return httpx.Response(401, request=request, json={"error": {"message": "secret"}})

    monkeypatch.setattr(httpx.Client, "post", reject_post)
    with pytest.raises(AgentError) as error:
        service._call([{"role": "user", "content": "status"}], [])
    assert error.value.code == "provider_auth_failed"
    assert "secret" not in error.value.message


def test_daily_request_limit_is_applied_to_each_provider_call(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(settings)
    service.update_settings({"requests_per_day": 1})
    calls = 0

    def tool_response(*args: Any, **kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_status",
                                    "type": "function",
                                    "function": {
                                        "name": "get_server_status",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    monkeypatch.setattr(httpx.Client, "post", tool_response)

    with pytest.raises(AgentError) as error:
        service.chat("帮我看看服务器状态")

    assert error.value.code == "ai_budget_exceeded"
    assert calls == 1
    assert service.usage()["request_count"] == 1


def test_token_limit_reserves_maximum_output_before_call(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(settings)
    service.update_settings({"tokens_per_day": 1000, "max_tokens": 800})
    service.database.execute(
        """
        INSERT INTO ai_usage(day, request_count, input_tokens, output_tokens)
        VALUES (date('now'), 1, 150, 100)
        """
    )
    monkeypatch.setattr(
        httpx.Client,
        "post",
        lambda *args, **kwargs: pytest.fail("provider call exceeded the configured token budget"),
    )

    with pytest.raises(AgentError) as error:
        service._call([{"role": "user", "content": "status"}], [])

    assert error.value.code == "ai_budget_exceeded"


def test_status_only_exposes_provider_origin(settings: Settings) -> None:
    service = _service(settings)
    assert service.status()["api_base_url"] == "https://provider.example"


def test_environment_rejects_unsafe_provider_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MC_PANEL_AI_API_BASE_URL", "https://user:secret@provider.example/v1")
    with pytest.raises(ValueError, match="without credentials"):
        Settings.from_env()


def test_agent_read_tools_are_bounded_and_reject_extra_arguments(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(settings)
    mods = [
        {
            "id": f"mod_{index:04d}",
            "name": f"Example {index}",
            "version": "unknown",
            "loader": "Fabric",
            "filename": f"example-{index}.jar",
            "enabled": True,
            "duplicate": index < 3,
        }
        for index in range(80)
    ]
    long_lines = [f"[Server thread/ERROR] {index} " + ("x" * 5_000) for index in range(120)]
    monkeypatch.setattr(service.adapter, "list_mods", lambda: mods)
    monkeypatch.setattr(
        service.adapter,
        "read_logs",
        lambda **kwargs: {"source": "latest", "lines": long_lines, "next_cursor": None},
    )

    mod_summary = service._read_tool("list_mods", {})
    assert mod_summary["total"] == 80
    assert len(mod_summary["items"]) == 60
    assert mod_summary["items_truncated"] is True

    compatibility = service._read_tool("check_mod_compatibility", {})
    assert compatibility["suspicious_item_count"] == 80
    assert len(compatibility["suspicious_items"]) == 25
    assert compatibility["suspicious_items_truncated"] is True
    compact_lines = compatibility["recent_log_signals"]["lines"]
    assert sum(len(line) for line in compact_lines) <= 24_000
    assert all(len(line) <= 1_200 for line in compact_lines)
    assert compatibility["recent_log_signals"]["truncated"] is True

    with pytest.raises(ValueError, match="unexpected arguments"):
        service._read_tool("get_server_status", {"command": "whoami"})
    with pytest.raises(ValueError, match="must be an integer"):
        service._read_tool("read_recent_logs", {"lines": True})

    activity = service._read_tool("get_player_activity", {"days": 7})
    assert activity["days"] == 7
    assert len(activity["daily"]) == 7
    with pytest.raises(ValueError, match="out of range"):
        service._read_tool("get_player_activity", {"days": 31})


def test_agent_can_read_latest_crash_and_recent_audit(settings: Settings) -> None:
    service = _service(settings)
    service.database.add_audit(
        event_id="audit_test_agent",
        user_id=None,
        action="create_backup",
        risk="low",
        outcome="succeeded",
        request_id="request_test_agent",
        confirmation_id=None,
        params_summary={},
        result_summary={"verified": True},
    )

    crash = service._read_tool("read_crash_report", {})
    assert crash["id"] == "crash_20260615_201528"
    operations = service._read_tool("read_recent_operations", {"limit": 1})
    assert operations["count"] == 1
    assert operations["events"][0]["action"] == "create_backup"
    assert operations["events"][0]["result_summary"] == {"verified": True}


def test_agent_write_tool_calls_only_create_proposals(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(settings)
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_restart",
                                "type": "function",
                                "function": {"name": "restart_server", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"role": "assistant", "content": "等待你的确认。"}}]},
    ]

    def fake_call(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        return responses.pop(0)

    def fail_execute(action: str, params: dict[str, Any]) -> dict[str, Any]:
        pytest.fail(f"write tool executed unexpectedly: {action} {json.dumps(params)}")

    monkeypatch.setattr(service, "_call", fake_call)
    monkeypatch.setattr(service.adapter, "execute", fail_execute)
    result = service.chat("帮我重启服务器")

    assert result["answer"] == "等待你的确认。"
    assert len(result["proposed_actions"]) == 1
    assert result["proposed_actions"][0]["action"] == "restart_server"
    assert result["proposed_actions"][0]["requires_confirmation"] is True
