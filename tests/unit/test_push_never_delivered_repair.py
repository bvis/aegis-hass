"""Phase (b) of #437: a Repair for a registration that never delivers.

Phase (a) made "has this credential set ever delivered a push" survive a
restart. On its own that answers the question only for someone who downloads a
diagnostics dump and knows to look. The missing half is the denominator: a
credential set with zero deliveries is unremarkable in a quiet house and
damning on a system that has been armed and disarmed twenty times.

The hub's own status stream carries the same space events push does, within
about a second on a healthy install, so counting those while the push client is
up gives that denominator without a single extra request to Ajax.

The false positive to fear is telling a correctly configured user their push is
broken, so everything here is built to under-report: the counter only moves
while the client is up, it stops the moment a push arrives, and the threshold is
deliberately well past the point of doubt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.aegis_ajax.notification import (
    FCM_NEVER_DELIVERED_EVENT_THRESHOLD,
    AjaxNotificationListener,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_FCM_KWARGS = {
    "fcm_project_id": "mws-mobile-client---2",
    "fcm_app_id": "1:991608156148:android:" + "a" * 40,
    "fcm_api_key": "AIza" + "x" * 35,
    "fcm_sender_id": "991608156148",
}


def _make_listener(*, connected: bool = True) -> AjaxNotificationListener:
    hass = MagicMock()
    hass.loop = None
    listener = AjaxNotificationListener(hass=hass, coordinator=MagicMock(), **_FCM_KWARGS)
    listener._entry_id = "entry-1"
    if connected:
        listener._push_client = MagicMock()
    return listener


def _async_return(value: Any) -> Callable[[], Any]:  # noqa: ANN401
    async def _call() -> Any:  # noqa: ANN401
        return value

    return _call


class TestCountingTheOpportunities:
    def test_a_hub_space_event_counts_while_the_client_is_up(self) -> None:
        listener = _make_listener()

        listener.note_hub_space_event()
        listener.note_hub_space_event()

        assert listener.hub_events_while_connected == 2
        assert listener._delivery_record_unsaved is True

    def test_nothing_is_counted_without_a_push_client(self) -> None:
        """No client means no opportunity, so no evidence either way."""
        listener = _make_listener(connected=False)

        listener.note_hub_space_event()

        assert listener.hub_events_while_connected == 0
        assert listener._delivery_record_unsaved is False

    def test_counting_stops_once_a_push_has_been_delivered(self) -> None:
        """The question is answered; further counting only costs writes."""
        listener = _make_listener()
        listener._note_push_delivered()
        listener._delivery_record_unsaved = False

        listener.note_hub_space_event()

        assert listener.hub_events_while_connected == 0
        assert listener._delivery_record_unsaved is False

    def test_counting_schedules_nothing_on_the_loop(self) -> None:
        """Same contract as the delivery mark: attributes only.

        `_on_hts_space_event` runs on the HTS listen task and the tests around
        it assert exactly what each event schedules, so a hop added here would
        break them for no benefit.
        """
        hass = MagicMock()
        hass.loop = MagicMock()
        listener = AjaxNotificationListener(hass=hass, coordinator=MagicMock(), **_FCM_KWARGS)
        listener._push_client = MagicMock()

        listener.note_hub_space_event()

        hass.loop.call_soon_threadsafe.assert_not_called()


class TestTheRecordSurvivesARestart:
    """The counter is the half that has to persist; that was #437's whole point."""

    @pytest.mark.asyncio
    async def test_the_counter_is_written_and_restored(self) -> None:
        listener = _make_listener()
        saved: dict[str, Any] = {}

        async def _save(data: dict[str, Any]) -> None:
            saved.update(data)

        listener._delivery_store.async_save = _save
        listener.note_hub_space_event()
        await listener._async_persist_delivery_record()

        assert saved["hub_events_while_connected"] == 1

        restored = _make_listener()
        restored._delivery_store.async_load = _async_return(saved)
        await restored._async_load_delivery_record()

        assert restored.hub_events_while_connected == 1
        assert restored.ever_delivered is False

    @pytest.mark.asyncio
    async def test_a_phase_a_record_still_reads_as_delivered(self) -> None:
        """The record shape shipped in 1.19.x carries no explicit flag.

        Inferring "not delivered" from its absence would reset the evidence on
        every install that already has one, and start counting towards a Repair
        on systems where push demonstrably works. Adopt the old shape instead
        of re-deriving it — the same mistake that broke push in 1.19.0.
        """
        listener = _make_listener()
        listener._delivery_store.async_load = _async_return(
            {
                "hash": listener.creds_fingerprint,
                "first_delivery_at": "2026-08-01T10:00:00+00:00",
            }
        )

        await listener._async_load_delivery_record()

        assert listener.ever_delivered is True
        assert listener.hub_events_while_connected == 0

    @pytest.mark.asyncio
    async def test_a_record_for_other_credentials_resets_the_counter_too(self) -> None:
        listener = _make_listener()
        listener._delivery_store.async_load = _async_return(
            {"hash": "other-credentials", "hub_events_while_connected": 99}
        )

        await listener._async_load_delivery_record()

        assert listener.hub_events_while_connected == 0


class TestTheRepair:
    @staticmethod
    def _repairs() -> tuple[Any, Any]:
        return (
            patch("custom_components.aegis_ajax.notification.async_register_fcm_never_delivered"),
            patch("custom_components.aegis_ajax.notification.async_clear_fcm_never_delivered"),
        )

    @pytest.mark.asyncio
    async def test_it_is_raised_once_the_threshold_is_passed(self) -> None:
        listener = _make_listener()
        listener._hub_events_while_connected = FCM_NEVER_DELIVERED_EVENT_THRESHOLD
        register, clear = self._repairs()

        with register as reg, clear:
            await listener._async_review_push_delivery()

        reg.assert_called_once()
        assert reg.call_args.kwargs["entry_id"] == "entry-1"
        assert reg.call_args.kwargs["events"] == FCM_NEVER_DELIVERED_EVENT_THRESHOLD

    @pytest.mark.asyncio
    async def test_it_is_not_raised_one_event_short(self) -> None:
        listener = _make_listener()
        listener._hub_events_while_connected = FCM_NEVER_DELIVERED_EVENT_THRESHOLD - 1
        register, clear = self._repairs()

        with register as reg, clear:
            await listener._async_review_push_delivery()

        reg.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_delivered_push_clears_it_however_many_events_were_counted(self) -> None:
        """The Repair must not outlive the thing it claims."""
        listener = _make_listener()
        listener._hub_events_while_connected = FCM_NEVER_DELIVERED_EVENT_THRESHOLD * 3
        listener._note_push_delivered()
        register, clear = self._repairs()

        with register as reg, clear as clr:
            await listener._async_review_push_delivery()

        reg.assert_not_called()
        clr.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_quiet_house_never_trips_it(self) -> None:
        """Zero deliveries with no opportunities is not evidence of anything."""
        listener = _make_listener()
        register, clear = self._repairs()

        with register as reg, clear:
            await listener._async_review_push_delivery()

        reg.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_entry_id_means_no_issue_is_written(self) -> None:
        """An issue id without an entry id would not identify anything."""
        listener = _make_listener()
        listener._entry_id = ""
        listener._hub_events_while_connected = FCM_NEVER_DELIVERED_EVENT_THRESHOLD * 2
        register, clear = self._repairs()

        with register as reg, clear as clr:
            await listener._async_review_push_delivery()

        reg.assert_not_called()
        clr.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_supervisor_reviews_delivery_and_flushes_the_counter(self) -> None:
        """Both live on the supervisor tick, ahead of its early returns."""
        listener = _make_listener()
        listener._hub_events_while_connected = FCM_NEVER_DELIVERED_EVENT_THRESHOLD
        listener._delivery_record_unsaved = True
        persisted: list[bool] = []

        async def _persist() -> None:
            persisted.append(True)

        listener._async_persist_delivery_record = _persist
        register, clear = self._repairs()

        with register as reg, clear:
            await listener._async_supervise_push_client()

        assert persisted == [True]
        reg.assert_called_once()


class TestTheDenominatorIsInTheDump:
    @pytest.mark.asyncio
    async def test_the_push_block_reports_the_counted_events(self) -> None:
        """A zero delivery count is ambiguous without its denominator."""
        from custom_components.aegis_ajax.diagnostics import _push_diagnostics

        listener = _make_listener()
        listener._hub_events_while_connected = 7

        block = _push_diagnostics(listener)

        assert block["hub_events_while_connected"] == 7
