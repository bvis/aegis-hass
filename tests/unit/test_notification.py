"""Tests for FCM notification listener."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.notification import (
    AjaxNotificationListener,
    _classify_fcm_failure,
    _fcm_creds_hash,
    _validate_fcm_shape,
    async_probe_fcm_refusal_reason,
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

        # `async_start` imports the *submodule* `firebase_messaging.fcmregister`,
        # and a cached submodule is importable even when the parent key is None.
        # Nulling only the parent therefore stops simulating an absent package
        # as soon as anything else in the session has imported it, which let
        # this test fall through into the real Store. Null both.
        with patch.dict(
            "sys.modules",
            {"firebase_messaging": None, "firebase_messaging.fcmregister": None},
        ):
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
        expected_hash = _fcm_creds_hash(
            fcm_project_id=_FCM_KWARGS["fcm_project_id"],
            fcm_app_id=_FCM_KWARGS["fcm_app_id"],
            fcm_api_key=_FCM_KWARGS["fcm_api_key"],
            fcm_sender_id=_FCM_KWARGS["fcm_sender_id"],
        )
        listener._store.async_load = AsyncMock(
            return_value={
                "fcm": {"registration": {"token": "tok"}},
                "creds_hash": expected_hash,
            }
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
            side_effect=RuntimeError("Unable to register with fcm")
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
    async def test_gcm_register_failure_does_not_persist_rejected_hash(self) -> None:
        """#464 — the GCM register step does not carry the four values, so its
        failure must stay retryable on the next restart / reload instead of
        being remembered as a credential rejection. wip3out3r's install hit
        PHONE_REGISTRATION_ERROR twice, 1 s apart, right after the #458
        migration forced a re-registration; the same four values registered
        fine 11m40s later once the marker was deleted by hand."""
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
            reg_inv as reg,
            clr_inv,
            reg_miss,
            clr_miss,
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
        ):
            await listener.async_start()

        # The attempt was made and failed…
        register_cls.assert_called_once()
        # …the user is still told push is off…
        reg.assert_called_once_with(hass, entry_id="entry-x")
        # …but nothing is latched: the next start retries.
        listener._rejected_store.async_save.assert_not_called()

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

    @pytest.mark.asyncio
    async def test_missing_fingerprint_is_reported_as_the_one_time_migration(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """#464 (side note) — a cache with no `creds_hash` is the pre-1.19.0
        shape every upgrading install has once. Telling that user their
        credential set is "different" is wrong and alarming; say what it is."""
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(
            return_value={"fcm": {"registration": {"token": "fresh"}}}
        )
        listener = self._listener(hass)
        listener._store.async_load = AsyncMock(
            return_value={"fcm": {"registration": {"token": "pre-1.19"}}}
        )
        listener._store.async_save = AsyncMock()
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
            caplog.at_level(logging.INFO, logger="custom_components.aegis_ajax.notification"),
        ):
            await listener.async_start()

        assert "no credential fingerprint" in caplog.text
        assert "different credential set" not in caplog.text

    @pytest.mark.asyncio
    async def test_changed_fingerprint_is_still_reported_as_a_different_set(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(
            return_value={"fcm": {"registration": {"token": "fresh"}}}
        )
        listener = self._listener(hass)
        listener._store.async_load = AsyncMock(
            return_value={
                "fcm": {"registration": {"token": "old"}},
                "creds_hash": "made-with-other-values",
            }
        )
        listener._store.async_save = AsyncMock()
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
            caplog.at_level(logging.INFO, logger="custom_components.aegis_ajax.notification"),
        ):
            await listener.async_start()

        assert "different credential set" in caplog.text
        assert "no credential fingerprint" not in caplog.text

    @pytest.mark.asyncio
    async def test_reregisters_when_stored_credentials_lack_token(self) -> None:
        """An incomplete stored registration cannot receive Ajax pushes."""
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(
            return_value={"fcm": {"registration": {"token": "replacement-token"}}}
        )
        listener = self._listener(hass)
        listener._store.async_load = AsyncMock(
            return_value={"fcm": {"registration": None, "installation": {"fid": "old"}}}
        )
        listener._store.async_save = AsyncMock()
        listener._register_push_token = AsyncMock()
        register_cls = MagicMock(return_value=MagicMock(register=MagicMock()))

        reg_inv, clr_inv, reg_miss, clr_miss = self._repair_patches()
        with (
            reg_inv,
            clr_inv,
            reg_miss,
            clr_miss,
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
            patch("firebase_messaging.FcmPushClient", MagicMock()),
        ):
            await listener.async_start()

        register_cls.assert_called_once()
        listener._store.async_save.assert_awaited_once()
        listener._register_push_token.assert_awaited_once_with("replacement-token")


class TestFcmCacheFingerprinting:
    """Issue #452 — invalidate cached FCM registration on credential changes."""

    @staticmethod
    def _listener(hass: MagicMock) -> AjaxNotificationListener:
        listener = AjaxNotificationListener(
            hass=hass, coordinator=MagicMock(), **_FCM_KWARGS, entry_id="entry-x"
        )
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
    async def test_valid_cache_with_matching_creds_hash_skips_reregistration(self) -> None:
        hass = MagicMock()
        listener = self._listener(hass)
        expected_hash = self._expected_hash()
        listener._store.async_load = AsyncMock(
            return_value={
                "fcm": {"registration": {"token": "cached-token"}},
                "creds_hash": expected_hash,
            }
        )
        listener._store.async_save = AsyncMock()
        listener._register_push_token = AsyncMock()
        register_cls = MagicMock()

        reg_inv, clr_inv, reg_miss, clr_miss = self._repair_patches()
        with (
            reg_inv,
            clr_inv,
            reg_miss,
            clr_miss,
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
            patch("firebase_messaging.FcmPushClient", MagicMock()),
        ):
            await listener.async_start()

        register_cls.assert_not_called()
        listener._register_push_token.assert_awaited_once_with("cached-token")
        assert listener.cache_creds_fingerprint == expected_hash[:16]

    @pytest.mark.asyncio
    async def test_adopts_a_pre_1_19_cache_instead_of_reregistering(self) -> None:
        """#487 — the working registration of an upgrading install is KEPT.

        This asserts the opposite of what it used to: 1.19.0 discarded a cache
        with no fingerprint and registered again, which put every upgrading
        install through the one step that can fail. Two reporters lost push on
        upgrade and got it back by reverting, because the old cache file was
        still on disk for 1.18.0 to reuse. The fingerprint exists to detect a
        *changed* credential set; absent, the 1.18.0 assumption — this token
        belongs to the configured values — is the safe one.
        """
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(
            return_value={"fcm": {"registration": {"token": "new-token"}}}
        )
        listener = self._listener(hass)
        expected_hash = self._expected_hash()
        listener._store.async_load = AsyncMock(
            return_value={"fcm": {"registration": {"token": "old-token"}}}
        )
        listener._store.async_save = AsyncMock()
        listener._register_push_token = AsyncMock()
        register_cls = MagicMock(return_value=MagicMock(register=MagicMock()))

        reg_inv, clr_inv, reg_miss, clr_miss = self._repair_patches()
        with (
            reg_inv,
            clr_inv,
            reg_miss,
            clr_miss,
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
            patch("firebase_messaging.FcmPushClient", MagicMock()),
        ):
            await listener.async_start()

        register_cls.assert_not_called()
        listener._register_push_token.assert_awaited_once_with("old-token")
        # The fingerprint is stamped and persisted, so the next start is an
        # ordinary cache hit and a real credential change stays detectable.
        listener._store.async_save.assert_awaited_once_with(
            {"fcm": {"registration": {"token": "old-token"}}, "creds_hash": expected_hash}
        )
        assert listener.cache_creds_fingerprint == expected_hash[:16]

    @pytest.mark.asyncio
    async def test_adoption_does_not_touch_a_cache_without_a_token(self) -> None:
        """A cache with no token has nothing to adopt — it must still register."""
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(
            return_value={"fcm": {"registration": {"token": "new-token"}}}
        )
        listener = self._listener(hass)
        expected_hash = self._expected_hash()
        listener._store.async_load = AsyncMock(return_value={"fcm": {"registration": {}}})
        listener._store.async_save = AsyncMock()
        listener._register_push_token = AsyncMock()
        register_cls = MagicMock(return_value=MagicMock(register=MagicMock()))

        reg_inv, clr_inv, reg_miss, clr_miss = self._repair_patches()
        with (
            reg_inv,
            clr_inv,
            reg_miss,
            clr_miss,
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
            patch("firebase_messaging.FcmPushClient", MagicMock()),
        ):
            await listener.async_start()

        register_cls.assert_called_once()
        listener._store.async_save.assert_awaited_once_with(
            {"fcm": {"registration": {"token": "new-token"}}, "creds_hash": expected_hash}
        )
        listener._register_push_token.assert_awaited_once_with("new-token")

    @pytest.mark.asyncio
    async def test_reregisters_when_stored_creds_hash_mismatches(self) -> None:
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(
            return_value={"fcm": {"registration": {"token": "new-token"}}}
        )
        listener = self._listener(hass)
        expected_hash = self._expected_hash()
        listener._store.async_load = AsyncMock(
            return_value={
                "fcm": {"registration": {"token": "old-token"}},
                "creds_hash": "different-hash-from-previous-creds",
            }
        )
        listener._store.async_save = AsyncMock()
        listener._register_push_token = AsyncMock()
        register_cls = MagicMock(return_value=MagicMock(register=MagicMock()))

        reg_inv, clr_inv, reg_miss, clr_miss = self._repair_patches()
        with (
            reg_inv,
            clr_inv,
            reg_miss,
            clr_miss,
            patch("firebase_messaging.fcmregister.FcmRegister", register_cls),
            patch("firebase_messaging.FcmPushClient", MagicMock()),
        ):
            await listener.async_start()

        register_cls.assert_called_once()
        listener._store.async_save.assert_awaited_once_with(
            {"fcm": {"registration": {"token": "new-token"}}, "creds_hash": expected_hash}
        )
        listener._register_push_token.assert_awaited_once_with("new-token")

    @pytest.mark.asyncio
    async def test_rejected_store_check_runs_before_cache_reregistration(self) -> None:
        hass = MagicMock()
        listener = self._listener(hass)
        expected_hash = self._expected_hash()
        listener._store.async_load = AsyncMock(
            return_value={
                "fcm": {"registration": {"token": "stale-token"}},
                "creds_hash": "old-hash",
            }
        )
        listener._rejected_store.async_load = AsyncMock(return_value={"hash": expected_hash})
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

        register_cls.assert_not_called()
        reg.assert_called_once_with(hass, entry_id="entry-x")


class TestIsTerminalFcmFailure:
    def test_firebase_installations_rejection_is_terminal(self) -> None:
        # The Firebase Installations request is the one that carries the
        # api-key, so a refusal there IS a verdict on the credentials (#182,
        # #227) and stays latched until the values change.
        from custom_components.aegis_ajax.notification import _is_terminal_fcm_failure

        assert _is_terminal_fcm_failure(RuntimeError("Unable to register with fcm"))

    def test_gcm_register_failure_is_not_terminal(self) -> None:
        # #464: `register()` raises this string whenever `gcm_register()` gives
        # up after its two tries. That request carries the library's default
        # bundle_id, the android_id from check-in and the library's own
        # constant server key — none of the four user values, which are only
        # used afterwards in `fcm_install_and_register`. A failure there can
        # never be a credential verdict, and on the reporter's install the
        # identical values succeeded 12 minutes later untouched. Latching it
        # made push unrecoverable without deleting a file by hand.
        from custom_components.aegis_ajax.notification import _is_terminal_fcm_failure

        assert not _is_terminal_fcm_failure(
            RuntimeError("Unable to establish subscription with Google Cloud Messaging.")
        )

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

    def test_gcm_register_failure_is_not_presented_as_a_credential_verdict(self) -> None:
        # #464: the GCM register request carries none of the four values (read
        # from `fcmregister.gcm_register`: default bundle_id, check-in
        # android_id, the library's constant server key), so telling the user
        # Google "rejected" their credentials and to re-enter all four was
        # wrong — and, with the marker latched, sent them into a loop where
        # the prescribed remedy could not work. The message must say which
        # step failed, that it is not about the values, and that it is retried
        # on the next restart / reload. Still no extraction internals.
        msg = _classify_fcm_failure(
            RuntimeError("Unable to establish subscription with Google Cloud Messaging.")
        )
        assert "rejected by Google" not in msg
        assert "GCM registration step" in msg
        assert "none of the four" in msg
        assert "restart or reload" in msg
        assert "Repair card" in msg
        for forbidden in ("APK", "cobrand", "libnative", "strings.xml"):
            assert forbidden not in msg

    def test_fcm_install_failure_presents_both_causes_without_picking_one(self) -> None:
        # This branch fires on a Firebase Installations 403, which has TWO
        # causes needing OPPOSITE fixes — and the library hides which one
        # (#344). The message used to assert the wrong-`AIza` explanation for
        # both, sending users who had the right key chasing key extraction.
        # It must now describe both and defer to the probe's follow-up line.
        msg = _classify_fcm_failure(RuntimeError("Unable to register with fcm"))
        assert "API_KEY_ANDROID_APP_BLOCKED" in msg
        assert "API_KEY_SERVICE_BLOCKED" in msg
        assert "API_KEY_INVALID" in msg
        # #344's second key returned a bare PERMISSION_DENIED — a fourth cause,
        # and the only one that is neither a wrong key nor a restriction.
        assert "PERMISSION_DENIED" in msg
        assert "next log line" in msg
        assert "Repair card" in msg
        # #344 arrived as HTTP 400, not the 403 this message used to assert.
        # Don't name a status the branch can't guarantee.
        assert "403" not in msg

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


class TestProbeFcmRefusalReason:
    """`async_probe_fcm_refusal_reason` reads the reason the library discards.

    `firebase_messaging` collapses `API_KEY_SERVICE_BLOCKED` (wrong key) and
    `API_KEY_ANDROID_APP_BLOCKED` (right key, restriction rejected our request)
    into one error string and drops the HTTP body that tells them apart — so
    every "FCM credentials rejected" report cost a round trip with the reporter
    to establish which one they hit (#344). This re-issues the same call purely
    to read the reason back.
    """

    def _session(self, *, status: int = 403, body: object = None, raises: object = None):  # noqa: ANN202
        response = MagicMock()
        response.status = status
        response.json = AsyncMock(return_value=body)
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post = AsyncMock(return_value=response, side_effect=raises)
        return session

    def _body(self, reason: str | None, message: str = "Requests are blocked.") -> dict:
        details = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}]
        return {
            "error": {
                "code": 403,
                "message": message,
                "status": "PERMISSION_DENIED",
                **({"details": details} if reason else {}),
            }
        }

    @pytest.mark.asyncio
    async def test_returns_the_structured_reason_and_message(self) -> None:
        session = self._session(body=self._body("API_KEY_SERVICE_BLOCKED"))

        reason, message = await async_probe_fcm_refusal_reason(
            session,
            fcm_project_id="p",
            fcm_app_id="1:1:android:ab",
            fcm_api_key="AIza-key",
            android_package="com.ajaxsystems",
        )

        assert reason == "API_KEY_SERVICE_BLOCKED"
        assert message == "Requests are blocked."

    @pytest.mark.asyncio
    async def test_sends_the_api_key_and_package_google_checks(self) -> None:
        session = self._session(body=self._body("API_KEY_ANDROID_APP_BLOCKED"))

        await async_probe_fcm_refusal_reason(
            session,
            fcm_project_id="my-project",
            fcm_app_id="1:1:android:ab",
            fcm_api_key="AIza-key",
            android_package="com.ajaxsystems",
        )

        url = session.post.call_args[0][0]
        headers = session.post.call_args[1]["headers"]
        assert "my-project" in url
        assert headers["x-goog-api-key"] == "AIza-key"
        # The package is the thing under test in the ANDROID_APP_BLOCKED case:
        # probing without it would answer a different question than the one
        # the failed registration asked.
        assert headers["X-Android-Package"] == "com.ajaxsystems"

    @pytest.mark.asyncio
    async def test_omits_the_package_header_when_unknown(self) -> None:
        session = self._session(body=self._body("API_KEY_ANDROID_APP_BLOCKED"))

        await async_probe_fcm_refusal_reason(
            session,
            fcm_project_id="p",
            fcm_app_id="1:1:android:ab",
            fcm_api_key="AIza-key",
            android_package=None,
        )

        assert "X-Android-Package" not in session.post.call_args[1]["headers"]

    @pytest.mark.asyncio
    async def test_status_is_the_reason_when_there_is_no_error_info(self) -> None:
        # #344 second key: `{"code": 403, "message": "The caller does not have
        # permission", "status": "PERMISSION_DENIED"}` — no `details` at all.
        # Falling back to the status is what separates a key Google recognises
        # but won't authorise from one it doesn't recognise (API_KEY_INVALID);
        # without it this drops into the generic "please report this" branch and
        # the distinction is lost.
        session = self._session(
            body=self._body(None, message="The caller does not have permission")
        )

        reason, message = await async_probe_fcm_refusal_reason(
            session,
            fcm_project_id="p",
            fcm_app_id="1:1:android:ab",
            fcm_api_key="AIza-key",
            android_package=None,
        )

        assert reason == "PERMISSION_DENIED"
        assert message == "The caller does not have permission"

    @pytest.mark.asyncio
    async def test_structured_reason_wins_over_the_status(self) -> None:
        # An `ErrorInfo` reason is the specific answer; the status is the
        # coarse one. A 403 body carries both, and the specific one must win.
        session = self._session(body=self._body("API_KEY_SERVICE_BLOCKED"))

        reason, _ = await async_probe_fcm_refusal_reason(
            session,
            fcm_project_id="p",
            fcm_app_id="1:1:android:ab",
            fcm_api_key="AIza-key",
            android_package=None,
        )

        assert reason == "API_KEY_SERVICE_BLOCKED"

    @pytest.mark.asyncio
    async def test_message_survives_a_body_with_neither_reason_nor_status(self) -> None:
        # Google's error shapes vary; the sentence still names the package it
        # saw, which is the actionable half.
        session = self._session(
            body={"error": {"code": 403, "message": "application <empty> blocked"}}
        )

        reason, message = await async_probe_fcm_refusal_reason(
            session,
            fcm_project_id="p",
            fcm_app_id="1:1:android:ab",
            fcm_api_key="AIza-key",
            android_package=None,
        )

        assert reason is None
        assert message == "application <empty> blocked"

    @pytest.mark.asyncio
    async def test_reads_a_400_not_only_a_403(self) -> None:
        # #344 came back as HTTP 400 / API_KEY_INVALID, not the 403 this probe
        # was written for. An earlier revision parsed 403s only and stayed
        # silent on the exact report that motivated it — the status code is
        # not part of the contract, the error body is.
        session = self._session(status=400, body=self._body("API_KEY_INVALID"))

        reason, _ = await async_probe_fcm_refusal_reason(
            session,
            fcm_project_id="p",
            fcm_app_id="1:1:android:ab",
            fcm_api_key="AIza-key",
            android_package=None,
        )

        assert reason == "API_KEY_INVALID"

    @pytest.mark.asyncio
    async def test_success_answers_nothing(self) -> None:
        # A 2xx means the probe isn't reproducing the failure it was called
        # about, so it has nothing trustworthy to say.
        session = self._session(status=200, body={})

        assert await async_probe_fcm_refusal_reason(
            session,
            fcm_project_id="p",
            fcm_app_id="1:1:android:ab",
            fcm_api_key="AIza-key",
            android_package=None,
        ) == (None, None)

    @pytest.mark.asyncio
    async def test_transport_error_answers_nothing_instead_of_raising(self) -> None:
        # This runs while a failure is already being handled — a diagnostic
        # that breaks startup would be worse than no diagnostic.
        session = self._session(raises=OSError("network down"))

        assert await async_probe_fcm_refusal_reason(
            session,
            fcm_project_id="p",
            fcm_app_id="1:1:android:ab",
            fcm_api_key="AIza-key",
            android_package=None,
        ) == (None, None)

    @pytest.mark.asyncio
    async def test_unexpected_body_shape_answers_nothing(self) -> None:
        session = self._session(body=["not", "a", "dict"])

        assert await async_probe_fcm_refusal_reason(
            session,
            fcm_project_id="p",
            fcm_app_id="1:1:android:ab",
            fcm_api_key="AIza-key",
            android_package=None,
        ) == (None, None)


class TestLogFcmRefusalReason:
    """The listener turns the probe's answer into an actionable log line (#344)."""

    def _listener(self, app_label: str = "Ajax") -> AjaxNotificationListener:
        return AjaxNotificationListener(
            hass=MagicMock(),
            coordinator=MagicMock(),
            **_VALID_FCM_SHAPES,
            entry_id="entry-x",
            app_label=app_label,
        )

    @pytest.mark.asyncio
    async def test_service_blocked_tells_the_user_to_change_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        listener = self._listener()
        with (
            patch(
                "custom_components.aegis_ajax.notification.async_probe_fcm_refusal_reason",
                AsyncMock(return_value=("API_KEY_SERVICE_BLOCKED", "blocked")),
            ),
            caplog.at_level(logging.WARNING, logger="custom_components.aegis_ajax.notification"),
        ):
            await listener._async_log_fcm_refusal_reason(
                RuntimeError("Unable to register with fcm"), "com.ajaxsystems"
            )

        assert "API_KEY_SERVICE_BLOCKED" in caplog.text
        assert "not scoped for FCM" in caplog.text

    @pytest.mark.asyncio
    async def test_invalid_key_is_neither_scope_nor_restriction(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The reason #344 actually returned. A third remedy: Google doesn't
        # know the string at all, so telling the user about FCM scopes or
        # package restrictions would send them the wrong way twice over.
        listener = self._listener()
        with (
            patch(
                "custom_components.aegis_ajax.notification.async_probe_fcm_refusal_reason",
                AsyncMock(return_value=("API_KEY_INVALID", "API key not valid.")),
            ),
            caplog.at_level(logging.WARNING, logger="custom_components.aegis_ajax.notification"),
        ):
            await listener._async_log_fcm_refusal_reason(
                RuntimeError("Unable to register with fcm"), "com.ajaxsystems"
            )

        assert "API_KEY_INVALID" in caplog.text
        assert "does not recognise this string" in caplog.text
        assert "truncation" in caplog.text

    @pytest.mark.asyncio
    async def test_app_blocked_points_at_the_package_not_the_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The opposite remedy: telling this user to extract another key is
        # exactly the wrong advice, which is the whole point of #344.
        listener = self._listener()
        with (
            patch(
                "custom_components.aegis_ajax.notification.async_probe_fcm_refusal_reason",
                AsyncMock(return_value=("API_KEY_ANDROID_APP_BLOCKED", "blocked")),
            ),
            caplog.at_level(logging.WARNING, logger="custom_components.aegis_ajax.notification"),
        ):
            await listener._async_log_fcm_refusal_reason(
                RuntimeError("Unable to register with fcm"), "com.ajaxsystems"
            )

        assert "may be correct" in caplog.text
        assert "com.ajaxsystems" in caplog.text
        assert "app label chosen during setup" in caplog.text

    @pytest.mark.asyncio
    async def test_permission_denied_points_at_mixed_builds_not_the_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # #344's *second* key: a real key Google won't authorise for this
        # project's app. The advice must not send the user hunting for another
        # `AIza…` (that's the API_KEY_INVALID remedy) nor at the app label
        # (that's the ANDROID_APP_BLOCKED one) — the api-key is the one value no
        # offline check can tie to the other three.
        listener = self._listener()
        with (
            patch(
                "custom_components.aegis_ajax.notification.async_probe_fcm_refusal_reason",
                AsyncMock(
                    return_value=("PERMISSION_DENIED", "The caller does not have permission")
                ),
            ),
            caplog.at_level(logging.WARNING, logger="custom_components.aegis_ajax.notification"),
        ):
            await listener._async_log_fcm_refusal_reason(
                RuntimeError("Unable to register with fcm"), "com.ajaxsystems"
            )

        assert "PERMISSION_DENIED" in caplog.text
        assert "same app build" in caplog.text
        # Must not fall through to the useless generic branch.
        assert "please include this line when reporting" not in caplog.text

    @pytest.mark.asyncio
    async def test_app_blocked_says_when_no_package_was_sent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # An unmapped co-brand sends no package at all — a blank-package
        # request is refused whatever key the user extracted, so the log has
        # to say the header was missing rather than name a package.
        listener = self._listener(app_label="Yavir")
        with (
            patch(
                "custom_components.aegis_ajax.notification.async_probe_fcm_refusal_reason",
                AsyncMock(return_value=("API_KEY_ANDROID_APP_BLOCKED", "blocked")),
            ),
            caplog.at_level(logging.WARNING, logger="custom_components.aegis_ajax.notification"),
        ):
            await listener._async_log_fcm_refusal_reason(
                RuntimeError("Unable to register with fcm"), None
            )

        assert "<none sent>" in caplog.text

    @pytest.mark.asyncio
    async def test_unknown_reason_asks_for_the_line_in_the_report(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        listener = self._listener()
        with (
            patch(
                "custom_components.aegis_ajax.notification.async_probe_fcm_refusal_reason",
                AsyncMock(return_value=("SOMETHING_NEW", "blocked")),
            ),
            caplog.at_level(logging.WARNING, logger="custom_components.aegis_ajax.notification"),
        ):
            await listener._async_log_fcm_refusal_reason(
                RuntimeError("Unable to register with fcm"), "com.ajaxsystems"
            )

        assert "SOMETHING_NEW" in caplog.text
        assert "include this line when reporting" in caplog.text

    @pytest.mark.asyncio
    async def test_other_failures_do_not_probe(self) -> None:
        # The network and project-mismatch branches already say everything
        # they can; an extra request there is pure noise.
        listener = self._listener()
        probe = AsyncMock()
        with patch(
            "custom_components.aegis_ajax.notification.async_probe_fcm_refusal_reason", probe
        ):
            await listener._async_log_fcm_refusal_reason(
                RuntimeError("Unable to register and check in to gcm"), "com.ajaxsystems"
            )

        probe.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_answer_logs_no_extra_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        listener = self._listener()
        with (
            patch(
                "custom_components.aegis_ajax.notification.async_probe_fcm_refusal_reason",
                AsyncMock(return_value=(None, None)),
            ),
            caplog.at_level(logging.WARNING, logger="custom_components.aegis_ajax.notification"),
        ):
            await listener._async_log_fcm_refusal_reason(
                RuntimeError("Unable to register with fcm"), "com.ajaxsystems"
            )

        assert caplog.text == ""

    @pytest.mark.asyncio
    async def test_probe_exception_never_escapes(self) -> None:
        # Runs inside the handler for a failure that is already being reported.
        listener = self._listener()
        with patch(
            "custom_components.aegis_ajax.notification.async_probe_fcm_refusal_reason",
            AsyncMock(side_effect=RuntimeError("probe exploded")),
        ):
            await listener._async_log_fcm_refusal_reason(
                RuntimeError("Unable to register with fcm"), "com.ajaxsystems"
            )


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
        # `battery_low` fires an HA event but implies no security-state change
        # and is not an intrusion alarm — nothing to dispatch.
        listener, hass, _ = self._make_listener()

        listener._apply_security_state_from_event("space-1", {"raw_tag": "battery_low"})

        hass.loop.call_soon_threadsafe.assert_not_called()

    def test_intrusion_alarm_tag_dispatches_alarm_overlay(self) -> None:
        # #426: the served SecurityState has no alarm value, so the intrusion
        # push marks the space in-alarm instead of writing a state.
        listener, hass, coordinator = self._make_listener()

        listener._apply_security_state_from_event("space-1", {"raw_tag": "intrusion_alarm"})

        hass.loop.call_soon_threadsafe.assert_called_once_with(
            coordinator.note_intrusion_alarm, "space-1"
        )

    def test_intrusion_alarm_confirmed_tag_dispatches_alarm_overlay(self) -> None:
        listener, hass, coordinator = self._make_listener()

        listener._apply_security_state_from_event(
            "space-1", {"raw_tag": "intrusion_alarm_confirmed"}
        )

        hass.loop.call_soon_threadsafe.assert_called_once_with(
            coordinator.note_intrusion_alarm, "space-1"
        )

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


class TestSecurityEventSnapshotNudge:
    """FCM security events must nudge the snapshot-backed re-read (#284/#287).

    `apply_push_security_state` keeps the space panel instant, but a scenario /
    keypad / fob action can flip several groups at once plus
    `night_mode_enabled` — state only `get_space_snapshot` carries. Before this
    nudge, only the HTS 0x08 path forced the snapshot, so on push-only signals
    the group panels lagged until the hourly snapshot (~9 min observed on a
    scenario-driven arm, wip3out3r in #287).
    """

    def _make_listener(self) -> AjaxNotificationListener:
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        hass.loop.call_soon_threadsafe.side_effect = lambda cb, *a: cb(*a)
        coordinator = MagicMock()
        coordinator._space_ids = []
        return AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

    def _fire(self, listener: AjaxNotificationListener, event_type: str, raw_tag: str) -> None:
        encoded = base64.b64encode(b"any-payload").decode()
        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=(event_type, {"raw_tag": raw_tag}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(listener, "_find_space_for_event", return_value="space-1"),
        ):
            listener._parse_and_fire_event(encoded)

    def test_arm_event_nudges_snapshot_refresh(self) -> None:
        listener = self._make_listener()
        self._fire(listener, "arm", "arm")
        listener._coordinator.request_security_snapshot_refresh.assert_called_once()

    def test_all_security_event_types_nudge(self) -> None:
        from custom_components.aegis_ajax.const import SECURITY_STATE_EVENT_TYPES

        assert {"arm", "disarm", "arm_night", "disarm_night"} == SECURITY_STATE_EVENT_TYPES
        for event_type in sorted(SECURITY_STATE_EVENT_TYPES):
            listener = self._make_listener()
            self._fire(listener, event_type, "whatever_tag")
            listener._coordinator.request_security_snapshot_refresh.assert_called_once()

    def test_non_security_event_does_not_nudge(self) -> None:
        listener = self._make_listener()
        self._fire(listener, "doorbell_pressed", "doorbell_press")
        listener._coordinator.request_security_snapshot_refresh.assert_not_called()

    def test_alarm_push_schedules_its_image_import(self) -> None:
        listener = self._make_listener()
        encoded = base64.b64encode(b"any-payload").decode()
        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("alarm", {"raw_tag": "intrusion_alarm"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(listener, "_find_space_for_event", return_value="space-1"),
        ):
            listener._parse_and_fire_event(encoded, notification_id="alarm-notification-id")

        listener._coordinator.schedule_alarm_image_import.assert_called_once_with(
            "space-1", "alarm-notification-id"
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


class TestUnresolvedPushStillQueriesTheApi:
    """A push that resolves to no event must still trigger the re-read.

    This is what keeps state correct for everything the event vocabulary
    doesn't cover: the push is the "something happened" signal and the
    coordinator refresh is what asks Ajax what it actually was. It has to
    stay independent of event resolution — the parser reporting nothing
    (unmapped tag, unmapped content type, undecodable payload) must never
    also mean "don't look".
    """

    def _make_listener(self) -> tuple[AjaxNotificationListener, MagicMock, MagicMock]:
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        coordinator = MagicMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator._space_ids = []
        listener = AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)
        return listener, hass, coordinator

    def test_real_photo_push_resolves_no_event_but_refreshes(self) -> None:
        # The captured payload is a photo-on-demand notification whose hub
        # tag maps to no HA event (it used to be reported as a phantom
        # `arm_night`). No event fires; the refresh still does.
        listener, hass, coordinator = self._make_listener()
        raw = base64.b64decode(_REAL_PUSH_ENCODED_DATA)
        assert listener._extract_event_with_compiled_protos(raw) is None

        listener._on_notification({"ENCODED_DATA": _restamp_push(_REAL_PUSH_ENCODED_DATA)}, "pid-1")

        coordinator.fire_push_event.assert_not_called()
        assert hass.loop.call_soon_threadsafe.call_count == 1

    def test_undecodable_push_still_refreshes(self) -> None:
        listener, hass, coordinator = self._make_listener()

        listener._on_notification({"ENCODED_DATA": base64.b64encode(b"not protobuf").decode()}, "p")

        coordinator.fire_push_event.assert_not_called()
        assert hass.loop.call_soon_threadsafe.call_count == 1


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


class TestPushSpaceRouting:
    """A push must reach only the space that produced it (#358).

    `_parse_and_fire_event` routes on `_find_space_for_event`; when that
    returns None it used to fan the event out to *every* space. On a
    single-space install the fan-out is silently correct, so the defect
    only surfaces with several Ajax systems on one account: one hub's
    arm/disarm fired every hub's event entity, and every device trigger
    scoped to any of them ran.
    """

    # The real capture in `_REAL_PUSH_ENCODED_DATA` belongs to this space.
    # Its hub's `HubOrigin.hex_id` is "E5F6A7B8", which — decisively — does
    # NOT appear in the payload as raw bytes, which is why the old
    # `bytes.fromhex(hub_id) in raw` scan could never route it.
    _CAPTURED_SPACE_ID = "aabb11223344556677889900"
    _CAPTURED_HUB_ID = "E5F6A7B8"

    def _make_listener(self, spaces: dict[str, str]) -> AjaxNotificationListener:
        """Listener over a coordinator holding `{space_id: hub_id}`."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.is_running.return_value = True
        hass.loop.call_soon_threadsafe.side_effect = lambda cb, *a: cb(*a)
        coordinator = MagicMock()
        coordinator.spaces = {
            space_id: MagicMock(id=space_id, hub_id=hub_id) for space_id, hub_id in spaces.items()
        }
        coordinator._space_ids = list(spaces)
        return AjaxNotificationListener(hass=hass, coordinator=coordinator, **_FCM_KWARGS)

    def test_routes_by_notification_space_id(self) -> None:
        """The payload's `Notification.space.id` identifies the space.

        Would fail if routing went back to scanning for the hub id's raw
        bytes: that substring is absent from this genuine capture.
        """
        listener = self._make_listener(
            {
                "1111111111111111111111aa": "AAAAAAAA",
                self._CAPTURED_SPACE_ID: self._CAPTURED_HUB_ID,
                "3333333333333333333333cc": "CCCCCCCC",
            }
        )
        raw = base64.b64decode(_REAL_PUSH_ENCODED_DATA)

        assert listener._find_space_for_event(raw) == self._CAPTURED_SPACE_ID

    def test_unroutable_push_does_not_fan_out_to_every_space(self) -> None:
        """#358: with several spaces, an unroutable push fires none of them.

        Broadcasting stamps each copy with a *genuine* hub_id, so the
        device trigger's own filter cannot save us — every scoped
        automation matches its own legitimate-looking event.
        """
        listener = self._make_listener(
            {
                "1111111111111111111111aa": "AAAAAAAA",
                "2222222222222222222222bb": "BBBBBBBB",
            }
        )
        encoded = base64.b64encode(b"unroutable payload").decode()

        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("disarm", {"raw_tag": "space_disarmed"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(listener, "_find_space_for_event", return_value=None),
        ):
            listener._parse_and_fire_event(encoded)

        listener._coordinator.fire_push_event.assert_not_called()

    def test_unroutable_push_does_not_restate_every_space(self) -> None:
        """The fan-out also applied the security state to every space, so
        disarming one hub briefly showed all of them disarmed."""
        listener = self._make_listener(
            {
                "1111111111111111111111aa": "AAAAAAAA",
                "2222222222222222222222bb": "BBBBBBBB",
            }
        )
        encoded = base64.b64encode(b"unroutable payload").decode()

        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("disarm", {"raw_tag": "space_disarmed"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(listener, "_find_space_for_event", return_value=None),
            patch.object(listener, "_apply_security_state_from_event") as apply_state,
        ):
            listener._parse_and_fire_event(encoded)

        apply_state.assert_not_called()

    def test_unroutable_push_on_single_space_still_fires(self) -> None:
        """No regression for the overwhelmingly common install: with one
        space the destination is unambiguous even when routing fails."""
        listener = self._make_listener({"1111111111111111111111aa": "AAAAAAAA"})
        encoded = base64.b64encode(b"unroutable payload").decode()

        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("disarm", {"raw_tag": "space_disarmed"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(listener, "_find_space_for_event", return_value=None),
        ):
            listener._parse_and_fire_event(encoded)

        listener._coordinator.fire_push_event.assert_called_once()
        assert listener._coordinator.fire_push_event.call_args[0][0] == "1111111111111111111111aa"

    def test_unroutable_multi_space_push_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Dropping an event is degraded behaviour, so it must be visible at
        the default HA log level, not buried at DEBUG."""
        listener = self._make_listener(
            {
                "1111111111111111111111aa": "AAAAAAAA",
                "2222222222222222222222bb": "BBBBBBBB",
            }
        )
        encoded = base64.b64encode(b"unroutable payload").decode()

        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("disarm", {"raw_tag": "space_disarmed"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            patch.object(listener, "_find_space_for_event", return_value=None),
            caplog.at_level(logging.WARNING, logger="custom_components.aegis_ajax.notification"),
        ):
            listener._parse_and_fire_event(encoded)

        assert any(
            "could not be routed" in r.message and r.levelno >= logging.WARNING
            for r in caplog.records
        ), f"expected a routing warning, got {[r.message for r in caplog.records]}"

    def test_push_for_an_unconfigured_space_is_dropped_quietly(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A push naming a space the user chose not to add is not a defect.

        The FCM stream is per *account*, so someone with five Ajax systems
        who added two of them keeps receiving pushes for the other three.
        Those are correctly undeliverable, so they must not raise the
        warning reserved for a push we genuinely could not place.
        """
        listener = self._make_listener(
            {
                "1111111111111111111111aa": "AAAAAAAA",
                "2222222222222222222222bb": "BBBBBBBB",
            }
        )
        raw = base64.b64decode(_REAL_PUSH_ENCODED_DATA)
        encoded = base64.b64encode(raw).decode()

        with (
            patch.object(
                listener,
                "_extract_event_from_proto",
                return_value=("disarm", {"raw_tag": "space_disarmed"}),
            ),
            patch.object(listener, "_extract_source_info", return_value={}),
            caplog.at_level(logging.DEBUG, logger="custom_components.aegis_ajax.notification"),
        ):
            listener._parse_and_fire_event(encoded)

        listener._coordinator.fire_push_event.assert_not_called()
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warnings, (
            f"unexpected warning for a foreign space: {[r.message for r in warnings]}"
        )
        assert any(self._CAPTURED_SPACE_ID in r.message for r in caplog.records), (
            "the skipped space should be named at DEBUG"
        )


class TestFcmStuckPushRepair:
    """Repeated deaths on the same replayed message raise a Repair (#373).

    A frame the library cannot decrypt kills the client before it acks the
    message, so the server replays it and every supervised restart dies on it
    again. Nothing about that loop is visible: alarm state stays correct via
    polling and HTS, so only a log grep reveals it. Hence the Repair.
    """

    def _make_listener(self, entry_id: str = "entry-x") -> AjaxNotificationListener:
        return AjaxNotificationListener(
            hass=MagicMock(), coordinator=MagicMock(), **_FCM_KWARGS, entry_id=entry_id
        )

    @staticmethod
    def _dead_client() -> MagicMock:
        client = MagicMock()
        client.do_listen = False
        client.tasks = []
        client.stop = AsyncMock()
        return client

    async def _die(self, listener: AjaxNotificationListener, now: float) -> None:
        listener._push_client = self._dead_client()
        await listener._async_supervise_push_client(now=now)

    @pytest.mark.asyncio
    async def test_repair_raised_on_third_death_with_same_persistent_id(self) -> None:
        listener = self._make_listener()
        listener._last_persistent_id = "0:1785558606347172%abc"
        with patch(
            "custom_components.aegis_ajax.notification.async_register_fcm_push_stuck"
        ) as reg:
            await self._die(listener, 1000.0)
            await self._die(listener, 2000.0)
            reg.assert_not_called()
            await self._die(listener, 3000.0)
        reg.assert_called_once()
        assert reg.call_args.kwargs["entry_id"] == "entry-x"
        assert reg.call_args.kwargs["terminations"] == 3

    @pytest.mark.asyncio
    async def test_deaths_on_different_messages_do_not_accumulate(self) -> None:
        """Distinct ids mean the client is getting past messages, so whatever
        is killing it is not a single replayed frame."""
        listener = self._make_listener()
        with patch(
            "custom_components.aegis_ajax.notification.async_register_fcm_push_stuck"
        ) as reg:
            for index in range(5):
                listener._last_persistent_id = f"0:msg-{index}"
                await self._die(listener, 1000.0 * (index + 1))
        reg.assert_not_called()

    @pytest.mark.asyncio
    async def test_deaths_with_no_push_received_never_raise(self) -> None:
        """An ordinary network outage kills the client without any push having
        arrived. That must not be reported as a poisoned message."""
        listener = self._make_listener()
        with patch(
            "custom_components.aegis_ajax.notification.async_register_fcm_push_stuck"
        ) as reg:
            for index in range(6):
                await self._die(listener, 1000.0 * (index + 1))
        reg.assert_not_called()
        assert listener._death_repeat_count == 0

    @pytest.mark.asyncio
    async def test_streak_resets_when_a_new_message_arrives_between_deaths(self) -> None:
        listener = self._make_listener()
        listener._last_persistent_id = "0:poison"
        with patch(
            "custom_components.aegis_ajax.notification.async_register_fcm_push_stuck"
        ) as reg:
            await self._die(listener, 1000.0)
            await self._die(listener, 2000.0)
            # A different message got through — the streak is broken.
            listener._last_persistent_id = "0:something-else"
            await self._die(listener, 3000.0)
            await self._die(listener, 4000.0)
        reg.assert_not_called()

    @pytest.mark.asyncio
    async def test_healthy_run_clears_the_repair(self) -> None:
        listener = self._make_listener()
        listener._last_persistent_id = "0:poison"
        with patch("custom_components.aegis_ajax.notification.async_register_fcm_push_stuck"):
            for tick in (1000.0, 2000.0, 3000.0):
                await self._die(listener, tick)
        assert listener._death_repeat_count == 3

        alive = MagicMock()
        alive.do_listen = True
        alive.tasks = []
        listener._push_client = alive
        listener._fcm_client_started_at = 5000.0
        with patch("custom_components.aegis_ajax.notification.async_clear_fcm_push_stuck") as clr:
            # Past FCM_HEALTHY_RUN_RESET_SECONDS of uninterrupted life.
            await listener._async_supervise_push_client(now=5000.0 + 1800.0)
        clr.assert_called_once_with(listener._hass, entry_id="entry-x")
        assert listener._death_repeat_count == 0

    @pytest.mark.asyncio
    async def test_healthy_run_without_a_streak_clears_nothing(self) -> None:
        """No Repair was raised, so there is nothing to delete — don't churn
        the issue registry on every healthy supervision tick."""
        listener = self._make_listener()
        alive = MagicMock()
        alive.do_listen = True
        alive.tasks = []
        listener._push_client = alive
        listener._fcm_client_started_at = 0.0
        with patch("custom_components.aegis_ajax.notification.async_clear_fcm_push_stuck") as clr:
            await listener._async_supervise_push_client(now=1800.0)
        clr.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_entry_id_skips_the_repair_but_still_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        listener = self._make_listener(entry_id="")
        listener._last_persistent_id = "0:poison"
        with (
            patch("custom_components.aegis_ajax.notification.async_register_fcm_push_stuck") as reg,
            caplog.at_level(logging.WARNING),
        ):
            for tick in (1000.0, 2000.0, 3000.0):
                await self._die(listener, tick)
        reg.assert_not_called()

    def test_callback_records_the_persistent_id(self) -> None:
        listener = self._make_listener()
        listener._on_notification({}, "0:1785558606347172%abc")
        assert listener._last_persistent_id == "0:1785558606347172%abc"


class TestFcmStuckPushRecovery:
    """The stuck loop is broken by replacing the FCM registration (#373).

    The replayed frame is queued server-side against a specific registration,
    so a new identity is the only exit. The reporter did it by hand; doing it
    here keeps users out of `.storage`.
    """

    def _make_listener(self) -> AjaxNotificationListener:
        listener = AjaxNotificationListener(
            hass=MagicMock(), coordinator=MagicMock(), **_FCM_KWARGS, entry_id="entry-x"
        )
        listener._store = MagicMock()
        listener._store.async_remove = AsyncMock()
        listener._credentials = {"fcm": {"registration": {"token": "old"}}}
        listener.async_start = AsyncMock()
        return listener

    async def _die(self, listener: AjaxNotificationListener, now: float) -> None:
        client = MagicMock()
        client.do_listen = False
        client.tasks = []
        client.stop = AsyncMock()
        listener._push_client = client
        await listener._async_supervise_push_client(now=now)

    @pytest.mark.asyncio
    async def test_registration_is_discarded_and_renewed_at_the_threshold(self) -> None:
        listener = self._make_listener()
        listener._last_persistent_id = "0:poison"
        with patch("custom_components.aegis_ajax.notification.async_register_fcm_push_stuck"):
            await self._die(listener, 1000.0)
            await self._die(listener, 2000.0)
            listener._store.async_remove.assert_not_called()
            await self._die(listener, 3000.0)
        listener._store.async_remove.assert_awaited_once()
        listener.async_start.assert_awaited_once()
        assert listener._credentials is None

    @pytest.mark.asyncio
    async def test_recovery_runs_only_once_per_streak(self) -> None:
        """Re-registering in a loop against the Firebase project is exactly
        what #227 exists to prevent."""
        listener = self._make_listener()
        listener._last_persistent_id = "0:poison"
        with patch("custom_components.aegis_ajax.notification.async_register_fcm_push_stuck"):
            for tick in range(1, 8):
                await self._die(listener, 1000.0 * tick)
        assert listener._store.async_remove.await_count == 1
        assert listener.async_start.await_count == 1

    @pytest.mark.asyncio
    async def test_replacement_client_is_not_torn_down_by_the_supervisor(self) -> None:
        """Ordering guard: the teardown of the dead client must happen before
        the recovery, or it would null out the client the recovery started."""
        listener = self._make_listener()
        listener._last_persistent_id = "0:poison"
        replacement = MagicMock()
        replacement.do_listen = True
        replacement.tasks = []

        async def _fake_start() -> None:
            listener._push_client = replacement

        listener.async_start = AsyncMock(side_effect=_fake_start)
        with patch("custom_components.aegis_ajax.notification.async_register_fcm_push_stuck"):
            for tick in (1000.0, 2000.0, 3000.0):
                await self._die(listener, tick)
        assert listener._push_client is replacement

    @pytest.mark.asyncio
    async def test_failed_reregistration_leaves_the_backoff_path_intact(self) -> None:
        listener = self._make_listener()
        listener._last_persistent_id = "0:poison"
        listener.async_start = AsyncMock(side_effect=RuntimeError("no network"))
        with patch("custom_components.aegis_ajax.notification.async_register_fcm_push_stuck"):
            for tick in (1000.0, 2000.0, 3000.0):
                await self._die(listener, tick)
        # Swallowed, not raised out of the supervision tick.
        listener.async_start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_removal_failure_skips_reregistration(self) -> None:
        """Re-registering without having dropped the old identity would leave
        the poisoned queue in place and burn a Firebase registration."""
        listener = self._make_listener()
        listener._last_persistent_id = "0:poison"
        listener._store.async_remove = AsyncMock(side_effect=OSError("read-only fs"))
        with patch("custom_components.aegis_ajax.notification.async_register_fcm_push_stuck"):
            for tick in (1000.0, 2000.0, 3000.0):
                await self._die(listener, tick)
        listener.async_start.assert_not_awaited()
        assert listener._credentials is not None

    @pytest.mark.asyncio
    async def test_healthy_run_rearms_the_recovery(self) -> None:
        listener = self._make_listener()
        listener._last_persistent_id = "0:poison"
        with patch("custom_components.aegis_ajax.notification.async_register_fcm_push_stuck"):
            for tick in (1000.0, 2000.0, 3000.0):
                await self._die(listener, tick)
        assert listener._stuck_recovery_done is True

        alive = MagicMock()
        alive.do_listen = True
        alive.tasks = []
        listener._push_client = alive
        listener._fcm_client_started_at = 5000.0
        with patch("custom_components.aegis_ajax.notification.async_clear_fcm_push_stuck"):
            await listener._async_supervise_push_client(now=5000.0 + 1800.0)
        assert listener._stuck_recovery_done is False
