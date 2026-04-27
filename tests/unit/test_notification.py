"""Tests for FCM notification listener."""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.notification import AjaxNotificationListener

_FCM_KWARGS = {
    "fcm_project_id": "test-project",
    "fcm_app_id": "test-app",
    "fcm_api_key": "test-key",
    "fcm_sender_id": "12345",
}

# Real ENCODED_DATA from a photo capture push notification (base64)
_REAL_PUSH_ENCODED_DATA = (
    "Cu0CCkA0ODQyNTM2NjYyOTE1NjAwQUFCQjExMjIzMzQ0NTU2Njc3ODg5OTAwQTFCMkMzRDQw"
    "MDAwMDE5RDg4NTdEODlFEhgwMDAwMDE5ZDg4NTdkODllN2M0Yzg3ZDQaMQoYYWFiYjExMjIz"
    "MzQ0NTU2Njc3ODg5OTAwEhVIMlBMVVMgLSBDQVJMT1MgTE9QRVoiDAiXi/XOBhCA+8bSAigE"
    "MAI6ZApiCicKCEU1RjZBN0I4EhVIMlBMVVMgLSBDQVJMT1MgTE9QRVoYASAKKAESCQoDogMA"
    "EgIKABosCCcSCEExQjJDM0Q0GglWRVNUSUJVTE8gASgBqgEIMDAwMDAwMDGyAQNIQUxAAaoB"
    "L9oGLAoOSG9tZSBBc3Npc3RhbnQSAggBGhYKFCIIQzlEMEUxRjIiCEYzRTRENUM2qgEfwgYc"
    "CAwSCEM5RDBFMUYyGg5Ib21lIEFzc2lzdGFudKoBDaIHCgoGCJWL9c4GEAE="
)

_EXPECTED_NOTIFICATION_ID = "4842536662915600AABB11223344556677889900A1B2C3D40000019D8857D89E"


class TestNotificationListener:
    def test_init(self) -> None:
        hass = MagicMock()
        coordinator = MagicMock()
        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)
        assert listener._coordinator is coordinator
        assert listener._push_client is None
        assert listener._photo_callbacks == {}
        assert listener._notification_id_callbacks == {}
        assert listener._last_notification_id is None

    @pytest.mark.asyncio
    async def test_on_notification_triggers_refresh(self) -> None:
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        coordinator = MagicMock()
        coordinator.async_request_refresh = AsyncMock()

        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)
        listener._on_notification({"data": "test"}, "persistent-1")

        hass.loop.call_soon_threadsafe.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_notification_extracts_photo_url(self) -> None:
        """ENCODED_DATA with an HTTPS URL resolves pending photo futures."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        coordinator = MagicMock()
        coordinator.async_request_refresh = AsyncMock()

        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

        # Create a pending future
        loop = asyncio.get_event_loop()
        future: asyncio.Future[str | None] = loop.create_future()
        listener._photo_callbacks["dev-1"] = future

        # Build a fake ENCODED_DATA containing an HTTPS URL
        raw_bytes = b"\x08\x01" + b"https://app.prod.ajax.systems/photo/test.jpg" + b"\x00"
        encoded = base64.b64encode(raw_bytes).decode()

        listener._on_notification({"ENCODED_DATA": encoded}, "persistent-2")

        assert future.done()
        assert future.result() == "https://app.prod.ajax.systems/photo/test.jpg"
        assert listener._photo_callbacks == {}

    @pytest.mark.asyncio
    async def test_on_notification_bad_encoded_data_does_not_raise(self) -> None:
        """Invalid ENCODED_DATA is silently ignored."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        coordinator = MagicMock()
        coordinator.async_request_refresh = AsyncMock()

        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)
        listener._on_notification({"ENCODED_DATA": "not-valid-base64!!!"}, "persistent-3")
        # Should not raise
        hass.loop.call_soon_threadsafe.assert_called_once()

    @pytest.mark.asyncio
    async def test_wait_for_photo_url_resolved_by_push(self) -> None:
        """wait_for_photo_url returns URL when push arrives."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        coordinator = MagicMock()
        coordinator.async_request_refresh = AsyncMock()

        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

        raw_bytes = b"https://app.prod.ajax.systems/photo/cam.jpg"
        encoded = base64.b64encode(raw_bytes).decode()

        async def _trigger_push() -> None:
            await asyncio.sleep(0)
            listener._on_notification({"ENCODED_DATA": encoded}, "pid-1")

        asyncio.ensure_future(_trigger_push())
        result = await listener.wait_for_photo_url("dev-1", timeout=2.0)
        assert result == "https://app.prod.ajax.systems/photo/cam.jpg"

    @pytest.mark.asyncio
    async def test_wait_for_photo_url_timeout(self) -> None:
        """wait_for_photo_url returns None on timeout."""
        hass = MagicMock()
        coordinator = MagicMock()
        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

        result = await listener.wait_for_photo_url("dev-99", timeout=0.05)
        assert result is None
        assert "dev-99" not in listener._photo_callbacks

    @pytest.mark.asyncio
    async def test_stop_when_no_client(self) -> None:
        hass = MagicMock()
        coordinator = MagicMock()
        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)
        await listener.async_stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_start_without_firebase_messaging(self) -> None:
        hass = MagicMock()
        coordinator = MagicMock()
        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

        with patch.dict("sys.modules", {"firebase_messaging": None}):
            await listener.async_start()

        assert listener._push_client is None

    @pytest.mark.asyncio
    async def test_stop_with_client(self) -> None:
        hass = MagicMock()
        coordinator = MagicMock()
        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)
        mock_client = MagicMock()
        mock_client.stop.return_value = None
        listener._push_client = mock_client

        await listener.async_stop()

        mock_client.stop.assert_called_once()
        assert listener._push_client is None

    def test_extract_notification_id_from_real_push(self) -> None:
        """Extract notification_id from real push data."""
        result = AjaxNotificationListener.extract_notification_id(_REAL_PUSH_ENCODED_DATA)
        assert result is not None
        assert len(result) == 64
        assert result == _EXPECTED_NOTIFICATION_ID

    def test_extract_notification_id_returns_none_for_invalid_data(self) -> None:
        """Invalid base64 returns None."""
        result = AjaxNotificationListener.extract_notification_id("not-valid!!!")
        assert result is None

    def test_extract_notification_id_returns_none_for_no_hex_match(self) -> None:
        """Data without a 64-char hex string returns None."""
        encoded = base64.b64encode(b"short data without hex ids").decode()
        result = AjaxNotificationListener.extract_notification_id(encoded)
        assert result is None

    def test_extract_source_from_real_push(self) -> None:
        """Extract device source info from real push notification data."""
        raw = base64.b64decode(_REAL_PUSH_ENCODED_DATA)
        result = AjaxNotificationListener._extract_source_info(raw)
        assert result is not None
        assert result["device_name"] == "VESTIBULO"
        assert result["device_id"] == "A1B2C3D4"
        assert result["device_type"] == "MOTION_CAM_PHOD"

    def test_extract_source_returns_empty_for_garbage(self) -> None:
        """Garbage data returns empty dict."""
        result = AjaxNotificationListener._extract_source_info(b"\x00\x01\x02\x03")
        assert result == {}

    def test_extract_source_returns_empty_for_no_name(self) -> None:
        """Source without name returns empty dict (hub-level events)."""
        # Minimal valid protobuf with only type field (field 1, varint 1 = HUB)
        raw = b"\x08\x01"
        result = AjaxNotificationListener._extract_source_info(raw)
        assert result == {}

    @pytest.mark.asyncio
    async def test_on_notification_extracts_notification_id(self) -> None:
        """ENCODED_DATA with a notification_id resolves pending notification_id futures."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        coordinator = MagicMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._space_ids = []

        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

        loop = asyncio.get_event_loop()
        future: asyncio.Future[str | None] = loop.create_future()
        listener._notification_id_callbacks["A1B2C3D4"] = future

        # Real encoded data containing a 64-char hex notification ID
        listener._on_notification({"ENCODED_DATA": _REAL_PUSH_ENCODED_DATA}, "persistent-n1")

        assert future.done()
        assert future.result() == _EXPECTED_NOTIFICATION_ID
        assert listener._notification_id_callbacks == {}
        assert listener._last_notification_id == _EXPECTED_NOTIFICATION_ID

    @pytest.mark.asyncio
    async def test_wait_for_notification_id_timeout(self) -> None:
        """wait_for_notification_id returns None on timeout."""
        hass = MagicMock()
        coordinator = MagicMock()
        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

        result = await listener.wait_for_notification_id("dev-99", timeout=0.05)
        assert result is None
        assert "dev-99" not in listener._notification_id_callbacks


class TestApplySecurityStateFromEvent:
    """Issue #68: arm/disarm pushes update space security_state instantly."""

    def _make_listener(self) -> tuple[AjaxNotificationListener, MagicMock, MagicMock]:
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        coordinator = MagicMock()
        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)
        return listener, hass, coordinator

    def test_arm_tag_dispatches_armed_state(self) -> None:
        from custom_components.aegis_ajax.const import SecurityState

        listener, hass, coordinator = self._make_listener()

        listener._apply_security_state_from_event("space-1", {"raw_tag": "arm"})

        hass.loop.call_soon_threadsafe.assert_called_once_with(
            coordinator.apply_push_security_state, "space-1", SecurityState.ARMED
        )

    def test_disarm_tag_dispatches_disarmed_state(self) -> None:
        from custom_components.aegis_ajax.const import SecurityState

        listener, hass, coordinator = self._make_listener()

        listener._apply_security_state_from_event("space-1", {"raw_tag": "disarm"})

        hass.loop.call_soon_threadsafe.assert_called_once_with(
            coordinator.apply_push_security_state, "space-1", SecurityState.DISARMED
        )

    def test_night_mode_on_dispatches_night_mode_state(self) -> None:
        from custom_components.aegis_ajax.const import SecurityState

        listener, hass, coordinator = self._make_listener()

        listener._apply_security_state_from_event("space-1", {"raw_tag": "night_mode_on"})

        hass.loop.call_soon_threadsafe.assert_called_once_with(
            coordinator.apply_push_security_state, "space-1", SecurityState.NIGHT_MODE
        )

    def test_group_arm_tag_does_not_dispatch(self) -> None:
        # group_* tags only affect a subgroup; let the next poll resolve the
        # space-level state instead of guessing it from the push.
        listener, hass, _ = self._make_listener()

        listener._apply_security_state_from_event("space-1", {"raw_tag": "group_arm"})

        hass.loop.call_soon_threadsafe.assert_not_called()

    def test_unmapped_tag_does_not_dispatch(self) -> None:
        listener, hass, _ = self._make_listener()

        listener._apply_security_state_from_event("space-1", {"raw_tag": "intrusion_alarm"})

        hass.loop.call_soon_threadsafe.assert_not_called()

    def test_missing_raw_tag_does_not_dispatch(self) -> None:
        listener, hass, _ = self._make_listener()

        listener._apply_security_state_from_event("space-1", {})

        hass.loop.call_soon_threadsafe.assert_not_called()

    def test_no_dispatch_when_loop_not_running(self) -> None:
        listener, hass, _ = self._make_listener()
        hass.loop.is_running.return_value = False

        listener._apply_security_state_from_event("space-1", {"raw_tag": "arm"})

        hass.loop.call_soon_threadsafe.assert_not_called()
