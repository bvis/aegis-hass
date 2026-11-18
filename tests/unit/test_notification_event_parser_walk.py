"""Wire-format walk tests for the FCM push candidate scanner (#339 / #320).

`_find_embedded_messages` used to scan for wire-type-2 field headers byte by
byte instead of walking the protobuf wire format. Three consequences, all
observed on real hardware pushes:

* varint / 64-bit / 32-bit payloads were not consumed, so a value byte got
  read as the next field tag and the walk lost alignment — stepping straight
  over the event qualifier (the tamper push of #339);
* candidates shorter than 5 bytes were discarded, which drops a legitimate
  transition-less qualifier (4 bytes on the wire);
* the recursive descent sat *inside* the `4 < length < 500` filter, so a push
  whose single top-level field exceeded 500 bytes yielded zero candidates and
  the event was lost outright (the scenario-arm push of #339).

These tests build genuine `PushNotificationDispatchEvent` payloads with the
vendored protos — the same wire bytes the FCM listener receives — per the
real-proto testing rule: a MagicMock qualifier cannot exercise a wire-format
walk at all.
"""

from __future__ import annotations

import base64

import pytest
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event import (
    transition_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.company import (
    qualifier_pb2 as company_qualifier_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.company import (
    tag_pb2 as company_tag_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.hub import (
    qualifier_pb2 as hub_qualifier_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.hub import (
    tag_pb2 as hub_tag_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.space import (
    qualifier_pb2 as space_qualifier_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.space import (
    tag_pb2 as space_tag_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification import (
    content_pb2,
    notification_pb2,
    space_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.company import (  # noqa: E501
    content_pb2 as company_content_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.hub import (
    content_pb2 as hub_content_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.hub import (
    origin_pb2,
    source_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.space import (
    content_pb2 as space_content_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.push.custom import (
    media_enriched_notification_pb2,
)
from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.service.push_notification_dispatch import (  # noqa: E501
    event_pb2,
)

from custom_components.aegis_ajax.notification_event_parser import (
    _extract_event_with_compiled_protos,
    _find_embedded_messages,
    _resolve_event_by_scanning,
    _resolve_event_from_dispatch,
    extract_category,
)

# A 64-char hex notification id, the shape real pushes carry.
_NOTIFICATION_ID = "4f" * 32
_SPACE_ID = "a1b2c3d4e5f60718293a4b5c"


def _tamper_qualifier() -> hub_qualifier_pb2.HubEventQualifier:
    """The qualifier a SmartBracket detach produces (field 17, triggered)."""
    return hub_qualifier_pb2.HubEventQualifier(
        tag=hub_tag_pb2.HubEventTag(tamper_opened=hub_tag_pb2.TamperOpened()),
        transition=transition_pb2.EventTransition(
            triggered=transition_pb2.EventTransition.Triggered()
        ),
    )


def _hub_push(
    qualifier: hub_qualifier_pb2.HubEventQualifier,
    *,
    device_name: str = "FIN VESTIDOR",
    room_name: str = "Vestidor",
) -> bytes:
    """Serialize a realistic hub-content push around `qualifier`.

    Mirrors the captured wire shape: string ids, a `server_timestamp`
    (multi-byte varints), scalar enum fields for folder / importance /
    source type, then the nested content carrying the qualifier. The
    varints before the content are what used to misalign the scan.
    """
    notification = notification_pb2.Notification(
        id=_NOTIFICATION_ID,
        universal_token="01HQZX9K2M4N6P8R0S2T4V6W",
        space=space_pb2.NotificationSpace(id=_SPACE_ID, name="Casa"),
        folder=2,
        importance=2,
        content=content_pb2.NotificationContent(
            hub_notification_content=hub_content_pb2.HubNotificationContent(
                origin=origin_pb2.HubOrigin(hex_id="00A1B2C3", name="Hub 2 Plus"),
                qualifier=qualifier,
                source=source_pb2.HubNotificationSource(
                    type=1,
                    id="003AE89B",
                    name=device_name,
                    room_name=room_name,
                ),
            )
        ),
    )
    notification.server_timestamp.GetCurrentTime()
    return event_pb2.PushNotificationDispatchEvent(notification=notification).SerializeToString()


class TestRealPushPayloadsResolve:
    """End-to-end: real dispatch-event bytes must resolve to their event."""

    def test_tamper_push_resolves_to_tamper_event(self) -> None:
        # #339 second leg: the tamper push arrived but the scan lost
        # alignment on the varints preceding the content and never
        # surfaced the qualifier, so no `tamper` event was ever fired.
        raw = _hub_push(_tamper_qualifier())

        result = _extract_event_with_compiled_protos(raw)

        assert result is not None
        event_type, data = result
        assert event_type == "tamper"
        assert data["raw_tag"] == "tamper_opened"
        assert data["transition"] == "triggered"

    def test_push_larger_than_500_bytes_resolves_to_its_event(self) -> None:
        # #339 scenario-arm leg: a 541-byte push whose single top-level
        # field was 538 bytes produced ZERO candidates, because the
        # recursive descent lived inside the size filter.
        raw = _hub_push(
            _tamper_qualifier(),
            device_name="SENSOR " + "N" * 240,
            room_name="HABITACION " + "H" * 240,
        )
        assert len(raw) > 500

        result = _extract_event_with_compiled_protos(raw)

        assert result is not None
        event_type, _ = result
        assert event_type == "tamper"

    def test_transition_less_qualifier_is_not_discarded(self) -> None:
        # A qualifier carrying only its tag serializes to exactly 4 bytes
        # (`0a 02 0a 00` for `door_opened`), which the old `4 < length`
        # floor rejected.
        qualifier = hub_qualifier_pb2.HubEventQualifier(
            tag=hub_tag_pb2.HubEventTag(door_opened=hub_tag_pb2.DoorOpened())
        )
        assert len(qualifier.SerializeToString()) == 4

        result = _extract_event_with_compiled_protos(_hub_push(qualifier))

        assert result is not None
        event_type, data = result
        assert event_type == "door_open"
        assert data["raw_tag"] == "door_opened"

    def test_door_open_while_armed_night_is_not_reported_as_arm_night(self) -> None:
        # #320: an armed-away door-open surfaced as `arm_night`. A
        # misaligned walk can manufacture a candidate that decodes as a
        # different, lower-priority qualifier; with an aligned walk the
        # door-open (priority 80) must beat the night-mode state context
        # (priority 50) that rides along in the same push.
        space_content = space_content_pb2.SpaceNotificationContent(
            qualifier=space_qualifier_pb2.SpaceEventQualifier(
                tag=space_tag_pb2.SpaceEventTag(
                    space_night_mode_on=space_tag_pb2.SpaceNightModeOn()
                ),
                transition=transition_pb2.EventTransition(
                    impulse=transition_pb2.EventTransition.Impulse()
                ),
            )
        )
        door_qualifier = hub_qualifier_pb2.HubEventQualifier(
            tag=hub_tag_pb2.HubEventTag(door_opened=hub_tag_pb2.DoorOpened()),
            transition=transition_pb2.EventTransition(
                triggered=transition_pb2.EventTransition.Triggered()
            ),
        )
        raw = _hub_push(door_qualifier) + space_content.SerializeToString()

        result = _extract_event_with_compiled_protos(raw)

        assert result is not None
        event_type, data = result
        assert event_type == "door_open"
        assert data["raw_tag"] == "door_opened"


class TestPushCategory:
    """#676: the category is what tells an alarm from a plain notice."""

    @staticmethod
    def _push(folder: int) -> bytes:
        notification = notification_pb2.Notification(id=_NOTIFICATION_ID, folder=folder)
        return event_pb2.PushNotificationDispatchEvent(
            notification=notification
        ).SerializeToString()

    @pytest.mark.parametrize(
        ("folder", "expected"),
        [(1, "alarm"), (2, "malfunction"), (3, "user_automation"), (5, "system"), (0, None)],
    )
    def test_category_is_read_from_the_notification(self, folder: int, expected: str) -> None:
        assert extract_category(self._push(folder)) == expected

    def test_category_unknown_to_these_protos_is_none(self) -> None:
        assert extract_category(self._push(99)) is None

    def test_bytes_that_are_not_a_push_have_no_category(self) -> None:
        assert extract_category(b"\xff\xff") is None


class TestQualifierTypeIsTakenFromTheContent:
    """The content oneof — not decode order — decides the qualifier type.

    `HubEventTag` and `SpaceEventTag` share field numbers, so hub qualifier
    bytes parse cleanly as a *different* space event and vice versa. Every
    mapped hub tag up to field 18 collides with a mapped space tag:
    `door_opened`(1) reads as `space_armed`, `malfunction`(14) as
    `space_night_mode_on_with_malfunctions`, `tamper_opened`(17) as
    `space_group_duress_disarmed`. Trying Space first and stopping at the
    first successful decode therefore relabels hub events as arm/disarm
    state changes — which also drives the alarm panel's security state.
    """

    def test_hub_tamper_is_not_relabelled_as_a_duress_disarm(self) -> None:
        # #339 / #287: `tamper_opened` (hub field 17) decodes as
        # `space_group_duress_disarmed` (space field 17) → `disarm`.
        raw = _hub_push(_tamper_qualifier())

        event_type, data = _extract_event_with_compiled_protos(raw)  # type: ignore[misc]

        assert (event_type, data["raw_tag"]) == ("tamper", "tamper_opened")

    def test_hub_malfunction_is_not_relabelled_as_arm_night(self) -> None:
        # #320: the reporter saw `arm_night` for a sensor event while armed
        # away. `malfunction` (hub field 14) decodes as
        # `space_night_mode_on_with_malfunctions` (space field 14).
        qualifier = hub_qualifier_pb2.HubEventQualifier(
            tag=hub_tag_pb2.HubEventTag(malfunction=hub_tag_pb2.Malfunction()),
            transition=transition_pb2.EventTransition(
                triggered=transition_pb2.EventTransition.Triggered()
            ),
        )

        result = _extract_event_with_compiled_protos(_hub_push(qualifier))

        assert result is not None
        event_type, data = result
        assert data["raw_tag"] == "malfunction"
        assert event_type != "arm_night"

    def test_unmapped_hub_tag_does_not_produce_a_space_event(self) -> None:
        # `ext_contact_opened` (hub field 2) is not in the hub tag map, but
        # it decodes as `space_disarmed` (space field 2). Reporting `disarm`
        # for it would apply a DISARMED security state off an unmapped
        # sensor event — worse than reporting nothing.
        qualifier = hub_qualifier_pb2.HubEventQualifier(
            tag=hub_tag_pb2.HubEventTag(ext_contact_opened=hub_tag_pb2.ExtContactOpened()),
            transition=transition_pb2.EventTransition(
                triggered=transition_pb2.EventTransition.Triggered()
            ),
        )

        assert _extract_event_with_compiled_protos(_hub_push(qualifier)) is None

    def test_space_arm_push_still_resolves_to_arm(self) -> None:
        # The regression guard for the other direction: a genuine space
        # arm push must keep resolving through the space tag map.
        notification = notification_pb2.Notification(
            id=_NOTIFICATION_ID,
            space=space_pb2.NotificationSpace(id=_SPACE_ID, name="Casa"),
            folder=2,
            importance=2,
            content=content_pb2.NotificationContent(
                space_notification_content=space_content_pb2.SpaceNotificationContent(
                    qualifier=space_qualifier_pb2.SpaceEventQualifier(
                        tag=space_tag_pb2.SpaceEventTag(space_armed=space_tag_pb2.SpaceArmed()),
                        transition=transition_pb2.EventTransition(
                            impulse=transition_pb2.EventTransition.Impulse()
                        ),
                    )
                )
            ),
        )
        raw = event_pb2.PushNotificationDispatchEvent(notification=notification).SerializeToString()

        result = _extract_event_with_compiled_protos(raw)

        assert result == ("arm", {"raw_tag": "space_armed", "transition": "impulse"})

    def test_media_enriched_push_resolves_its_event(self) -> None:
        # Photo-on-demand pushes arrive as `media_enriched_notification`,
        # which wraps the same `Notification` message.
        notification = notification_pb2.Notification(
            id=_NOTIFICATION_ID,
            space=space_pb2.NotificationSpace(id=_SPACE_ID, name="Casa"),
            content=content_pb2.NotificationContent(
                hub_notification_content=hub_content_pb2.HubNotificationContent(
                    qualifier=_tamper_qualifier(),
                    source=source_pb2.HubNotificationSource(type=1, id="003AE89B", name="MOTION"),
                )
            ),
        )
        raw = event_pb2.PushNotificationDispatchEvent(
            media_enriched_notification=media_enriched_notification_pb2.MediaEnrichedNotification(
                notification=notification
            )
        ).SerializeToString()

        result = _extract_event_with_compiled_protos(raw)

        assert result is not None
        assert result[0] == "tamper"


class TestRealCapturedPush:
    """The captured payload the rest of the suite replays (#80 dedupe tests).

    Imported rather than copied so both suites always exercise the same
    bytes. It is a photo-on-demand notification from a MotionCam, whose
    hub tag (`photo_on_demand_with_name`) this integration maps to no HA
    event.
    """

    @staticmethod
    def _raw() -> bytes:
        from tests.unit.test_notification import _REAL_PUSH_ENCODED_DATA  # noqa: PLC0415

        return base64.b64decode(_REAL_PUSH_ENCODED_DATA)

    def test_real_push_decodes_as_a_dispatch_notification(self) -> None:
        # Guards the assumption the whole structured path rests on: real
        # FCM payloads are `PushNotificationDispatchEvent` messages with a
        # typed content, so the fallback scan never runs for them.
        recognised, _ = _resolve_event_from_dispatch(self._raw())

        assert recognised is True

    def test_photo_notification_does_not_fire_a_phantom_night_arm(self) -> None:
        # The candidate scan reported this photo notification as
        # `arm_night` / `space_night_mode_on`, which also applied a
        # NIGHT_MODE security state to the panel.
        assert _resolve_event_by_scanning(self._raw()) == (
            "arm_night",
            {"raw_tag": "space_night_mode_on"},
        ), "fixture no longer reproduces the phantom event this test guards against"

        assert _extract_event_with_compiled_protos(self._raw()) is None


class TestUnmappedContentIsNotScanned:
    """A recognised content type is authoritative even when unmapped.

    `company` / `accounting` / `blank` contents do carry a qualifier — of
    a vocabulary this integration maps nothing from. Falling back to the
    candidate scan for them would resolve those bytes against the *space*
    vocabulary and invent an arm/disarm, which is the whole failure this
    change exists to remove.
    """

    def test_company_content_yields_no_event_instead_of_a_scanned_one(self) -> None:
        # Field 2 of `CompanyEventTag` (`installation_company_removed`)
        # occupies the same slot as `space_disarmed`, so the scan would
        # report a `disarm` for a company message.
        qualifier = company_qualifier_pb2.CompanyEventQualifier(
            tag=company_tag_pb2.CompanyEventTag(
                installation_company_removed=company_tag_pb2.InstallationCompanyRemoved()
            ),
            transition=transition_pb2.EventTransition(
                impulse=transition_pb2.EventTransition.Impulse()
            ),
        )
        notification = notification_pb2.Notification(
            id=_NOTIFICATION_ID,
            space=space_pb2.NotificationSpace(id=_SPACE_ID, name="Casa"),
            content=content_pb2.NotificationContent(
                company_notification_content=company_content_pb2.CompanyNotificationContent(
                    qualifier=qualifier
                )
            ),
        )
        raw = event_pb2.PushNotificationDispatchEvent(notification=notification).SerializeToString()

        assert _extract_event_with_compiled_protos(raw) is None


class TestWireFormatWalk:
    """Unit-level: the walk must consume every wire type's payload."""

    @staticmethod
    def _len_delimited(field_number: int, payload: bytes) -> bytes:
        assert len(payload) < 128, "test helper only emits single-byte lengths"
        return bytes([(field_number << 3) | 2, len(payload)]) + payload

    @pytest.mark.parametrize(
        ("label", "prefix"),
        [
            # field 5 varint = 2, the byte pair that misaligned the real
            # tamper push (`28 02` read as tag `02` + length `30`).
            ("single-byte varint", b"\x28\x02\x30\x02"),
            # a timestamp-sized varint: several continuation bytes, any of
            # which could be mistaken for a length-delimited field tag.
            ("multi-byte varint", b"\x08\xf5\x90\x8f\xd3\x06"),
            ("64-bit fixed", b"\x09" + b"\x12" * 8),
            ("32-bit fixed", b"\x0d" + b"\x12" * 4),
        ],
    )
    def test_qualifier_after_scalar_field_is_found(self, label: str, prefix: bytes) -> None:
        qualifier = _tamper_qualifier().SerializeToString()
        raw = prefix + self._len_delimited(7, qualifier)

        candidates = _find_embedded_messages(raw)

        assert qualifier in candidates, f"qualifier lost after {label}"

    def test_real_push_walk_surfaces_the_qualifier(self) -> None:
        # The discriminating case for the misalignment: only a full push,
        # with its real field ordering and multi-byte varints, arranges the
        # bytes so the bogus jump lands *past* the qualifier header. Short
        # hand-built payloads recover by accident — the byte-wise scan's
        # `i += 1` fallback happens to re-sync, or the bogus candidate
        # happens to still contain the qualifier — so they cannot stand in
        # for this assertion.
        qualifier = _tamper_qualifier().SerializeToString()
        raw = _hub_push(_tamper_qualifier())
        assert qualifier in raw, "fixture must embed the qualifier verbatim"

        candidates = _find_embedded_messages(raw)

        assert qualifier in candidates

    def test_qualifier_nested_inside_an_oversized_wrapper_is_found(self) -> None:
        # The wrapper is deliberately larger than the old 500-byte upper
        # bound; descending into it is the only way to reach the qualifier.
        qualifier = _tamper_qualifier().SerializeToString()
        padding = self._len_delimited(3, b"P" * 120)
        inner = padding * 5 + self._len_delimited(7, qualifier)
        assert len(inner) > 500
        raw = bytes([0x0A, 0x80 | (len(inner) & 0x7F), len(inner) >> 7]) + inner

        candidates = _find_embedded_messages(raw)

        assert qualifier in candidates

    def test_scalar_only_payload_yields_no_candidates(self) -> None:
        # Nothing length-delimited in here: a walk that stays aligned must
        # not invent candidates out of varint value bytes.
        raw = b"\x08\xf5\x90\x8f\xd3\x06\x10\x02\x18\x01\x25\x01\x02\x03\x04"

        assert _find_embedded_messages(raw) == []
