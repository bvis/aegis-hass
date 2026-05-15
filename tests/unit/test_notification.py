"""Tests for FCM notification listener."""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.notification import (
    AjaxNotificationListener,
    _classify_fcm_failure,
)

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


class TestAsyncStartFcmRepairs:
    """The FCM listener raises a Repair when registration / push start fails."""

    @pytest.mark.asyncio
    async def test_not_configured_repair_raised_when_fcm_unconfigured(self) -> None:
        """No api_key → raise `fcm_not_configured` repair so the user gets a
        visible nudge to enter keys via the Repair card, plus a WARNING log
        line (instead of the previous silent INFO). `fcm_credentials_invalid`
        is left alone — it's a different state (keys present but rejected)."""
        hass = MagicMock()
        coordinator = MagicMock()
        listener = AjaxNotificationListener(
            hass=hass,
            coordinator=coordinator,
            fcm_project_id="",
            fcm_app_id="",
            fcm_api_key="",
            fcm_sender_id="",
            entry_id="entry-x",
        )

        with (
            patch(
                "custom_components.aegis_ajax.notification.async_register_fcm_credentials_invalid"
            ) as reg_invalid,
            patch(
                "custom_components.aegis_ajax.notification.async_clear_fcm_credentials_invalid"
            ) as clr_invalid,
            patch(
                "custom_components.aegis_ajax.notification.async_register_fcm_not_configured"
            ) as reg_missing,
            patch(
                "custom_components.aegis_ajax.notification.async_clear_fcm_not_configured"
            ) as clr_missing,
        ):
            await listener.async_start()

        reg_invalid.assert_not_called()
        clr_invalid.assert_called_once_with(hass, entry_id="entry-x")
        # Cleared once at the top of async_start (start with a clean slate)
        # then re-registered after the missing-api-key check fires.
        clr_missing.assert_called_once_with(hass, entry_id="entry-x")
        reg_missing.assert_called_once_with(hass, entry_id="entry-x")

    @pytest.mark.asyncio
    async def test_register_failure_raises_repair(self) -> None:
        """firebase_messaging.register() throwing → repair raised, listener returns gracefully."""
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=RuntimeError("boom"))
        coordinator = MagicMock()
        listener = AjaxNotificationListener(
            hass=hass, coordinator=coordinator, **_FCM_KWARGS, entry_id="entry-x"
        )
        listener._store.async_load = AsyncMock(return_value=None)

        register_cls = MagicMock()
        instance = MagicMock()
        instance.register = MagicMock(side_effect=RuntimeError("boom"))
        register_cls.return_value = instance

        with (
            patch(
                "custom_components.aegis_ajax.notification.async_register_fcm_credentials_invalid"
            ) as reg,
            patch("custom_components.aegis_ajax.notification.async_clear_fcm_credentials_invalid"),
            patch("custom_components.aegis_ajax.notification.async_register_fcm_not_configured"),
            patch("custom_components.aegis_ajax.notification.async_clear_fcm_not_configured"),
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
        ):
            await listener.async_start()

        reg.assert_called_once_with(hass, entry_id="entry-x")


class TestClassifyFcmFailure:
    """`_classify_fcm_failure` turns library errors into actionable WARNINGs.

    Each branch corresponds to one of the three literal `RuntimeError` strings
    `firebase-messaging` 0.4.5 actually emits. The mapping was verified by an
    empirical probe (deliberate credential corruptions + DNS block of FCM
    hosts) — not by reading the library source — so the substrings here are
    guaranteed to be the ones the listener observes in production.
    """

    def test_gcm_subscription_rejection_points_at_project_consistency(self) -> None:
        # Probe result: emitted for ANY credential-set error (bad sender_id,
        # api_key with or without AIza prefix, project_id, app_id with valid
        # shape). Hansontech190's case (#131) lands here. Message must steer
        # the user toward checking the four-value consistency, not toward
        # extraction internals (no APK / cobrand / .so wording).
        msg = _classify_fcm_failure(
            RuntimeError("Unable to establish subscription with Google Cloud Messaging.")
        )
        assert "rejected by Google" in msg
        assert "same Firebase project" in msg
        assert "fcm_sender_id" in msg and "fcm_app_id" in msg and "fcm_project_id" in msg
        for forbidden in ("APK", "cobrand", "libnative", "strings.xml"):
            assert forbidden not in msg

    def test_fcm_install_failure_points_at_app_id_format(self) -> None:
        # Probe result: emitted when the Firebase Installation API rejects the
        # request with HTTP 400 INVALID_ARGUMENT, which empirically only fires
        # when `fcm_app_id` is malformed enough that Firebase cannot parse it.
        # Other shapes (bad api_key, wrong project_id) surface as the
        # subscription branch above.
        msg = _classify_fcm_failure(RuntimeError("Unable to register with fcm"))
        assert "fcm_app_id" in msg
        assert "1:" in msg and "sender" in msg  # the format hint
        assert "Repair card" in msg

    def test_gcm_checkin_failure_points_at_network(self) -> None:
        # Probe result: emitted exclusively on network failure (DNS / firewall
        # / FCM hosts unreachable). The four credentials are not used by the
        # GCM checkin step, so this string is an unambiguous network signal.
        msg = _classify_fcm_failure(RuntimeError("Unable to register and check in to gcm"))
        assert "reach Google FCM servers" in msg
        # Both FCM hosts must be named so the user knows exactly what to
        # whitelist in their firewall / DNS. The full slash-separated pair
        # is asserted as a single substring so CodeQL's URL-sanitization
        # heuristic doesn't misread this as a partial-URL match guard.
        assert "android.clients.google.com / firebaseinstallations.googleapis.com" in msg
        assert "firewall" in msg or "DNS" in msg

    def test_unknown_error_falls_back_to_generic_with_message(self) -> None:
        # Future-proofing: if firebase-messaging changes its error strings or
        # a different exception slips through (aiohttp leak, etc.), preserve
        # the original message so a human reading the log can still diagnose.
        msg = _classify_fcm_failure(RuntimeError("something completely unexpected"))
        assert msg.startswith("FCM registration failed")
        assert "something completely unexpected" in msg

    def test_empty_exception_message_still_returns_a_string(self) -> None:
        # Some library paths raise bare RuntimeError() with no message.
        # Don't crash the listener; surface the class name instead.
        msg = _classify_fcm_failure(RuntimeError())
        assert "FCM registration failed" in msg
        assert "RuntimeError" in msg


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

    def test_space_armed_tag_dispatches_armed_state(self) -> None:
        from custom_components.aegis_ajax.const import SecurityState

        listener, hass, coordinator = self._make_listener()

        listener._apply_security_state_from_event("space-1", {"raw_tag": "space_armed"})

        hass.loop.call_soon_threadsafe.assert_called_once_with(
            coordinator.apply_push_security_state, "space-1", SecurityState.ARMED
        )

    def test_space_disarmed_tag_dispatches_disarmed_state(self) -> None:
        from custom_components.aegis_ajax.const import SecurityState

        listener, hass, coordinator = self._make_listener()

        listener._apply_security_state_from_event("space-1", {"raw_tag": "space_disarmed"})

        hass.loop.call_soon_threadsafe.assert_called_once_with(
            coordinator.apply_push_security_state, "space-1", SecurityState.DISARMED
        )

    def test_space_night_mode_on_dispatches_night_mode_state(self) -> None:
        from custom_components.aegis_ajax.const import SecurityState

        listener, hass, coordinator = self._make_listener()

        listener._apply_security_state_from_event("space-1", {"raw_tag": "space_night_mode_on"})

        hass.loop.call_soon_threadsafe.assert_called_once_with(
            coordinator.apply_push_security_state, "space-1", SecurityState.NIGHT_MODE
        )

    def test_space_group_armed_does_not_dispatch(self) -> None:
        # Group-level transitions don't determine the space-level state alone.
        listener, hass, _ = self._make_listener()

        listener._apply_security_state_from_event("space-1", {"raw_tag": "space_group_armed"})

        hass.loop.call_soon_threadsafe.assert_not_called()


class TestExtractEventCompiledProtos:
    """Issue #68: arm/disarm pushes carry a SpaceEventQualifier, not Hub one."""

    def _make_listener(self) -> AjaxNotificationListener:
        hass = MagicMock()
        coordinator = MagicMock()
        return AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

    @staticmethod
    def _wrap(payload: bytes) -> bytes:
        # Embed `payload` as a length-delimited submessage of an outer parent
        # (field 1, wire type 2) so `_find_embedded_messages` surfaces it.
        # `_find_embedded_messages` filters candidates with `4 < length < 500`,
        # so callers must pass payloads of >=5 bytes (qualifier + transition
        # always satisfies that in real FCM data).
        assert len(payload) > 4
        return b"\x0a" + bytes([len(payload)]) + payload

    def test_space_armed_qualifier_resolved_first(self) -> None:
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event import (  # noqa: E501
            transition_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.space import (  # noqa: E501
            qualifier_pb2 as space_qualifier_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.space import (
            tag_pb2 as space_tag_pb2,
        )

        qualifier = space_qualifier_pb2.SpaceEventQualifier(
            tag=space_tag_pb2.SpaceEventTag(space_armed=space_tag_pb2.SpaceArmed()),
            transition=transition_pb2.EventTransition(
                impulse=transition_pb2.EventTransition.Impulse()
            ),
        )
        wrapped = self._wrap(qualifier.SerializeToString())

        listener = self._make_listener()
        result = listener._extract_event_with_compiled_protos(wrapped)

        assert result is not None
        event_type, data = result
        assert event_type == "arm"
        assert data["raw_tag"] == "space_armed"

    def test_space_disarmed_qualifier(self) -> None:
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event import (  # noqa: E501
            transition_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.space import (  # noqa: E501
            qualifier_pb2 as space_qualifier_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.space import (
            tag_pb2 as space_tag_pb2,
        )

        qualifier = space_qualifier_pb2.SpaceEventQualifier(
            tag=space_tag_pb2.SpaceEventTag(space_disarmed=space_tag_pb2.SpaceDisarmed()),
            transition=transition_pb2.EventTransition(
                impulse=transition_pb2.EventTransition.Impulse()
            ),
        )
        wrapped = self._wrap(qualifier.SerializeToString())

        listener = self._make_listener()
        result = listener._extract_event_with_compiled_protos(wrapped)

        assert result is not None
        event_type, data = result
        assert event_type == "disarm"
        assert data["raw_tag"] == "space_disarmed"

    def test_space_qualifier_preferred_over_hub_subincident(self) -> None:
        # When both a SpaceEventQualifier (primary) and a HubEventQualifier
        # (sub-incident, e.g. ext_contact_opened) are present in the same
        # payload, the space-level transition wins.
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event import (  # noqa: E501
            transition_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.hub import (  # noqa: E501
            qualifier_pb2 as hub_qualifier_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.hub import (
            tag_pb2 as hub_tag_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.space import (  # noqa: E501
            qualifier_pb2 as space_qualifier_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.space import (
            tag_pb2 as space_tag_pb2,
        )

        space_q = space_qualifier_pb2.SpaceEventQualifier(
            tag=space_tag_pb2.SpaceEventTag(space_night_mode_on=space_tag_pb2.SpaceNightModeOn()),
            transition=transition_pb2.EventTransition(
                impulse=transition_pb2.EventTransition.Impulse()
            ),
        )
        hub_q = hub_qualifier_pb2.HubEventQualifier(
            tag=hub_tag_pb2.HubEventTag(intrusion_alarm=hub_tag_pb2.IntrusionAlarm()),
            transition=transition_pb2.EventTransition(
                triggered=transition_pb2.EventTransition.Triggered()
            ),
        )
        # Place the hub qualifier BEFORE the space one so the test would fail
        # if we were still picking the first parseable HubEventQualifier.
        wrapped = self._wrap(hub_q.SerializeToString()) + self._wrap(space_q.SerializeToString())

        listener = self._make_listener()
        result = listener._extract_event_with_compiled_protos(wrapped)

        assert result is not None
        event_type, data = result
        assert event_type == "arm_night"
        assert data["raw_tag"] == "space_night_mode_on"

    def test_hub_qualifier_used_when_no_space_qualifier(self) -> None:
        # Hub-level events (alarm, tamper, …) still resolve through the
        # existing HubEventQualifier path.
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event import (  # noqa: E501
            transition_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.hub import (  # noqa: E501
            qualifier_pb2 as hub_qualifier_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.hub import (
            tag_pb2 as hub_tag_pb2,
        )

        qualifier = hub_qualifier_pb2.HubEventQualifier(
            tag=hub_tag_pb2.HubEventTag(intrusion_alarm=hub_tag_pb2.IntrusionAlarm()),
            transition=transition_pb2.EventTransition(
                triggered=transition_pb2.EventTransition.Triggered()
            ),
        )
        wrapped = self._wrap(qualifier.SerializeToString())

        listener = self._make_listener()
        result = listener._extract_event_with_compiled_protos(wrapped)

        assert result is not None
        event_type, data = result
        assert event_type == "alarm"
        assert data["raw_tag"] == "intrusion_alarm"

    def test_hub_ring_button_pressed_resolves_to_doorbell_pressed(self) -> None:
        # Wireless DoorBell (Jeweller standalone ring button paired with the
        # hub) fires `ring_button_pressed` inside HubEventTag. Same FCM path
        # as every other hub-level event we already parse (#119).
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event import (  # noqa: E501
            transition_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.hub import (  # noqa: E501
            qualifier_pb2 as hub_qualifier_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.hub import (
            tag_pb2 as hub_tag_pb2,
        )

        qualifier = hub_qualifier_pb2.HubEventQualifier(
            tag=hub_tag_pb2.HubEventTag(ring_button_pressed=hub_tag_pb2.RingButtonPressed()),
            transition=transition_pb2.EventTransition(
                impulse=transition_pb2.EventTransition.Impulse()
            ),
        )
        wrapped = self._wrap(qualifier.SerializeToString())

        listener = self._make_listener()
        result = listener._extract_event_with_compiled_protos(wrapped)

        assert result is not None
        event_type, data = result
        assert event_type == "doorbell_pressed"
        assert data["raw_tag"] == "ring_button_pressed"

    def test_video_ring_button_pressed_resolves_to_doorbell_pressed(self) -> None:
        # MotionCam Video Doorbell (camera-with-ring-button) fires the same
        # event tag but inside a VideoEventQualifier — different oneof,
        # different qualifier wrapper. Pass 4 in `_extract_event_with_
        # compiled_protos` walks VideoEventQualifier specifically (#119).
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event import (  # noqa: E501
            transition_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.video import (  # noqa: E501
            qualifier_pb2 as video_qualifier_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.video import (
            tag_pb2 as video_tag_pb2,
        )

        qualifier = video_qualifier_pb2.VideoEventQualifier(
            tag=video_tag_pb2.VideoEventTag(ring_button_pressed=video_tag_pb2.RingButtonPressed()),
            transition=transition_pb2.EventTransition(
                impulse=transition_pb2.EventTransition.Impulse()
            ),
        )
        wrapped = self._wrap(qualifier.SerializeToString())

        listener = self._make_listener()
        result = listener._extract_event_with_compiled_protos(wrapped)

        assert result is not None
        event_type, data = result
        assert event_type == "doorbell_pressed"
        assert data["raw_tag"] == "ring_button_pressed"

    def test_video_qualifier_motion_detected_does_not_false_positive(self) -> None:
        # The Video Doorbell also emits non-doorbell events (motion_detected,
        # human_detected, etc.). Those are not currently mapped so the parser
        # must return None for them — not silently mis-fire `doorbell_pressed`.
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event import (  # noqa: E501
            transition_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.video import (  # noqa: E501
            qualifier_pb2 as video_qualifier_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.video import (
            tag_pb2 as video_tag_pb2,
        )

        qualifier = video_qualifier_pb2.VideoEventQualifier(
            tag=video_tag_pb2.VideoEventTag(motion_detected=video_tag_pb2.MotionDetected()),
            transition=transition_pb2.EventTransition(
                triggered=transition_pb2.EventTransition.Triggered()
            ),
        )
        wrapped = self._wrap(qualifier.SerializeToString())

        listener = self._make_listener()
        result = listener._extract_event_with_compiled_protos(wrapped)

        # `motion_detected` from a video qualifier maps cleanly to "motion"
        # — same downstream HA event_type the existing motion sensors emit,
        # no need for a doorbell-only mapping.
        assert result is not None
        event_type, _ = result
        assert event_type == "motion"


class TestNotificationDedupe:
    """Issue #80: Ajax dispatches two FCM messages per security transition with
    identical notification_id; the second must not double-fire automations."""

    def _make_listener(self) -> tuple[AjaxNotificationListener, MagicMock, MagicMock]:
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        coordinator = MagicMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._space_ids = []
        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)
        return listener, hass, coordinator

    def test_duplicate_notification_id_skips_second_fire(self) -> None:
        listener, hass, _ = self._make_listener()

        # Two pushes with the same encoded data → same notification_id.
        listener._on_notification({"ENCODED_DATA": _REAL_PUSH_ENCODED_DATA}, "pid-1")
        listener._on_notification({"ENCODED_DATA": _REAL_PUSH_ENCODED_DATA}, "pid-2")

        # First push triggered the refresh; second one short-circuited.
        assert hass.loop.call_soon_threadsafe.call_count == 1

    def test_distinct_notification_ids_both_fire(self) -> None:
        listener, hass, _ = self._make_listener()

        # First push uses the canonical real payload (notif_id = …D89E).
        listener._on_notification({"ENCODED_DATA": _REAL_PUSH_ENCODED_DATA}, "pid-1")

        # Second push has a different 64-char hex notification_id embedded in the
        # raw bytes — extract_notification_id picks the first 64-hex match.
        other_notif_id = "AAAA" + "B" * 60
        encoded = base64.b64encode(other_notif_id.encode()).decode()
        listener._on_notification({"ENCODED_DATA": encoded}, "pid-2")

        assert hass.loop.call_soon_threadsafe.call_count == 2

    def test_duplicate_outside_window_fires_again(self) -> None:
        from custom_components.aegis_ajax.notification import (  # noqa: PLC0415
            NOTIFICATION_DEDUPE_WINDOW_SECONDS,
        )

        listener, hass, _ = self._make_listener()

        with patch("custom_components.aegis_ajax.notification.time.monotonic") as monotonic:
            monotonic.return_value = 1000.0
            listener._on_notification({"ENCODED_DATA": _REAL_PUSH_ENCODED_DATA}, "pid-1")

            # Second push beyond the dedupe window — should fire again.
            monotonic.return_value = 1000.0 + NOTIFICATION_DEDUPE_WINDOW_SECONDS + 0.1
            listener._on_notification({"ENCODED_DATA": _REAL_PUSH_ENCODED_DATA}, "pid-2")

        assert hass.loop.call_soon_threadsafe.call_count == 2

    def test_push_without_notification_id_does_not_dedupe(self) -> None:
        # Defensive: if extract_notification_id returns None (parser miss), we
        # never want to silence the second push by accident.
        listener, hass, _ = self._make_listener()

        # Encoded data without a 64-char hex string → notif_id is None.
        encoded = base64.b64encode(b"no hex id present here, just text").decode()

        listener._on_notification({"ENCODED_DATA": encoded}, "pid-1")
        listener._on_notification({"ENCODED_DATA": encoded}, "pid-2")

        assert hass.loop.call_soon_threadsafe.call_count == 2

    def test_dedupe_dict_pruned_to_recent_entries(self) -> None:
        from custom_components.aegis_ajax.notification import (  # noqa: PLC0415
            NOTIFICATION_DEDUPE_WINDOW_SECONDS,
        )

        listener, _, _ = self._make_listener()

        with patch("custom_components.aegis_ajax.notification.time.monotonic") as monotonic:
            monotonic.return_value = 1000.0
            listener._on_notification({"ENCODED_DATA": _REAL_PUSH_ENCODED_DATA}, "pid-1")
            assert _EXPECTED_NOTIFICATION_ID in listener._recent_notification_ids

            # A later, distinct push prunes the expired entry.
            monotonic.return_value = 1000.0 + NOTIFICATION_DEDUPE_WINDOW_SECONDS + 1.0
            other = "FFFF" + "E" * 60
            listener._on_notification(
                {"ENCODED_DATA": base64.b64encode(other.encode()).decode()},
                "pid-2",
            )

        assert _EXPECTED_NOTIFICATION_ID not in listener._recent_notification_ids
        assert other in listener._recent_notification_ids
