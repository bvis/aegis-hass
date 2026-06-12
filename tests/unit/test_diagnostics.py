"""Tests for diagnostics support."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.aegis_ajax.api.models import BatteryInfo, Device, Space
from custom_components.aegis_ajax.const import ConnectionStatus, DeviceState, SecurityState
from custom_components.aegis_ajax.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)


def _make_space(sid: str = "space-1") -> Space:
    return Space(
        id=sid,
        hub_id="hub-1",
        name="Home",
        security_state=SecurityState.DISARMED,
        connection_status=ConnectionStatus.ONLINE,
        malfunctions_count=0,
    )


def _make_device(
    did: str = "dev-1", malfunctions: int = 0, battery: BatteryInfo | None = None
) -> Device:
    return Device(
        id=did,
        hub_id="hub-1",
        name="Front Door",
        device_type="door_protect",
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=malfunctions,
        bypassed=False,
        statuses={"door_opened": True},
        battery=battery,
    )


class TestToRedact:
    def test_password_is_redacted(self) -> None:
        assert "password" in TO_REDACT

    def test_email_is_redacted(self) -> None:
        assert "email" in TO_REDACT

    def test_session_token_is_redacted(self) -> None:
        assert "session_token" in TO_REDACT

    def test_password_hash_is_redacted(self) -> None:
        assert "password_hash" in TO_REDACT

    def test_push_token_is_redacted(self) -> None:
        assert "push_token" in TO_REDACT


class TestAsyncGetConfigEntryDiagnostics:
    @pytest.fixture
    def coordinator(self) -> MagicMock:
        coord = MagicMock()
        coord.spaces = {"space-1": _make_space()}
        coord.devices = {"dev-1": _make_device()}
        coord._stream_tasks = [MagicMock(), MagicMock()]
        coord.notification_listener = MagicMock()
        return coord

    @pytest.fixture
    def entry(self, coordinator: MagicMock) -> MagicMock:
        e = MagicMock()
        e.runtime_data = coordinator
        e.data = {
            "email": "user@example.com",
            "password": "secret",
            "spaces": ["space-1"],
        }
        return e

    @pytest.mark.asyncio
    async def test_returns_dict(self, entry: MagicMock) -> None:
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_entry_data_present(self, entry: MagicMock) -> None:
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert "entry_data" in result

    @pytest.mark.asyncio
    async def test_sensitive_data_redacted(self, entry: MagicMock) -> None:
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        entry_data = result["entry_data"]
        assert entry_data.get("email") != "user@example.com"
        assert entry_data.get("password") != "secret"

    @pytest.mark.asyncio
    async def test_spaces_included(self, entry: MagicMock) -> None:
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert "space-1" in result["spaces"]
        space_info = result["spaces"]["space-1"]
        assert space_info["name"] == "Home"
        assert space_info["online"] is True
        assert space_info["malfunctions"] == 0

    @pytest.mark.asyncio
    async def test_devices_included(self, entry: MagicMock) -> None:
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert "dev-1" in result["devices"]
        dev_info = result["devices"]["dev-1"]
        assert dev_info["name"] == "Front Door"
        assert dev_info["type"] == "door_protect"
        assert dev_info["online"] is True
        assert dev_info["malfunctions"] == 0
        assert dev_info["bypassed"] is False
        assert dev_info["battery"] is None
        assert "door_opened" in dev_info["statuses"]

    @pytest.mark.asyncio
    async def test_device_with_battery(self, entry: MagicMock) -> None:
        battery = BatteryInfo(level=85, is_low=False)
        entry.runtime_data.devices = {"dev-1": _make_device(battery=battery)}
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        bat = result["devices"]["dev-1"]["battery"]
        assert bat is not None
        assert bat["level"] == 85
        assert bat["low"] is False

    @pytest.mark.asyncio
    async def test_video_device_includes_raw_type_and_sources(self, entry: MagicMock) -> None:
        # #282/#290: the raw `About.Type` value and the source list are
        # what diagnostics-driven triage of duplicated / unknown video
        # devices runs on — they must survive into the dump (the
        # `statuses` block only lists keys, not values).
        from dataclasses import replace

        device = replace(
            _make_device(did="cam-1"),
            statuses={
                "video_edge_type": 7,
                "video_sources": [
                    {"kind": "nvr", "video_edge_id": "ve-nvr", "channel_id": "3", "type": 7}
                ],
            },
        )
        entry.runtime_data.devices = {"cam-1": device}
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        dev_info = result["devices"]["cam-1"]
        assert dev_info["video_edge_type"] == 7
        assert dev_info["video_sources"] == [
            {"kind": "nvr", "video_edge_id": "ve-nvr", "channel_id": "3", "type": 7}
        ]

    @pytest.mark.asyncio
    async def test_non_video_device_omits_video_keys(self, entry: MagicMock) -> None:
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        dev_info = result["devices"]["dev-1"]
        assert "video_edge_type" not in dev_info
        assert "video_sources" not in dev_info

    @pytest.mark.asyncio
    async def test_stream_tasks_count(self, entry: MagicMock) -> None:
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["stream_tasks"] == 2

    @pytest.mark.asyncio
    async def test_notification_listener_true_when_present(self, entry: MagicMock) -> None:
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["notification_listener"] is True

    @pytest.mark.asyncio
    async def test_notification_listener_false_when_absent(self, entry: MagicMock) -> None:
        entry.runtime_data.notification_listener = None
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["notification_listener"] is False

    @pytest.mark.asyncio
    async def test_spaces_include_groups_when_present(self, entry: MagicMock) -> None:
        # The diagnostics block must expose `groups` + `group_mode_enabled`
        # so support requests for group-related issues (#148) can be
        # diagnosed without re-asking for a custom log. Previously the
        # serializer omitted both fields and a missing block was
        # indistinguishable from an actually-empty `space.groups`.
        from dataclasses import replace

        from custom_components.aegis_ajax.api.models import Group

        groups = (
            Group(
                id="g1",
                space_id="space-1",
                name="Home",
                security_state=SecurityState.ARMED,
                sorting_key="01",
            ),
            Group(
                id="g2",
                space_id="space-1",
                name="Studio",
                security_state=SecurityState.DISARMED,
                sorting_key="02",
            ),
        )
        entry.runtime_data.spaces = {
            "space-1": replace(
                _make_space(), groups=groups, group_mode_enabled=True, night_mode_enabled=True
            )
        }

        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        space_info = result["spaces"]["space-1"]
        assert space_info["group_mode_enabled"] is True
        # Drives the panel's armed_night-vs-custom_bypass discrimination (#284),
        # so it must be dumped — a missing key must mean "stale integration".
        assert space_info["night_mode_enabled"] is True
        assert len(space_info["groups"]) == 2
        assert space_info["groups"][0] == {
            "id": "g1",
            "name": "Home",
            "security_state": "ARMED",
        }
        assert space_info["groups"][1] == {
            "id": "g2",
            "name": "Studio",
            "security_state": "DISARMED",
        }

    @pytest.mark.asyncio
    async def test_spaces_include_empty_groups_when_not_in_group_mode(
        self, entry: MagicMock
    ) -> None:
        # When the space isn't in group mode, the block still emits the
        # fields (with empty list / false) so a missing block reliably
        # means "stale integration without the diagnostics fix" rather
        # than "no groups configured".
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        space_info = result["spaces"]["space-1"]
        assert space_info["group_mode_enabled"] is False
        assert space_info["night_mode_enabled"] is False
        assert space_info["groups"] == []
