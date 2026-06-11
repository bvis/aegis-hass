"""Tests for FCM notification listener."""

from __future__ import annotations

import asyncio
import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.notification import (
    AjaxNotificationListener,
    _classify_fcm_failure,
    _validate_fcm_shape,
)

# A coherent four-value FCM set whose shapes pass every validator check:
# app_id matches `1:<digits>:android:<40 hex>`, sender_id equals the digit
# chunk byte-for-byte, api_key is `AIza` + 35 chars (39 total). Real values
# would round-trip to a real Firebase project; these don't, which is fine
# because shape validation is offline.
_VALID_FCM_SHAPES = {
    "fcm_project_id": "mws-mobile-client---2",
    "fcm_app_id": "1:991608156148:android:" + "a" * 40,
    "fcm_api_key": "AIza" + "x" * 35,
    "fcm_sender_id": "991608156148",
}

# Alias kept for the dozens of listener tests that don't exercise FCM
# shape validation (notification parsing, photo-on-demand, etc) — they
# just need any four-tuple that satisfies the constructor. Pre-#182 this
# was a separate dict with placeholder strings ("test-app", etc.) that
# would have failed the new shape check the moment any of them invoked
# `async_start`, so pointing it at the validated set keeps them
# bulletproof without per-test edits.
_FCM_KWARGS = _VALID_FCM_SHAPES

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


def _restamp_push(encoded: str, *, seconds_ago: float = 0.0) -> str:
    """Re-serialize an ENCODED_DATA payload with `server_timestamp` set to
    `now - seconds_ago` so the FCM-replay filter (#174) treats it as fresh
    in test runs. The original capture is frozen in time; without this
    helper every push older than `STALE_PUSH_THRESHOLD_SECONDS` (120 s)
    would be dropped before the assertion under test runs.
    """
    from datetime import UTC, datetime, timedelta

    from google.protobuf.timestamp_pb2 import Timestamp
    from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.service.push_notification_dispatch import (  # noqa: E501
        event_pb2,
    )

    dispatch = event_pb2.PushNotificationDispatchEvent()
    dispatch.ParseFromString(base64.b64decode(encoded))
    ts = Timestamp()
    ts.FromDatetime(datetime.now(tz=UTC) - timedelta(seconds=seconds_ago))
    dispatch.notification.server_timestamp.CopyFrom(ts)
    return base64.b64encode(dispatch.SerializeToString()).decode()


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
        """ENCODED_DATA with an HTTPS URL resolves pending photo futures.

        Resolution must be marshaled to the event loop (#274):
        `_on_notification` runs on the FCM worker thread and
        `Future.set_result` is loop-only.
        """
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        scheduled: list[tuple[Any, tuple[Any, ...]]] = []
        hass.loop.call_soon_threadsafe.side_effect = lambda func, *args: scheduled.append(
            (func, args)
        )
        coordinator = MagicMock()

        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

        # Create a pending future
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str | None] = loop.create_future()
        listener._photo_callbacks["dev-1"] = future

        # Build a fake ENCODED_DATA containing an HTTPS URL
        raw_bytes = b"\x08\x01" + b"https://app.prod.ajax.systems/photo/test.jpg" + b"\x00"
        encoded = base64.b64encode(raw_bytes).decode()

        listener._on_notification({"ENCODED_DATA": encoded}, "persistent-2")

        # The (simulated) worker thread must not resolve the future inline.
        assert not future.done()

        # Run what the worker scheduled onto the loop.
        for func, args in scheduled:
            func(*args)

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
        # Route worker-thread dispatches onto the real test loop so the
        # marshaled future resolution (#274) actually executes.
        real_loop = asyncio.get_running_loop()
        hass.loop.call_soon_threadsafe.side_effect = lambda func, *args: real_loop.call_soon(
            func, *args
        )
        coordinator = MagicMock()

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

    def test_extract_space_source_info_returns_group_id_for_group_source(self) -> None:
        # `space_group_*` events come wrapped in a SpaceNotificationContent
        # whose `space_source` carries `type=GROUP (3)` plus the group's id
        # and name. The parser scans for this and returns
        # `{"group_id": ..., "group_name": ...}` so the per-group alarm panel
        # can refresh from the push (#148).
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.space import (  # noqa: E501
            source_pb2,
            source_type_pb2,
        )

        source = source_pb2.SpaceNotificationSource(
            type=source_type_pb2.SpaceNotificationSourceType.GROUP,
            id="group-abc-123",
            name="Downstairs",
        )
        # The push payload wraps the source as a length-delimited field; the
        # parser is robust against the outer wrapper, so emitting the raw
        # source bytes is enough to exercise the scan.
        raw = source.SerializeToString()

        result = AjaxNotificationListener._extract_space_source_info(raw)
        assert result == {"group_id": "group-abc-123", "group_name": "Downstairs"}

    def test_extract_space_source_info_skips_non_group_source(self) -> None:
        # A SPACE-level source (whole-space arm/disarm) must not be reported
        # as a group, so the per-group routing path stays inert.
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.space import (  # noqa: E501
            source_pb2,
            source_type_pb2,
        )

        source = source_pb2.SpaceNotificationSource(
            type=source_type_pb2.SpaceNotificationSourceType.SPACE,
            id="space-xyz",
            name="Home",
        )
        raw = source.SerializeToString()

        result = AjaxNotificationListener._extract_space_source_info(raw)
        assert result == {}

    def test_extract_space_source_info_returns_empty_for_garbage(self) -> None:
        result = AjaxNotificationListener._extract_space_source_info(b"\x00\x01\x02\x03")
        assert result == {}

    def test_extract_space_group_info_resolves_from_display_groups(self) -> None:
        """Real production fix (#148): Ajax carries the group_id in
        `additional_data.space_display_groups` → `DisplayGroups`. Real
        payloads always wrap the Group in the parent message (confirmed
        from beta.6 + beta.8 wire captures), so the extractor parses the
        parent — not the inner Group directly — to stay specific.
        """
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.space.additional.data import (  # noqa: E501
            display_groups_pb2,
        )

        display = display_groups_pb2.DisplayGroups(
            groups=[
                display_groups_pb2.DisplayGroups.Group(
                    group_hex_id="00000001", group_name="Out House"
                ),
                display_groups_pb2.DisplayGroups.Group(group_hex_id="00000002", group_name="Home"),
            ]
        )
        # Pad with leading bytes so the scan also has to walk past noise.
        raw = b"\x99\x88" + display.SerializeToString() + b"\x77"
        result = AjaxNotificationListener._extract_space_group_info(raw)
        # First valid group wins — that's the one Ajax pushes for the
        # specific event (the others are context for the rest of the UI).
        assert result == {"group_id": "00000001", "group_name": "Out House"}

    def test_extract_space_group_info_returns_empty_for_no_match(self) -> None:
        assert AjaxNotificationListener._extract_space_group_info(b"\x00\x01\x02\x03") == {}

    def test_extract_space_group_info_rejects_long_hex_id_like_space_id(self) -> None:
        """Regression for #148 1.5.0-beta.8: the extractor used to latch
        onto the 24-char `space_id` (also a hex string, encoded as field
        1 string of some unrelated message) and return it as `group_id`,
        which then failed to match any real Group in
        `coordinator.spaces[].groups`. Length cap + parent-DisplayGroups
        parse together reject that path. Reproducer: forge a Group with
        a 24-char hex `group_hex_id` (looks exactly like a `space_id`)
        and confirm the extractor refuses it.
        """
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.space.additional.data import (  # noqa: E501
            display_groups_pb2,
        )

        display = display_groups_pb2.DisplayGroups(
            groups=[
                display_groups_pb2.DisplayGroups.Group(
                    group_hex_id="68f94162415a39f8b8df2e5d",  # real space_id from #148 capture
                    group_name="169 WA",
                )
            ]
        )
        result = AjaxNotificationListener._extract_space_group_info(display.SerializeToString())
        assert result == {}, "must not surface a 24-char hex string as group_id"

    def test_extract_space_group_info_rejects_non_hex_id(self) -> None:
        """Ajax `group_hex_id` is always hex chars — letters g-z signal
        we landed on an unrelated (string, string) pair and must skip."""
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.space.additional.data import (  # noqa: E501
            display_groups_pb2,
        )

        display = display_groups_pb2.DisplayGroups(
            groups=[
                display_groups_pb2.DisplayGroups.Group(group_hex_id="hello", group_name="world")
            ]
        )
        assert AjaxNotificationListener._extract_space_group_info(display.SerializeToString()) == {}

    def test_extract_space_group_info_picks_next_group_when_first_invalid(self) -> None:
        """If the first Group in a DisplayGroups payload fails sanity
        (e.g. a long hex_id), the extractor must check the next entry
        rather than returning empty — defends against any payload that
        leads with a context Group and follows with the real one."""
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.space.additional.data import (  # noqa: E501
            display_groups_pb2,
        )

        display = display_groups_pb2.DisplayGroups(
            groups=[
                display_groups_pb2.DisplayGroups.Group(
                    group_hex_id="68f94162415a39f8b8df2e5d", group_name="169 WA"
                ),
                display_groups_pb2.DisplayGroups.Group(
                    group_hex_id="00000001", group_name="Out House"
                ),
            ]
        )
        result = AjaxNotificationListener._extract_space_group_info(display.SerializeToString())
        assert result == {"group_id": "00000001", "group_name": "Out House"}

    @pytest.mark.asyncio
    async def test_on_notification_extracts_notification_id(self) -> None:
        """ENCODED_DATA with a notification_id resolves pending notification_id futures.

        Resolution must be marshaled to the event loop (#274), same as the
        photo-URL futures: `Future.set_result` is loop-only.
        """
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        scheduled: list[tuple[Any, tuple[Any, ...]]] = []
        hass.loop.call_soon_threadsafe.side_effect = lambda func, *args: scheduled.append(
            (func, args)
        )
        coordinator = MagicMock()
        coordinator._space_ids = []

        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str | None] = loop.create_future()
        listener._notification_id_callbacks["A1B2C3D4"] = future

        # Real encoded data containing a 64-char hex notification ID
        listener._on_notification(
            {"ENCODED_DATA": _restamp_push(_REAL_PUSH_ENCODED_DATA)}, "persistent-n1"
        )

        # The (simulated) worker thread must not resolve the future inline.
        assert not future.done()

        for func, args in scheduled:
            func(*args)

        assert future.result() == _EXPECTED_NOTIFICATION_ID
        assert listener._notification_id_callbacks == {}
        assert listener._last_notification_id == _EXPECTED_NOTIFICATION_ID

    @pytest.mark.asyncio
    async def test_scheduled_resolution_skips_future_cancelled_before_loop_runs(self) -> None:
        """A `wait_for_*` timeout can cancel the future after the worker
        thread matched it but before the loop runs the scheduled resolution
        (#274). The loop-side callback must re-check the future state instead
        of raising InvalidStateError.
        """
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        scheduled: list[tuple[Any, tuple[Any, ...]]] = []
        hass.loop.call_soon_threadsafe.side_effect = lambda func, *args: scheduled.append(
            (func, args)
        )
        coordinator = MagicMock()
        coordinator._space_ids = []

        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str | None] = loop.create_future()
        listener._notification_id_callbacks["A1B2C3D4"] = future

        listener._on_notification(
            {"ENCODED_DATA": _restamp_push(_REAL_PUSH_ENCODED_DATA)}, "persistent-n2"
        )

        # Timeout fires on the loop before the scheduled resolution runs.
        future.cancel()

        for func, args in scheduled:
            func(*args)  # must not raise InvalidStateError

        assert future.cancelled()
        assert "A1B2C3D4" not in listener._notification_id_callbacks

    @pytest.mark.asyncio
    async def test_wait_for_notification_id_timeout(self) -> None:
        """wait_for_notification_id returns None on timeout."""
        hass = MagicMock()
        coordinator = MagicMock()
        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

        result = await listener.wait_for_notification_id("dev-99", timeout=0.05)
        assert result is None
        assert "dev-99" not in listener._notification_id_callbacks


class TestFcmPushClientSupervision:
    """Supervised restart of a self-terminated FCM client (#285).

    firebase-messaging shuts itself down (`do_listen = False`) after
    `abort_on_sequential_error_count` sequential errors or repeated failed
    reconnects. Without supervision push silently stays dead until the next
    HA restart; with it, the client is restarted with a delayed backoff so
    a Google-side outage can't turn into a reconnect storm.
    """

    def _make_listener(self) -> AjaxNotificationListener:
        hass = MagicMock()
        coordinator = MagicMock()
        return AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

    @staticmethod
    def _running_task() -> MagicMock:
        task = MagicMock()
        task.done.return_value = False
        return task

    @staticmethod
    def _finished_task() -> MagicMock:
        task = MagicMock()
        task.done.return_value = True
        return task

    @pytest.mark.asyncio
    async def test_alive_client_is_left_alone(self) -> None:
        listener = self._make_listener()
        client = MagicMock()
        client.do_listen = True
        client.tasks = [self._running_task(), self._running_task()]
        listener._push_client = client

        await listener._async_supervise_push_client(now=1000.0)

        assert listener._push_client is client
        assert listener._fcm_restart_at is None

    @pytest.mark.asyncio
    async def test_zombie_client_with_dead_listen_task_schedules_restart(self) -> None:
        """`_listen()` early-returns when the INITIAL connect exhausts its
        retries — `do_listen` stays True (only `_terminate()` and the reset
        path lower it) and `run_state` parks in STARTING_CONNECTION, where
        the library's own `_do_monitor` never acts. The flag alone would
        report this zombie as healthy forever; a finished task in
        `client.tasks` is the tell.
        """
        listener = self._make_listener()
        client = MagicMock()
        client.do_listen = True
        client.tasks = [self._finished_task(), self._running_task()]
        client.stop = AsyncMock()
        listener._push_client = client

        await listener._async_supervise_push_client(now=1000.0)

        assert listener._push_client is None
        assert listener._fcm_restart_at == 1000.0 + 300.0

    @pytest.mark.asyncio
    async def test_client_with_no_tasks_yet_is_not_a_zombie(self) -> None:
        # `tasks` is [] until start() runs — an empty list must not read as
        # "listen task finished".
        listener = self._make_listener()
        client = MagicMock()
        client.do_listen = True
        client.tasks = []
        listener._push_client = client

        await listener._async_supervise_push_client(now=1000.0)

        assert listener._push_client is client
        assert listener._fcm_restart_at is None

    @pytest.mark.asyncio
    async def test_initial_start_failure_still_supervises_and_schedules_retry(self) -> None:
        """A failed first start must not leave push dead until the next HA
        restart — the supervisor is installed regardless and a delayed retry
        is seeded with the same backoff the death path uses.
        """
        hass = MagicMock()
        listener = AjaxNotificationListener(hass=hass, coordinator=MagicMock(), **_FCM_KWARGS)
        listener._store.async_load = AsyncMock(
            return_value={"fcm": {"registration": {"token": "tok"}}}
        )
        listener._register_push_token = AsyncMock()
        listener._async_start_push_client = AsyncMock(return_value=False)
        fake_unsub = MagicMock()

        with patch(
            "homeassistant.helpers.event.async_track_time_interval",
            MagicMock(return_value=fake_unsub),
        ):
            await listener.async_start()

        assert listener._fcm_supervisor_unsub is fake_unsub
        assert listener._fcm_restart_at is not None

    @pytest.mark.asyncio
    async def test_dead_client_schedules_delayed_restart(self) -> None:
        listener = self._make_listener()
        client = MagicMock()
        client.do_listen = False
        client.stop = AsyncMock()
        listener._push_client = client

        await listener._async_supervise_push_client(now=1000.0)

        # Torn down (also flips `is_fcm_connected` → reachability shows
        # "push down") and scheduled, NOT restarted inline.
        assert listener._push_client is None
        assert listener._fcm_restart_at == 1000.0 + 300.0

    @pytest.mark.asyncio
    async def test_restart_fires_only_after_backoff_elapses(self) -> None:
        listener = self._make_listener()
        listener._fcm_restart_at = 1300.0
        listener._async_start_push_client = AsyncMock(return_value=True)

        await listener._async_supervise_push_client(now=1299.0)
        listener._async_start_push_client.assert_not_awaited()

        await listener._async_supervise_push_client(now=1300.0)
        listener._async_start_push_client.assert_awaited_once()
        assert listener._fcm_restart_at is None

    @pytest.mark.asyncio
    async def test_backoff_doubles_and_caps(self) -> None:
        listener = self._make_listener()

        def _dead_client() -> MagicMock:
            client = MagicMock()
            client.do_listen = False
            client.stop = AsyncMock()
            return client

        listener._push_client = _dead_client()
        await listener._async_supervise_push_client(now=0.0)
        assert listener._fcm_restart_at == 300.0

        listener._push_client = _dead_client()
        await listener._async_supervise_push_client(now=2000.0)
        assert listener._fcm_restart_at == 2000.0 + 600.0

        listener._push_client = _dead_client()
        await listener._async_supervise_push_client(now=4000.0)
        assert listener._fcm_restart_at == 4000.0 + 900.0

        # Capped: never exceeds 15 minutes.
        listener._push_client = _dead_client()
        await listener._async_supervise_push_client(now=6000.0)
        assert listener._fcm_restart_at == 6000.0 + 900.0

    @pytest.mark.asyncio
    async def test_failed_restart_reschedules(self) -> None:
        listener = self._make_listener()
        listener._fcm_restart_at = 1000.0
        listener._fcm_restart_backoff = 600.0
        listener._async_start_push_client = AsyncMock(return_value=False)

        await listener._async_supervise_push_client(now=1000.0)

        assert listener._fcm_restart_at == 1000.0 + 600.0

    @pytest.mark.asyncio
    async def test_long_healthy_run_resets_backoff(self) -> None:
        listener = self._make_listener()
        client = MagicMock()
        client.do_listen = True
        client.tasks = [self._running_task()]
        listener._push_client = client
        listener._fcm_restart_backoff = 900.0
        listener._fcm_client_started_at = 0.0

        await listener._async_supervise_push_client(now=1800.0)

        assert listener._fcm_restart_backoff == 300.0

    @pytest.mark.asyncio
    async def test_async_stop_cancels_supervisor(self) -> None:
        listener = self._make_listener()
        unsub = MagicMock()
        listener._fcm_supervisor_unsub = unsub
        listener._fcm_restart_at = 123.0

        await listener.async_stop()

        unsub.assert_called_once()
        assert listener._fcm_supervisor_unsub is None
        assert listener._fcm_restart_at is None


class TestAsyncStartFcmRepairs:
    """The FCM listener raises a Repair when registration / push start fails."""

    @pytest.mark.asyncio
    async def test_not_configured_repair_raised_when_fcm_unconfigured(self) -> None:
        """No api_key → raise `fcm_not_configured` repair so the user gets a
        visible nudge to enter keys via the Repair card, plus a WARNING log
        line (instead of the previous silent INFO). `fcm_credentials_invalid`
        is left alone — it's a different state (keys present but rejected).

        #252: the repair is NOT cleared first in this path. Deleting and
        re-creating it on every start wiped HA's per-issue dismissal, so a
        user who chose to leave push off saw the card return after every
        reboot. We now `async_register` idempotently (HA preserves a prior
        dismissal) and only clear once credentials are actually present."""
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
        # #252: must NOT clear in the unconfigured path — clearing deletes the
        # registry entry, which would wipe a user's dismissal so the card
        # reappears every reboot. Register is idempotent; HA keeps the dismiss.
        clr_missing.assert_not_called()
        reg_missing.assert_called_once_with(hass, entry_id="entry-x")

    @pytest.mark.asyncio
    async def test_no_repair_when_push_warning_disabled(self) -> None:
        """User opted out of push (`disable_push_warning`): an empty api_key
        must NOT raise the `fcm_not_configured` repair, and any stale card is
        cleared. #252 — a durable opt-out for users who deliberately run
        without push, instead of relying on HA's per-issue dismissal."""
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
            disable_push_warning=True,
        )

        with (
            patch("custom_components.aegis_ajax.notification.async_clear_fcm_credentials_invalid"),
            patch(
                "custom_components.aegis_ajax.notification.async_clear_fcm_credentials_malformed"
            ),
            patch(
                "custom_components.aegis_ajax.notification.async_register_fcm_not_configured"
            ) as reg_missing,
            patch(
                "custom_components.aegis_ajax.notification.async_clear_fcm_not_configured"
            ) as clr_missing,
        ):
            await listener.async_start()

        # Opt-out: never nag, and clear any card raised on a prior start.
        reg_missing.assert_not_called()
        clr_missing.assert_called_once_with(hass, entry_id="entry-x")

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
        listener._rejected_store.async_load = AsyncMock(return_value=None)

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
            patch(
                "custom_components.aegis_ajax.notification.async_clear_fcm_not_configured"
            ) as clr_missing,
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
        ):
            await listener.async_start()

        reg.assert_called_once_with(hass, entry_id="entry-x")
        # #252: credentials ARE present here (only registration failed), so the
        # "not configured" repair is cleared once the api_key check passes.
        clr_missing.assert_called_once_with(hass, entry_id="entry-x")


class TestFcmRejectedCredsShortCircuit:
    """#227 — don't re-hit the Firebase project on every restart with a
    credential set Google already rejected. The rejection is remembered by a
    one-way hash; a terminal failure persists it, a matching hash short-circuits
    the next attempt, and a successful registration / changed values clear it.
    """

    @staticmethod
    def _listener(hass: MagicMock) -> AjaxNotificationListener:
        listener = AjaxNotificationListener(
            hass=hass, coordinator=MagicMock(), **_FCM_KWARGS, entry_id="entry-x"
        )
        listener._store.async_load = AsyncMock(return_value=None)
        listener._rejected_store.async_load = AsyncMock(return_value=None)
        listener._rejected_store.async_load = AsyncMock(return_value=None)
        listener._rejected_store.async_save = AsyncMock()
        listener._rejected_store.async_remove = AsyncMock()
        return listener

    @staticmethod
    def _expected_hash() -> str:
        from custom_components.aegis_ajax.notification import _fcm_creds_hash

        return _fcm_creds_hash(
            fcm_project_id=_FCM_KWARGS["fcm_project_id"],
            fcm_app_id=_FCM_KWARGS["fcm_app_id"],
            fcm_api_key=_FCM_KWARGS["fcm_api_key"],
            fcm_sender_id=_FCM_KWARGS["fcm_sender_id"],
        )

    @staticmethod
    def _repair_patches() -> tuple:
        return (
            patch(
                "custom_components.aegis_ajax.notification.async_register_fcm_credentials_invalid"
            ),
            patch("custom_components.aegis_ajax.notification.async_clear_fcm_credentials_invalid"),
            patch("custom_components.aegis_ajax.notification.async_register_fcm_not_configured"),
            patch("custom_components.aegis_ajax.notification.async_clear_fcm_not_configured"),
        )

    @pytest.mark.asyncio
    async def test_terminal_failure_persists_rejected_hash(self) -> None:
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(
            side_effect=RuntimeError(
                "Unable to establish subscription with Google Cloud Messaging."
            )
        )
        listener = self._listener(hass)
        register_cls = MagicMock(return_value=MagicMock(register=MagicMock()))

        reg_inv, clr_inv, reg_miss, clr_miss = self._repair_patches()
        with (
            reg_inv,
            clr_inv,
            reg_miss,
            clr_miss,
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
        ):
            await listener.async_start()

        listener._rejected_store.async_save.assert_awaited_once_with(
            {"hash": self._expected_hash()}
        )

    @pytest.mark.asyncio
    async def test_transient_failure_does_not_persist(self) -> None:
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(
            side_effect=RuntimeError("Unable to register and check in to gcm")
        )
        listener = self._listener(hass)
        register_cls = MagicMock(return_value=MagicMock(register=MagicMock()))

        reg_inv, clr_inv, reg_miss, clr_miss = self._repair_patches()
        with (
            reg_inv,
            clr_inv,
            reg_miss,
            clr_miss,
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
        ):
            await listener.async_start()

        listener._rejected_store.async_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_short_circuits_when_hash_matches(self) -> None:
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock()
        listener = self._listener(hass)
        listener._rejected_store.async_load = AsyncMock(
            return_value={"hash": self._expected_hash()}
        )
        register_cls = MagicMock()

        reg_inv, clr_inv, reg_miss, clr_miss = self._repair_patches()
        with (
            reg_inv as reg,
            clr_inv,
            reg_miss,
            clr_miss,
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
        ):
            await listener.async_start()

        # No network registration attempt, but the Repair is kept visible.
        register_cls.assert_not_called()
        hass.async_add_executor_job.assert_not_called()
        reg.assert_called_once_with(hass, entry_id="entry-x")

    @pytest.mark.asyncio
    async def test_no_short_circuit_when_hash_differs(self) -> None:
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=RuntimeError("boom"))
        listener = self._listener(hass)
        listener._rejected_store.async_load = AsyncMock(return_value={"hash": "some-other-hash"})
        register_cls = MagicMock(return_value=MagicMock(register=MagicMock()))

        reg_inv, clr_inv, reg_miss, clr_miss = self._repair_patches()
        with (
            reg_inv,
            clr_inv,
            reg_miss,
            clr_miss,
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
        ):
            await listener.async_start()

        # Different values → must still attempt registration.
        register_cls.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_clears_rejected_marker(self) -> None:
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(
            return_value={"fcm": {"registration": {"token": "T"}}}
        )
        listener = self._listener(hass)
        listener._store.async_save = AsyncMock()
        listener._rejected_store.async_load = AsyncMock(return_value={"hash": "stale-hash"})
        listener._register_push_token = AsyncMock()

        reg_inv, clr_inv, reg_miss, clr_miss = self._repair_patches()
        with (
            reg_inv,
            clr_inv,
            reg_miss,
            clr_miss,
            patch(
                "firebase_messaging.fcmregister.FcmRegister",
                MagicMock(return_value=MagicMock(register=MagicMock())),
            ),
            patch("firebase_messaging.FcmPushClient", MagicMock()),
        ):
            await listener.async_start()

        listener._rejected_store.async_remove.assert_awaited_once()


class TestIsTerminalFcmFailure:
    def test_credential_rejection_strings_are_terminal(self) -> None:
        from custom_components.aegis_ajax.notification import _is_terminal_fcm_failure

        assert _is_terminal_fcm_failure(
            RuntimeError("Unable to establish subscription with Google Cloud Messaging.")
        )
        assert _is_terminal_fcm_failure(RuntimeError("Unable to register with fcm"))

    def test_network_and_unknown_are_not_terminal(self) -> None:
        from custom_components.aegis_ajax.notification import _is_terminal_fcm_failure

        assert not _is_terminal_fcm_failure(RuntimeError("Unable to register and check in to gcm"))
        assert not _is_terminal_fcm_failure(TimeoutError())
        assert not _is_terminal_fcm_failure(RuntimeError("something unexpected"))


class TestFcmCredsHash:
    def test_stable_and_sensitive(self) -> None:
        from custom_components.aegis_ajax.notification import _fcm_creds_hash

        a = _fcm_creds_hash(**{k: v for k, v in _FCM_KWARGS.items()})
        b = _fcm_creds_hash(**{k: v for k, v in _FCM_KWARGS.items()})
        assert a == b and len(a) == 64
        changed = dict(_FCM_KWARGS)
        changed["fcm_api_key"] = "AIza" + "y" * 35
        assert _fcm_creds_hash(**changed) != a
        # The hash must not leak the secret itself.
        assert _FCM_KWARGS["fcm_api_key"] not in a


class TestValidateFcmShape:
    """Pure-function shape checks on the four FCM credentials.

    The validator runs offline (no Firebase round-trip) and returns a
    short English description of the first shape problem it finds, or
    `None` when every value is structurally coherent. The point is to
    catch paste-truncation / mismatched-projects errors BEFORE Google's
    403 — same error class that surfaced in #155 and #182 with the
    cryptic `API_KEY_ANDROID_APP_BLOCKED` / `androidPackage: <empty>`
    message that doesn't name `fcm_app_id` as the culprit.
    """

    def test_all_valid_returns_none(self) -> None:
        assert _validate_fcm_shape(**_VALID_FCM_SHAPES) is None

    def test_app_id_missing_android_segment_is_rejected(self) -> None:
        problem = _validate_fcm_shape(
            **{**_VALID_FCM_SHAPES, "fcm_app_id": "1:991608156148:ios:" + "a" * 40},
        )
        assert problem is not None
        assert "fcm_app_id" in problem

    def test_app_id_missing_sender_chunk_is_rejected(self) -> None:
        problem = _validate_fcm_shape(
            **{**_VALID_FCM_SHAPES, "fcm_app_id": "1::android:" + "a" * 40},
        )
        assert problem is not None
        assert "fcm_app_id" in problem

    def test_app_id_canonical_16_char_hash_passes(self) -> None:
        # Firebase docs example is `1:1234567890:android:321abc456def7890`
        # — a 16-char hex tail. Real Ajax Play Store APK ships the same
        # length. An earlier version of this validator enforced a 30..64
        # char range and false-positived against the official Ajax APK
        # (#182 follow-up, @zwagerzaken). We mirror Firebase's own iOS
        # SDK validator (`^\\d+:ios:[a-f0-9]+$` — no length constraint;
        # firebase-ios-sdk PR #2529).
        assert (
            _validate_fcm_shape(
                **{
                    **_VALID_FCM_SHAPES,
                    "fcm_app_id": "1:991608156148:android:1be5b6c08d8fc6d7",
                }
            )
            is None
        )

    def test_app_id_non_hex_tail_is_rejected(self) -> None:
        problem = _validate_fcm_shape(
            **{**_VALID_FCM_SHAPES, "fcm_app_id": "1:991608156148:android:" + "G" * 40},
        )
        assert problem is not None
        assert "fcm_app_id" in problem

    def test_app_id_empty_hex_tail_is_rejected(self) -> None:
        # The regex's `+` quantifier rejects a zero-char tail — the most
        # extreme paste truncation (where the user clipped right after
        # the `:android:` separator). Any other length ≥ 1 hex char
        # passes shape validation by design (Firebase itself doesn't
        # enforce a length range); paste truncations that leave 1+ hex
        # chars still fall through to Google's 403 like before #182.
        problem = _validate_fcm_shape(
            **{**_VALID_FCM_SHAPES, "fcm_app_id": "1:991608156148:android:"},
        )
        assert problem is not None
        assert "fcm_app_id" in problem

    def test_sender_id_mismatch_with_app_id_is_rejected(self) -> None:
        # The sender chunk inside fcm_app_id is `991608156148`, but
        # fcm_sender_id was pasted as a different project's id.
        problem = _validate_fcm_shape(
            **{**_VALID_FCM_SHAPES, "fcm_sender_id": "123456789012"},
        )
        assert problem is not None
        assert "fcm_sender_id" in problem

    def test_sender_id_with_non_digit_is_rejected(self) -> None:
        problem = _validate_fcm_shape(
            **{**_VALID_FCM_SHAPES, "fcm_sender_id": "99160815614X"},
        )
        assert problem is not None
        assert "fcm_sender_id" in problem

    def test_api_key_wrong_prefix_is_rejected(self) -> None:
        problem = _validate_fcm_shape(
            **{**_VALID_FCM_SHAPES, "fcm_api_key": "Bzzz" + "x" * 35},
        )
        assert problem is not None
        assert "fcm_api_key" in problem

    def test_api_key_wrong_length_is_rejected(self) -> None:
        problem = _validate_fcm_shape(
            **{**_VALID_FCM_SHAPES, "fcm_api_key": "AIza" + "x" * 20},
        )
        assert problem is not None
        assert "fcm_api_key" in problem

    def test_project_id_empty_is_rejected(self) -> None:
        problem = _validate_fcm_shape(
            **{**_VALID_FCM_SHAPES, "fcm_project_id": ""},
        )
        assert problem is not None
        assert "fcm_project_id" in problem

    def test_returns_first_problem_when_multiple_fields_bad(self) -> None:
        # Both app_id and api_key are bad; the validator returns one
        # message (the caller surfaces one Repair at a time, not a
        # bulk diff). The exact field surfaced is deterministic so
        # the message is stable across runs.
        problem = _validate_fcm_shape(
            fcm_project_id="",
            fcm_app_id="garbage",
            fcm_api_key="garbage",
            fcm_sender_id="garbage",
        )
        assert problem is not None


class TestAsyncStartFcmAndroidPackageHeader:
    """`async_start` injects `X-Android-Package` on Firebase Installations
    calls for known co-brands so Google's api-key package restriction
    doesn't refuse the request with `API_KEY_ANDROID_APP_BLOCKED` /
    `androidPackage: <empty>` (#155, #182). The header rides as a
    default on a session passed to `FcmRegister` via
    `http_client_session`; aiohttp merges per-request headers on top so
    the library's own `x-firebase-client` / `x-goog-api-key` stay
    untouched.
    """

    @pytest.mark.asyncio
    async def test_ajax_cobrand_passes_custom_session_with_package_header(self) -> None:
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(return_value={})
        coordinator = MagicMock()
        listener = AjaxNotificationListener(
            hass=hass,
            coordinator=coordinator,
            **_VALID_FCM_SHAPES,
            entry_id="entry-x",
            app_label="Ajax",
        )
        listener._store.async_load = AsyncMock(return_value=None)
        listener._rejected_store.async_load = AsyncMock(return_value=None)
        listener._register_push_token = AsyncMock()

        register_cls = MagicMock()
        instance = MagicMock()
        instance.register = MagicMock(side_effect=RuntimeError("boom"))  # bail before push start
        register_cls.return_value = instance

        with (
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
            patch(
                "custom_components.aegis_ajax.notification.async_register_fcm_credentials_invalid"
            ),
            patch("custom_components.aegis_ajax.notification.async_clear_fcm_credentials_invalid"),
            patch(
                "custom_components.aegis_ajax.notification.async_register_fcm_credentials_malformed"
            ),
            patch(
                "custom_components.aegis_ajax.notification.async_clear_fcm_credentials_malformed"
            ),
            patch("custom_components.aegis_ajax.notification.async_register_fcm_not_configured"),
            patch("custom_components.aegis_ajax.notification.async_clear_fcm_not_configured"),
        ):
            await listener.async_start()

        # FcmRegister must be constructed with a session whose default
        # headers include `X-Android-Package: com.ajaxsystems` so
        # Firebase Installations sees the package id and the api-key
        # restriction passes.
        assert register_cls.call_count == 1
        kwargs = register_cls.call_args.kwargs
        session = kwargs["http_client_session"]
        assert session is not None
        # aiohttp.ClientSession exposes default headers via its `headers`
        # property — accept either dict-like (`["X-Android-Package"]`)
        # or attr-style depending on the aiohttp version.
        header_value = (
            session.headers.get("X-Android-Package")
            if hasattr(session.headers, "get")
            else session.headers["X-Android-Package"]
        )
        assert header_value == "com.ajaxsystems"

    @pytest.mark.asyncio
    async def test_unknown_cobrand_passes_no_session(self) -> None:
        """Co-brand labels without a known Android package mapping fall
        back to the pre-1.5.3-beta.10 behaviour: FcmRegister gets the
        default constructor (no `http_client_session`), so we don't
        emit an empty / unrelated session that would never satisfy
        Google's restriction anyway.
        """
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(return_value={})
        coordinator = MagicMock()
        listener = AjaxNotificationListener(
            hass=hass,
            coordinator=coordinator,
            **_VALID_FCM_SHAPES,
            entry_id="entry-x",
            app_label="some_brand_we_dont_map_yet",
        )
        listener._store.async_load = AsyncMock(return_value=None)
        listener._rejected_store.async_load = AsyncMock(return_value=None)
        listener._register_push_token = AsyncMock()

        register_cls = MagicMock()
        instance = MagicMock()
        instance.register = MagicMock(side_effect=RuntimeError("boom"))
        register_cls.return_value = instance

        with (
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
            patch(
                "custom_components.aegis_ajax.notification.async_register_fcm_credentials_invalid"
            ),
            patch("custom_components.aegis_ajax.notification.async_clear_fcm_credentials_invalid"),
            patch(
                "custom_components.aegis_ajax.notification.async_register_fcm_credentials_malformed"
            ),
            patch(
                "custom_components.aegis_ajax.notification.async_clear_fcm_credentials_malformed"
            ),
            patch("custom_components.aegis_ajax.notification.async_register_fcm_not_configured"),
            patch("custom_components.aegis_ajax.notification.async_clear_fcm_not_configured"),
        ):
            await listener.async_start()

        # FcmRegister called without `http_client_session` — kwargs
        # dict must not carry that key.
        assert register_cls.call_count == 1
        assert "http_client_session" not in register_cls.call_args.kwargs


class TestAsyncStartFcmShapePreflight:
    """`async_start` runs shape validation BEFORE invoking firebase_messaging.

    When shapes are malformed we raise the dedicated
    `fcm_credentials_malformed` Repair (one click → re-enter the four
    values) and skip the Firebase round-trip, so the user gets a
    precise diagnosis instead of Google's opaque 403. Counterpart of
    `test_register_failure_raises_repair`, which only fires once the
    library has been called.
    """

    @pytest.mark.asyncio
    async def test_malformed_app_id_short_circuits_firebase_call(self) -> None:
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock()
        coordinator = MagicMock()
        listener = AjaxNotificationListener(
            hass=hass,
            coordinator=coordinator,
            fcm_project_id="proj",
            fcm_app_id="1:991608156148:android:short",  # truncated
            fcm_api_key="AIza" + "x" * 35,
            fcm_sender_id="991608156148",
            entry_id="entry-x",
        )
        listener._store.async_load = AsyncMock(return_value=None)
        listener._rejected_store.async_load = AsyncMock(return_value=None)

        register_cls = MagicMock()

        with (
            patch(
                "custom_components.aegis_ajax.notification.async_register_fcm_credentials_malformed"
            ) as reg_malformed,
            patch(
                "custom_components.aegis_ajax.notification.async_clear_fcm_credentials_malformed"
            ) as clr_malformed,
            patch(
                "custom_components.aegis_ajax.notification.async_register_fcm_credentials_invalid"
            ) as reg_invalid,
            patch(
                "custom_components.aegis_ajax.notification.async_clear_fcm_credentials_invalid"
            ) as clr_invalid,
            patch("custom_components.aegis_ajax.notification.async_register_fcm_not_configured"),
            patch("custom_components.aegis_ajax.notification.async_clear_fcm_not_configured"),
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
        ):
            await listener.async_start()

        # The malformed Repair is raised exactly once with the problem
        # description as a translation placeholder so the Repair card
        # names `fcm_app_id` instead of leaving the user to read logs.
        assert reg_malformed.call_count == 1
        kwargs = reg_malformed.call_args.kwargs
        assert kwargs["entry_id"] == "entry-x"
        assert "fcm_app_id" in kwargs["problem"]

        # Firebase never gets called — shape check happens first.
        register_cls.assert_not_called()

        # The runtime-rejection Repair stays cleared (mutually
        # exclusive: shapes-bad OR Google-rejected, never both visible).
        reg_invalid.assert_not_called()
        clr_invalid.assert_called_once_with(hass, entry_id="entry-x")
        # The malformed Repair is also cleared at the top of the
        # method, then re-registered after the shape check. Mirrors
        # the existing pattern for `_invalid` / `_not_configured`.
        clr_malformed.assert_called_once_with(hass, entry_id="entry-x")

    @pytest.mark.asyncio
    async def test_valid_shapes_skip_malformed_repair_and_call_firebase(self) -> None:
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock()
        coordinator = MagicMock()
        listener = AjaxNotificationListener(
            hass=hass, coordinator=coordinator, **_VALID_FCM_SHAPES, entry_id="entry-x"
        )
        listener._store.async_load = AsyncMock(return_value=None)
        listener._rejected_store.async_load = AsyncMock(return_value=None)

        register_cls = MagicMock()
        instance = MagicMock()
        # Library raises after the shape check — we don't care about
        # the rest of the pipeline here, just that the shape check
        # didn't short-circuit before the library was reached.
        instance.register = MagicMock(side_effect=RuntimeError("boom"))
        register_cls.return_value = instance

        with (
            patch(
                "custom_components.aegis_ajax.notification.async_register_fcm_credentials_malformed"
            ) as reg_malformed,
            patch(
                "custom_components.aegis_ajax.notification.async_clear_fcm_credentials_malformed"
            ) as clr_malformed,
            patch(
                "custom_components.aegis_ajax.notification.async_register_fcm_credentials_invalid"
            ),
            patch("custom_components.aegis_ajax.notification.async_clear_fcm_credentials_invalid"),
            patch("custom_components.aegis_ajax.notification.async_register_fcm_not_configured"),
            patch("custom_components.aegis_ajax.notification.async_clear_fcm_not_configured"),
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
        ):
            await listener.async_start()

        # Shape check passed → no malformed Repair raised, only the
        # standard top-of-method clear fired.
        reg_malformed.assert_not_called()
        clr_malformed.assert_called_once_with(hass, entry_id="entry-x")

        # Library was reached.
        register_cls.assert_called_once()


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

    def test_fcm_install_failure_points_at_wrong_aiza_string(self) -> None:
        # Empirical: this branch fires when Firebase returns 403
        # API_KEY_ANDROID_APP_BLOCKED. After @alt-BadBatch / @zwagerzaken's
        # #182 data points, we know the most common cause is the user
        # picking a non-FCM `AIza...` string from the APK's native lib
        # (Maps / ML Kit). Surface that explanation so future users
        # don't go down the paste-truncation rabbit hole.
        msg = _classify_fcm_failure(RuntimeError("Unable to register with fcm"))
        # Both 403 sub-codes share the same cause (wrong AIza key) and remedy,
        # so the message names both: ANDROID_APP_BLOCKED (package-restricted
        # key) and SERVICE_BLOCKED (Maps/other-service key — raven2k24's #194).
        assert "API_KEY_ANDROID_APP_BLOCKED" in msg
        assert "API_KEY_SERVICE_BLOCKED" in msg
        assert "AIza" in msg  # the wrong-string explanation
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

    def test_space_group_armed_without_group_id_does_not_dispatch(self) -> None:
        # Group-level transitions only refresh the matching per-group panel.
        # Without a group_id (parser couldn't extract a SpaceNotificationSource
        # of type GROUP) we have nothing to route, so we no-op rather than
        # falling back to the space-level dispatch (#148).
        listener, hass, _ = self._make_listener()

        listener._apply_security_state_from_event("space-1", {"raw_tag": "space_group_armed"})

        hass.loop.call_soon_threadsafe.assert_not_called()

    def test_space_group_armed_with_group_id_dispatches_group_state(self) -> None:
        from custom_components.aegis_ajax.const import SecurityState

        listener, hass, coordinator = self._make_listener()

        listener._apply_security_state_from_event(
            "space-1", {"raw_tag": "space_group_armed", "group_id": "group-7"}
        )

        hass.loop.call_soon_threadsafe.assert_called_once_with(
            coordinator.apply_push_group_security_state,
            "space-1",
            "group-7",
            SecurityState.ARMED,
        )

    def test_space_group_disarmed_with_group_id_dispatches_disarmed(self) -> None:
        from custom_components.aegis_ajax.const import SecurityState

        listener, hass, coordinator = self._make_listener()

        listener._apply_security_state_from_event(
            "space-1", {"raw_tag": "space_group_disarmed", "group_id": "group-7"}
        )

        hass.loop.call_soon_threadsafe.assert_called_once_with(
            coordinator.apply_push_group_security_state,
            "space-1",
            "group-7",
            SecurityState.DISARMED,
        )


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

    def test_intrusion_alarm_beats_state_context(self) -> None:
        # When a payload bundles a state-context tag (`space_night_mode_on`)
        # together with a real incident (`intrusion_alarm`), the incident
        # wins regardless of qualifier order — `TAG_PRIORITY` ranks
        # confirmed incidents above state transitions. Previously the
        # first SpaceEventQualifier match was returned unconditionally, so
        # genuine alarms were rendered as `event_type=arm_night`.
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
        wrapped = self._wrap(hub_q.SerializeToString()) + self._wrap(space_q.SerializeToString())

        listener = self._make_listener()
        result = listener._extract_event_with_compiled_protos(wrapped)

        assert result is not None
        event_type, data = result
        assert event_type == "alarm"
        assert data["raw_tag"] == "intrusion_alarm"

    def test_priority_resolution_picks_highest_ranked_match(self) -> None:
        # Direct unit test of the priority logic: given multiple candidate
        # decodes across different qualifier types, the highest-ranked
        # tag wins regardless of candidate scan order. Mocks the
        # candidate-scan + per-qualifier-resolve helpers so the test
        # doesn't depend on whether synthetic proto bytes happen to
        # cross-decode (they sometimes do; real Ajax wire payloads — where
        # each qualifier comes wrapped in its own typed
        # `*NotificationContent` — do not).
        listener = self._make_listener()
        # Two candidates: first decodes as a state-context tag (priority
        # 50), second decodes as a sensor tag (priority 80). Sensor wins
        # by priority even though it's discovered second.
        with (
            patch(
                "custom_components.aegis_ajax.notification_event_parser._find_embedded_messages",
                return_value=[b"\x01", b"\x02"],
            ),
            patch(
                "custom_components.aegis_ajax.notification_event_parser._resolve_qualifier",
                side_effect=lambda c, *_: {
                    b"\x01": ("arm_night", {"raw_tag": "space_night_mode_on"}),
                    b"\x02": ("motion", {"raw_tag": "motion_detected"}),
                }.get(c),
            ),
        ):
            result = listener._extract_event_with_compiled_protos(b"")

        assert result is not None
        event_type, data = result
        assert event_type == "motion"
        assert data["raw_tag"] == "motion_detected"

    def test_priority_resolution_state_context_alone_still_wins(self) -> None:
        # When no higher-priority match is present, the state-context tag
        # is correctly returned — the priority ladder only changes which
        # match wins under contention, not what gets returned for a pure
        # arm / disarm push.
        listener = self._make_listener()
        with (
            patch(
                "custom_components.aegis_ajax.notification_event_parser._find_embedded_messages",
                return_value=[b"\x01"],
            ),
            patch(
                "custom_components.aegis_ajax.notification_event_parser._resolve_qualifier",
                side_effect=lambda c, *_: ("arm", {"raw_tag": "space_armed"}),
            ),
        ):
            result = listener._extract_event_with_compiled_protos(b"")

        assert result == ("arm", {"raw_tag": "space_armed"})

    def test_priority_resolution_intrusion_alarm_beats_motion(self) -> None:
        # Confirmed-incident tier (100) beats sensor-activity tier (80) —
        # an intrusion in progress with concurrent motion pings should
        # surface as `alarm`, not `motion`.
        listener = self._make_listener()
        with (
            patch(
                "custom_components.aegis_ajax.notification_event_parser._find_embedded_messages",
                return_value=[b"\x01", b"\x02"],
            ),
            patch(
                "custom_components.aegis_ajax.notification_event_parser._resolve_qualifier",
                side_effect=lambda c, *_: {
                    b"\x01": ("motion", {"raw_tag": "motion_detected"}),
                    b"\x02": ("alarm", {"raw_tag": "intrusion_alarm"}),
                }.get(c),
            ),
        ):
            result = listener._extract_event_with_compiled_protos(b"")

        assert result is not None
        event_type, data = result
        assert event_type == "alarm"
        assert data["raw_tag"] == "intrusion_alarm"

    def test_priority_resolution_ties_resolve_in_scan_order(self) -> None:
        # When two candidates produce matches at the same tier, the first
        # candidate (scan order) wins — preserving the legacy first-match
        # behaviour for tags that share a tier and avoiding silent
        # behaviour changes for state-only pushes.
        listener = self._make_listener()
        with (
            patch(
                "custom_components.aegis_ajax.notification_event_parser._find_embedded_messages",
                return_value=[b"\x01", b"\x02"],
            ),
            patch(
                "custom_components.aegis_ajax.notification_event_parser._resolve_qualifier",
                side_effect=lambda c, *_: {
                    b"\x01": ("arm", {"raw_tag": "space_armed"}),
                    b"\x02": ("disarm", {"raw_tag": "space_disarmed"}),
                }.get(c),
            ),
        ):
            result = listener._extract_event_with_compiled_protos(b"")

        assert result == ("arm", {"raw_tag": "space_armed"})

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

    def test_smartlock_doorbell_pressed_resolves_to_doorbell_pressed(self) -> None:
        # Ajax SmartLock / LockBridge (Yale) with integrated ring button
        # fires its press inside `SmartLockEventQualifier` — disjoint oneof
        # from HubEventTag and VideoEventTag, so the parser needs its own
        # pass (Pass 4 in `_extract_event_with_compiled_protos`) for #158.
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event import (  # noqa: E501
            transition_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.smartlock import (  # noqa: E501
            qualifier_pb2 as smartlock_qualifier_pb2,
        )
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.smartlock import (  # noqa: E501
            tag_pb2 as smartlock_tag_pb2,
        )

        qualifier = smartlock_qualifier_pb2.SmartLockEventQualifier(
            tag=smartlock_tag_pb2.SmartLockEventTag(
                doorbell_pressed=smartlock_tag_pb2.DoorbellPressed()
            ),
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
        assert data["raw_tag"] == "doorbell_pressed"

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


class TestParseAndFireEventLogging:
    """Push events now log `event_type / raw_tag / group_id` at DEBUG (#148).

    Without this line the only way to confirm what the parser extracted from
    a user's payload was to add ad-hoc logging mid-debugging — now the
    standard debug log already shows it.
    """

    def _make_listener(self) -> AjaxNotificationListener:
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        # Event dispatch is now marshaled via call_soon_threadsafe; invoke the
        # callback inline so these tests observe the downstream fire/state-write.
        hass.loop.call_soon_threadsafe.side_effect = lambda cb, *a: cb(*a)
        coordinator = MagicMock()
        coordinator._space_ids = []
        return AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

    def test_log_includes_event_type_raw_tag_and_group_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging as _logging

        listener = self._make_listener()
        encoded = base64.b64encode(b"any-payload").decode()
        notif_logger = "custom_components.aegis_ajax.notification"
        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=(
                    "arm",
                    {"raw_tag": "space_group_armed", "group_id": "g7"},
                ),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(listener, "_extract_space_source_info", return_value={}),
            patch.object(listener, "_find_space_for_event", return_value="space-1"),
            caplog.at_level(_logging.DEBUG, logger=notif_logger),
        ):
            listener._parse_and_fire_event(encoded)

        log_lines = [r.message for r in caplog.records]
        assert any(
            "Push event parsed" in m
            and "event_type=arm" in m
            and "raw_tag=space_group_armed" in m
            and "group_id=g7" in m
            for m in log_lines
        ), f"missing expected debug line in {log_lines}"

    def test_log_shows_none_group_id_for_non_group_events(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging as _logging

        listener = self._make_listener()
        encoded = base64.b64encode(b"any-payload").decode()
        notif_logger = "custom_components.aegis_ajax.notification"
        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("arm", {"raw_tag": "space_armed"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(listener, "_find_space_for_event", return_value="space-1"),
            caplog.at_level(_logging.DEBUG, logger=notif_logger),
        ):
            listener._parse_and_fire_event(encoded)

        assert any(
            "Push event parsed" in r.message and "group_id=None" in r.message
            for r in caplog.records
        )

    def test_group_event_without_group_id_logs_warning_with_hex(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When `space_group_*` is recognised but neither extractor finds
        a `group_id`, we WARN and dump the raw payload hex (#148 beta.6).
        The hex dump survives even after the DisplayGroups fix — if Ajax
        ever ships yet another wire shape, we'll still see the bytes."""
        import logging as _logging

        listener = self._make_listener()
        raw_bytes = b"\xff\xfe\xfd\xfc\xfb"
        encoded = base64.b64encode(raw_bytes).decode()
        notif_logger = "custom_components.aegis_ajax.notification"
        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("disarm", {"raw_tag": "space_group_disarmed"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(listener, "_extract_space_group_info", return_value={}),
            patch.object(listener, "_extract_space_source_info", return_value={}),
            patch.object(listener, "_find_space_for_event", return_value="space-1"),
            caplog.at_level(_logging.WARNING, logger=notif_logger),
        ):
            listener._parse_and_fire_event(encoded)

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "space_group_disarmed" in r.message and raw_bytes.hex() in r.message for r in warnings
        ), f"missing expected WARNING with hex dump, got {[r.message for r in warnings]}"

    def test_group_event_from_display_groups_sets_group_id_silently(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """End-to-end (#148 fix): with a real `DisplayGroups.Group` in the
        payload, the DisplayGroups extractor resolves `group_id` and the
        WARNING path stays silent. Counter-test to ensure the new extractor
        is actually wired into `_parse_and_fire_event`, not just defined."""
        import logging as _logging

        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.space.additional.data import (  # noqa: E501
            display_groups_pb2,
        )

        listener = self._make_listener()
        # Build a payload with a real DisplayGroups embedded; padding before
        # and after makes the scan walk past noise like a real push would.
        display = display_groups_pb2.DisplayGroups(
            groups=[
                display_groups_pb2.DisplayGroups.Group(
                    group_hex_id="00000001", group_name="Out House"
                )
            ]
        )
        raw_bytes = b"\x99\x88\x77" + display.SerializeToString() + b"\x66"
        encoded = base64.b64encode(raw_bytes).decode()

        # Capture event_data so we can assert group_id was routed through.
        captured: dict[str, object] = {}

        def _capture_fire(space_id: str, event_type: str, event_data: dict) -> None:
            captured["space_id"] = space_id
            captured["event_type"] = event_type
            captured["event_data"] = dict(event_data)

        listener._coordinator.fire_push_event = _capture_fire
        notif_logger = "custom_components.aegis_ajax.notification"
        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("disarm", {"raw_tag": "space_group_disarmed"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(listener, "_find_space_for_event", return_value="space-1"),
            caplog.at_level(_logging.WARNING, logger=notif_logger),
        ):
            listener._parse_and_fire_event(encoded)

        # No diagnostic WARNING — the fix worked.
        assert not [r for r in caplog.records if r.levelname == "WARNING"], (
            "DisplayGroups extractor should resolve group_id and silence the warning path"
        )
        # And the group_id arrived in event_data so the coordinator can
        # route to the right per-group alarm panel.
        assert captured["event_data"]["group_id"] == "00000001"
        assert captured["event_data"]["group_name"] == "Out House"

    def test_group_event_with_group_id_does_not_log_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Counter-test: when the source scan resolves a `group_id`, the
        warning path must stay silent — otherwise every healthy group
        push would spam the logs."""
        import logging as _logging

        listener = self._make_listener()
        encoded = base64.b64encode(b"any-payload").decode()
        notif_logger = "custom_components.aegis_ajax.notification"
        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("arm", {"raw_tag": "space_group_armed"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(
                listener,
                "_extract_space_source_info",
                return_value={"group_id": "g7", "group_name": "Studio"},
            ),
            patch.object(listener, "_find_space_for_event", return_value="space-1"),
            caplog.at_level(_logging.WARNING, logger=notif_logger),
        ):
            listener._parse_and_fire_event(encoded)

        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    def test_non_group_event_without_source_does_not_log_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Counter-test: whole-space `space_armed` carries no
        SpaceNotificationSource by design — must not trip the group
        diagnostic warning."""
        import logging as _logging

        listener = self._make_listener()
        encoded = base64.b64encode(b"any-payload").decode()
        notif_logger = "custom_components.aegis_ajax.notification"
        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("arm", {"raw_tag": "space_armed"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(listener, "_extract_space_source_info", return_value={}),
            patch.object(listener, "_find_space_for_event", return_value="space-1"),
            caplog.at_level(_logging.WARNING, logger=notif_logger),
        ):
            listener._parse_and_fire_event(encoded)

        assert not [r for r in caplog.records if r.levelname == "WARNING"]


class TestRedactPrintable:
    """`_redact_printable` masks ASCII text runs in a hex dump (#173, PII)."""

    def test_replaces_long_printable_run_with_marker(self) -> None:
        from custom_components.aegis_ajax.notification import _redact_printable

        out = _redact_printable(b"Deurbel")
        assert "Deurbel" not in out
        assert "<text:7b>" in out

    def test_keeps_binary_bytes_as_hex(self) -> None:
        from custom_components.aegis_ajax.notification import _redact_printable

        out = _redact_printable(b"\x00\x01\x02")
        assert out == "000102"

    def test_short_printable_run_not_masked(self) -> None:
        from custom_components.aegis_ajax.notification import _redact_printable

        # 2 printable bytes stay as hex (too short to be meaningful PII)
        out = _redact_printable(b"\x00AB\x00")
        assert "<text:" not in out
        assert out == "004142" + "00"

    def test_mixed_binary_and_text(self) -> None:
        from custom_components.aegis_ajax.notification import _redact_printable

        out = _redact_printable(b"\xff\xfeHELLO\x00")
        assert out == "fffe" + "<text:5b>" + "00"


class TestParseAndFireEventDeviceRouting:
    """A doorbell/motion push is surfaced on the source device's own card (#173).

    The hub-level event entity still fires for every event; on top of that,
    when the push carries (or resolves to) a device id, the ring is mirrored
    onto a per-device doorbell event entity and motion flips the device's
    motion binary_sensor.
    """

    def _make_listener(self, devices: dict) -> tuple[AjaxNotificationListener, MagicMock]:
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        # Marshaled dispatch: run the scheduled callback inline so the existing
        # assertions on fire_push_device_event still observe the call.
        hass.loop.call_soon_threadsafe.side_effect = lambda cb, *a: cb(*a)
        coordinator = MagicMock()
        coordinator._space_ids = []
        coordinator.devices = devices
        coordinator.fire_push_device_event = MagicMock(return_value=True)
        coordinator.apply_push_device_motion = MagicMock()
        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)
        return listener, coordinator

    def _doorbell_device(self, device_id: str = "310A8DF4") -> MagicMock:
        dev = MagicMock()
        dev.id = device_id
        dev.device_type = "video_edge_doorbell"
        return dev

    def _fire(self, listener: AjaxNotificationListener, event_type: str, source: dict) -> None:
        encoded = base64.b64encode(b"any-payload").decode()
        raw_tag = {
            "doorbell_pressed": "ring_button_pressed",
            "motion": "human_detected",
            "alarm": "intrusion_alarm",
        }[event_type]
        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=(event_type, {"raw_tag": raw_tag}),
            ),
            patch.object(listener, "_extract_source_info", return_value=source),
            patch.object(listener, "_find_space_for_event", return_value="space-1"),
        ):
            listener._parse_and_fire_event(encoded)

    def test_doorbell_with_matching_device_id_fires_device_event(self) -> None:
        listener, coordinator = self._make_listener({"310A8DF4": self._doorbell_device()})

        self._fire(
            listener, "doorbell_pressed", {"device_id": "310A8DF4", "device_name": "Deurbel"}
        )

        coordinator.fire_push_device_event.assert_called_once()
        args = coordinator.fire_push_device_event.call_args[0]
        assert args[0] == "310A8DF4"
        assert args[1] == "doorbell_pressed"

    def test_motion_with_matching_device_id_schedules_device_motion(self) -> None:
        listener, coordinator = self._make_listener({"310A8DF4": self._doorbell_device()})

        self._fire(listener, "motion", {"device_id": "310A8DF4", "device_name": "Deurbel"})

        listener._hass.loop.call_soon_threadsafe.assert_any_call(
            coordinator.apply_push_device_motion, "310A8DF4"
        )

    def test_doorbell_without_device_id_falls_back_to_single_doorbell(self) -> None:
        listener, coordinator = self._make_listener({"310A8DF4": self._doorbell_device()})

        self._fire(listener, "doorbell_pressed", {})

        coordinator.fire_push_device_event.assert_called_once()
        assert coordinator.fire_push_device_event.call_args[0][0] == "310A8DF4"

    def test_motion_without_device_id_does_not_schedule_motion(self) -> None:
        listener, coordinator = self._make_listener({"310A8DF4": self._doorbell_device()})

        self._fire(listener, "motion", {})

        for call in listener._hass.loop.call_soon_threadsafe.call_args_list:
            assert call[0][0] is not coordinator.apply_push_device_motion

    def test_unrelated_event_does_not_dispatch_to_device(self) -> None:
        listener, coordinator = self._make_listener({"310A8DF4": self._doorbell_device()})

        self._fire(listener, "alarm", {"device_id": "310A8DF4"})

        coordinator.fire_push_device_event.assert_not_called()
        for call in listener._hass.loop.call_soon_threadsafe.call_args_list:
            assert call[0][0] is not coordinator.apply_push_device_motion

    def test_motion_resolves_via_twin_alias(self) -> None:
        # The push carries the Jeweller twin id (310A8DF4), which is gone after
        # the #173 dedup; the surviving video_edge sibling is 9c756e2bca39-0.
        # The twin→sibling alias must let motion attribute to the real device
        # (the exact miss in Bruno's #173 capture: resolved=None → hub only).
        listener, coordinator = self._make_listener(
            {"9c756e2bca39-0": self._doorbell_device("9c756e2bca39-0")}
        )
        coordinator.doorbell_twin_aliases = {"310A8DF4": "9c756e2bca39-0"}

        self._fire(listener, "motion", {"device_id": "310A8DF4", "device_name": "Deurbel"})

        listener._hass.loop.call_soon_threadsafe.assert_any_call(
            coordinator.apply_push_device_motion, "9c756e2bca39-0"
        )

    def test_doorbell_resolves_via_twin_alias(self) -> None:
        listener, coordinator = self._make_listener(
            {"9c756e2bca39-0": self._doorbell_device("9c756e2bca39-0")}
        )
        coordinator.doorbell_twin_aliases = {"310A8DF4": "9c756e2bca39-0"}

        self._fire(
            listener, "doorbell_pressed", {"device_id": "310A8DF4", "device_name": "Deurbel"}
        )

        coordinator.fire_push_device_event.assert_called_once()
        assert coordinator.fire_push_device_event.call_args[0][0] == "9c756e2bca39-0"


class TestParseAndFireEventThreadSafety:
    """The FCM callback runs on the firebase_messaging worker thread, so every
    event-entity dispatch (which calls async_write_ha_state / bus.async_fire,
    both loop-only) MUST be marshaled to the loop via call_soon_threadsafe and
    never invoked directly on the worker thread (audit fix)."""

    def _make_listener(self) -> tuple[AjaxNotificationListener, MagicMock]:
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        coordinator = MagicMock()
        coordinator._space_ids = []
        coordinator.fire_push_event = MagicMock()
        coordinator.fire_push_device_event = MagicMock(return_value=True)
        coordinator.devices = {}
        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)
        return listener, coordinator

    @staticmethod
    def _scheduled(listener: AjaxNotificationListener) -> list:
        return [c[0][0] for c in listener._hass.loop.call_soon_threadsafe.call_args_list]

    def test_hub_event_marshaled_to_loop_not_called_directly(self) -> None:
        listener, coordinator = self._make_listener()
        encoded = base64.b64encode(b"any-payload").decode()
        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("arm", {"raw_tag": "space_armed"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(listener, "_find_space_for_event", return_value="space-1"),
        ):
            listener._parse_and_fire_event(encoded)

        # Never called directly on the worker thread …
        coordinator.fire_push_event.assert_not_called()
        # … always scheduled onto the loop instead.
        assert coordinator.fire_push_event in self._scheduled(listener)

    def test_doorbell_device_event_marshaled_to_loop_not_called_directly(self) -> None:
        listener, coordinator = self._make_listener()
        dev = MagicMock()
        dev.id = "310A8DF4"
        dev.device_type = "video_edge_doorbell"
        coordinator.devices = {"310A8DF4": dev}
        encoded = base64.b64encode(b"any-payload").decode()
        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("doorbell_pressed", {"raw_tag": "ring_button_pressed"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={"device_id": "310A8DF4"}),
            patch.object(listener, "_find_space_for_event", return_value="space-1"),
        ):
            listener._parse_and_fire_event(encoded)

        coordinator.fire_push_device_event.assert_not_called()
        assert coordinator.fire_push_device_event in self._scheduled(listener)

    def test_no_dispatch_when_loop_not_running(self) -> None:
        listener, coordinator = self._make_listener()
        listener._hass.loop.is_running.return_value = False
        encoded = base64.b64encode(b"any-payload").decode()
        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("arm", {"raw_tag": "space_armed"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(listener, "_find_space_for_event", return_value="space-1"),
        ):
            listener._parse_and_fire_event(encoded)

        coordinator.fire_push_event.assert_not_called()
        listener._hass.loop.call_soon_threadsafe.assert_not_called()


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
        fresh = _restamp_push(_REAL_PUSH_ENCODED_DATA)

        # Two pushes with the same encoded data → same notification_id.
        listener._on_notification({"ENCODED_DATA": fresh}, "pid-1")
        listener._on_notification({"ENCODED_DATA": fresh}, "pid-2")

        # First push triggered the refresh; second one short-circuited.
        assert hass.loop.call_soon_threadsafe.call_count == 1

    def test_distinct_notification_ids_both_fire(self) -> None:
        listener, hass, _ = self._make_listener()

        # First push uses the canonical real payload (notif_id = …D89E).
        listener._on_notification({"ENCODED_DATA": _restamp_push(_REAL_PUSH_ENCODED_DATA)}, "pid-1")

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
        fresh = _restamp_push(_REAL_PUSH_ENCODED_DATA)

        with patch("custom_components.aegis_ajax.notification.time.monotonic") as monotonic:
            monotonic.return_value = 1000.0
            listener._on_notification({"ENCODED_DATA": fresh}, "pid-1")

            # Second push beyond the dedupe window — should fire again.
            monotonic.return_value = 1000.0 + NOTIFICATION_DEDUPE_WINDOW_SECONDS + 0.1
            listener._on_notification({"ENCODED_DATA": fresh}, "pid-2")

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
            listener._on_notification(
                {"ENCODED_DATA": _restamp_push(_REAL_PUSH_ENCODED_DATA)}, "pid-1"
            )
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


class TestStalePushFilter:
    """Issue #174: FCM redelivers pushes that were buffered server-side after
    a reconnect, sometimes hours later. The Notification proto's
    `server_timestamp` lets us drop the replay before it fires a stale
    'desarmada' (or any other) event on the user's phone."""

    def _make_listener(self) -> tuple[AjaxNotificationListener, MagicMock, MagicMock]:
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        coordinator = MagicMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._space_ids = []
        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)
        return listener, hass, coordinator

    def test_stale_push_is_dropped(self) -> None:
        """A push whose server_timestamp is older than the threshold must not
        fire events nor trigger a coordinator refresh — that is precisely the
        FCM-replay scenario from #174."""
        listener, hass, _ = self._make_listener()
        encoded = _restamp_push(_REAL_PUSH_ENCODED_DATA, seconds_ago=600)

        listener._on_notification({"ENCODED_DATA": encoded}, "pid-stale")

        assert hass.loop.call_soon_threadsafe.call_count == 0
        assert listener._pushes_received == 0

    def test_fresh_push_still_fires(self) -> None:
        """A push with a current server_timestamp must keep firing normally."""
        listener, hass, _ = self._make_listener()
        encoded = _restamp_push(_REAL_PUSH_ENCODED_DATA, seconds_ago=0)

        listener._on_notification({"ENCODED_DATA": encoded}, "pid-fresh")

        assert hass.loop.call_soon_threadsafe.call_count == 1
        assert listener._pushes_received == 1

    def test_push_without_parseable_timestamp_falls_through(self) -> None:
        """Fail-open: if the payload doesn't carry a parseable server_timestamp
        we keep the previous behaviour rather than silently dropping pushes —
        a parser miss must never silence a real event."""
        listener, hass, _ = self._make_listener()
        encoded = base64.b64encode(b"no proto here, just bytes").decode()

        listener._on_notification({"ENCODED_DATA": encoded}, "pid-no-ts")

        assert hass.loop.call_soon_threadsafe.call_count == 1
