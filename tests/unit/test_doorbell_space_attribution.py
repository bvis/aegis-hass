"""A doorbell push must never be attributed across a space boundary (#494).

Two independent defects, both invisible on a single-space install and both
reported together by a PRO account managing several client spaces:

1. The single-doorbell fallback in `_dispatch_event_to_device` searched every
   device the config entry knows about. With one doorbell in the account and
   several spaces, every unattributed ring resolved to that one doorbell — so a
   press in one client's space fired the ring on another client's entity.
2. `extract_notification_id` scanned the payload for the first 64-character hex
   run instead of reading `Notification.id`. When the real id is not 64 hex
   characters the scan returns some other, often constant, blob, which makes the
   5-second duplicate window swallow genuinely different presses.

This is the same family as #358: per-space behaviour that cannot be tested with
one space. Every test here therefore uses at least two.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

from custom_components.aegis_ajax.api.models import Device, Space
from custom_components.aegis_ajax.const import ConnectionStatus, DeviceState, SecurityState
from custom_components.aegis_ajax.notification import AjaxNotificationListener
from tests.unit.test_notification import _FCM_KWARGS


def _doorbell(device_id: str, hub_id: str) -> Device:
    return Device(
        id=device_id,
        hub_id=hub_id,
        name="Doorbell",
        device_type="video_edge_doorbell",
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses={},
        battery=None,
    )


def _space(space_id: str, hub_id: str) -> Space:
    return Space(
        id=space_id,
        hub_id=hub_id,
        name="Client space",
        security_state=SecurityState.DISARMED,
        connection_status=ConnectionStatus.ONLINE,
        malfunctions_count=0,
    )


class _Harness:
    """A listener wired to a coordinator with real Device/Space objects.

    The existing routing tests use `MagicMock` device stand-ins, which cannot
    express "this doorbell belongs to that hub" — the very fact this fix turns
    on. Real dataclasses are used here for that reason.
    """

    def __init__(self, devices: dict[str, Device], spaces: dict[str, Space]) -> None:
        self.hass = MagicMock()
        self.hass.loop = MagicMock()
        self.hass.loop.is_running.return_value = True
        self.hass.loop.call_soon_threadsafe.side_effect = lambda cb, *a: cb(*a)
        self.coordinator = MagicMock()
        self.coordinator.devices = devices
        self.coordinator.spaces = spaces
        self.coordinator._space_ids = list(spaces)
        self.coordinator.doorbell_twin_aliases = {}
        self.coordinator.fire_push_device_event = MagicMock(return_value=True)
        self.listener = AjaxNotificationListener(
            hass=self.hass, coordinator=self.coordinator, **_FCM_KWARGS
        )

    def ring(self, routed_space: str | None, source: dict | None = None) -> None:
        encoded = base64.b64encode(b"any-payload").decode()
        with (
            patch.object(
                self.listener,
                "_extract_event_from_proto",
                return_value=("doorbell_pressed", {"raw_tag": "ring_button_pressed"}),
            ),
            patch.object(self.listener, "_extract_source_info", return_value=source or {}),
            patch.object(self.listener, "_find_space_for_event", return_value=routed_space),
        ):
            self.listener._parse_and_fire_event(encoded)

    @property
    def attributed_to(self) -> str | None:
        if not self.coordinator.fire_push_device_event.called:
            return None
        target: str = self.coordinator.fire_push_device_event.call_args[0][0]
        return target


class TestDoorbellFallbackIsScopedToItsSpace:
    def test_ring_in_one_space_is_not_attributed_to_another_spaces_doorbell(self) -> None:
        """The #494 report: press in space A, ring fires on space B's entity."""
        harness = _Harness(
            devices={"bell-b": _doorbell("bell-b", "HUB-B")},
            spaces={"space-a": _space("space-a", "HUB-A"), "space-b": _space("space-b", "HUB-B")},
        )

        harness.ring(routed_space="space-a")

        assert harness.attributed_to is None

    def test_ring_still_resolves_to_the_single_doorbell_of_its_own_space(self) -> None:
        harness = _Harness(
            devices={"bell-b": _doorbell("bell-b", "HUB-B")},
            spaces={"space-a": _space("space-a", "HUB-A"), "space-b": _space("space-b", "HUB-B")},
        )

        harness.ring(routed_space="space-b")

        assert harness.attributed_to == "bell-b"

    def test_two_doorbells_in_one_space_are_still_not_guessed_between(self) -> None:
        harness = _Harness(
            devices={
                "bell-1": _doorbell("bell-1", "HUB-A"),
                "bell-2": _doorbell("bell-2", "HUB-A"),
            },
            spaces={"space-a": _space("space-a", "HUB-A")},
        )

        harness.ring(routed_space="space-a")

        assert harness.attributed_to is None

    def test_an_unroutable_ring_is_not_guessed_on_a_multi_space_account(self) -> None:
        """No space resolved and several spaces configured: refuse to guess.

        Guessing here is what produced the cross-client ring in #494, and an
        unattributed push still fires the hub-level event entity.
        """
        harness = _Harness(
            devices={"bell-b": _doorbell("bell-b", "HUB-B")},
            spaces={"space-a": _space("space-a", "HUB-A"), "space-b": _space("space-b", "HUB-B")},
        )

        harness.ring(routed_space=None)

        assert harness.attributed_to is None

    def test_an_unroutable_ring_still_resolves_on_a_single_space_account(self) -> None:
        """The #173 case must keep working: one space, one doorbell, no source id."""
        harness = _Harness(
            devices={"bell-a": _doorbell("bell-a", "HUB-A")},
            spaces={"space-a": _space("space-a", "HUB-A")},
        )

        harness.ring(routed_space=None)

        assert harness.attributed_to == "bell-a"

    def test_an_explicit_source_id_is_honoured_regardless_of_space(self) -> None:
        """Only the *fallback* is scoped. An id the push actually carries wins.

        Narrowing this too would break installs whose push names a device whose
        hub attribution we read differently, which is a regression, not a fix.
        """
        harness = _Harness(
            devices={"bell-b": _doorbell("bell-b", "HUB-B")},
            spaces={"space-a": _space("space-a", "HUB-A"), "space-b": _space("space-b", "HUB-B")},
        )

        harness.ring(routed_space="space-a", source={"device_id": "bell-b"})

        assert harness.attributed_to == "bell-b"


class TestNotificationIdIsReadStructurally:
    """`Notification.id` is the id; the first 64-hex run in the payload is not."""

    @staticmethod
    def _push(notification_id: str, decoy: str) -> str:
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.service.push_notification_dispatch import (  # noqa: E501
            event_pb2,
        )

        dispatch = event_pb2.PushNotificationDispatchEvent()
        notification = dispatch.notification
        notification.id = notification_id
        # A 64-hex blob that is not the id, positioned after it in the payload.
        notification.content.hub_notification_content.source.name = decoy
        return base64.b64encode(dispatch.SerializeToString()).decode()

    def test_an_id_that_is_not_64_hex_characters_is_still_returned(self) -> None:
        decoy = "a" * 64
        encoded = self._push("ring-000123", decoy)

        result = AjaxNotificationListener.extract_notification_id(encoded)

        assert result == "ring-000123"
        assert result != decoy

    def test_a_64_hex_id_is_unchanged_by_the_structural_read(self) -> None:
        real_id = "4842536662915600AABB112233445566778899" + "00A1B2C3D4000001"[:10] + "0" * 16
        assert len(real_id) == 64
        encoded = self._push(real_id, "b" * 64)

        assert AjaxNotificationListener.extract_notification_id(encoded) == real_id

    def test_two_different_ids_do_not_collapse_onto_one_value(self) -> None:
        """The duplicate window keys on this value, so a constant is a lost press."""
        decoy = "c" * 64
        first = AjaxNotificationListener.extract_notification_id(self._push("ring-1", decoy))
        second = AjaxNotificationListener.extract_notification_id(self._push("ring-2", decoy))

        assert first != second

    def test_a_payload_that_is_not_a_dispatch_event_still_uses_the_hex_scan(self) -> None:
        """The scan stays as a fallback for shapes the structural read can't decode."""
        scanned = "d" * 64
        encoded = base64.b64encode(b"\xff\xfe not a dispatch event " + scanned.encode()).decode()

        assert AjaxNotificationListener.extract_notification_id(encoded) == scanned
