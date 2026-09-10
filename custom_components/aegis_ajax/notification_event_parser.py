"""Pure parsers for Ajax FCM push payloads.

Extracted from `notification.py` so the base64/protobuf event-decoding logic
lives in one focused, side-effect-free module that can be tested without a
listener, coordinator, or HA instance. `AjaxNotificationListener` keeps thin
delegators (`extract_notification_id`, `_extract_event_with_compiled_protos`,
`_extract_source_info`, …) that forward here, so every existing caller and test
keeps working unchanged.

Everything here is a pure function of its `bytes`/`str` argument — no listener
state, no network. The listener's `_parse_and_fire_event` orchestrates these
and owns the side effects (firing HA events, applying security state).
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

from custom_components.aegis_ajax.const import (
    HUB_EVENT_TAG_MAP,
    SMARTLOCK_EVENT_TAG_MAP,
    SPACE_EVENT_TAG_MAP,
    TAG_PRIORITY,
    VIDEO_EVENT_TAG_MAP,
)

# Logger name is pinned to the original `notification` module (not __name__) so
# DEBUG output keeps its existing namespace — the listener and caplog-based
# tests filter on `custom_components.aegis_ajax.notification`. Same name == same
# logger object, so log lines are byte-for-byte unchanged after the move.
_LOGGER = logging.getLogger("custom_components.aegis_ajax.notification")

# Ajax `group_hex_id` values observed in real installs are short hex
# strings (typically 8 chars like "00000001"). The cap and hex-only
# check filter out false matches where the scan happens to land on
# an unrelated (string, string) pair — most notably the 24-char
# `space_id` followed by some printable field, which is what beta.8's
# too-permissive scan picked up in #148.
_MAX_GROUP_HEX_ID_LEN = 16
_HEX_CHARS = frozenset("0123456789abcdefABCDEF")

# `NotificationContent` oneof case → tag map for the qualifier it carries.
# The content case is the ONLY reliable discriminator between the four
# qualifier types: `HubEventTag` and `SpaceEventTag` share field numbers,
# so hub qualifier bytes decode cleanly as an unrelated space event and
# vice versa (`door_opened` reads as `space_armed`, `tamper_opened` as
# `space_group_duress_disarmed`, `malfunction` as
# `space_night_mode_on_with_malfunctions`). Decode order cannot resolve
# that ambiguity — the wrapper has to.
_CONTENT_TAG_MAPS: dict[str, dict[str, str]] = {
    "hub_notification_content": HUB_EVENT_TAG_MAP,
    "space_notification_content": SPACE_EVENT_TAG_MAP,
    "video_notification_content": VIDEO_EVENT_TAG_MAP,
    "smart_lock_notification_content": SMARTLOCK_EVENT_TAG_MAP,
}

# Content cases that carry a qualifier of a vocabulary this integration
# maps nothing from (`CompanyEventQualifier`, `AccountingEventQualifier`,
# `BlankEventQualifier`). They are still authoritative: a recognised
# content type means no scan, because scanning a company or accounting
# message resolves its bytes against the space vocabulary and invents an
# arm/disarm. `tests/unit/test_event_tag_maps.py` asserts that every case
# the proto defines appears here or in `_CONTENT_TAG_MAPS`.
_UNMAPPED_CONTENT_CASES = frozenset(
    {
        "company_notification_content",
        "accounting_notification_content",
        "blank_notification_content",
    }
)

# Bounds for the best-effort wire walk (fallback path only). Depth covers
# the deepest real nesting (dispatch → notification → content → typed
# content → qualifier → tag) with room to spare; the candidate cap keeps a
# malformed or adversarial payload from expanding without limit, since
# string and bytes fields get descended into as well.
_MAX_WALK_DEPTH = 8
_MAX_WALK_CANDIDATES = 1024
# The shortest qualifier that can carry a mapped tag is 4 bytes on the
# wire (`0a 02 <tag> 00` — a tag-only qualifier with no transition), so
# anything shorter can neither be a candidate nor contain one.
_MIN_QUALIFIER_LEN = 4


def extract_alarm_photo_context(raw: bytes, notification_id: str) -> tuple[str, str, float] | None:
    """Recover the camera, hub and event time from the push without an API lookup."""
    try:
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.service.push_notification_dispatch import (  # noqa: PLC0415, E501
            event_pb2,
        )

        dispatch = event_pb2.PushNotificationDispatchEvent()
        dispatch.ParseFromString(raw)
        if dispatch.WhichOneof("push") != "notification":
            return None
        notification = dispatch.notification
        if notification.id != notification_id:
            return None
        if notification.content.WhichOneof("content") != "hub_notification_content":
            return None
        content = notification.content.hub_notification_content
        ts = notification.server_timestamp
        timestamp = ts.seconds + ts.nanos / 1_000_000_000
        if not content.source.id or not content.origin.hex_id or timestamp <= 0:
            return None
        return content.source.id, content.origin.hex_id, timestamp
    except Exception:
        _LOGGER.debug("Alarm push lacks usable photo context; manual backfill is available")
        return None


def extract_notification_id(encoded_data: str) -> str | None:
    """Return `Notification.id` from a base64-encoded push payload, or None.

    Read structurally, for the reason spelled out in `extract_space_id`: an id
    is just text, and a substring search cannot tell one id from another that
    happens to sit earlier in the payload. This function used to return the
    first 64-character hex run it could find, which is the real id only while
    the real id happens to be 64 hex characters and first. When it is not — a
    video/NVR-sourced push is one shape where it is not — the scan returns some
    other, often per-device constant, blob. That is worse than nothing here: the
    value keys the 5 s duplicate window, so a constant makes two genuinely
    different presses look like one delivery and silently drops the second
    (#494).

    The hex scan is kept as a fallback for payloads the structural read cannot
    decode at all, which is what it was really covering.
    """
    try:
        raw = base64.b64decode(encoded_data)
    except Exception:
        _LOGGER.debug("Failed to extract notification_id from push")
        return None

    notification = _dispatch_notification(raw)
    if notification is not None and notification.id:
        structural: str = notification.id
        return structural

    try:
        matches = re.findall(rb"[0-9A-Fa-f]{64}", raw)
        if matches:
            result: str = matches[0].decode("ascii")
            _LOGGER.debug("Push carried no readable Notification.id; using the hex scan instead")
            return result
    except Exception:
        _LOGGER.debug("Failed to extract notification_id from push")
    return None


def _dispatch_notification(raw: bytes) -> Any | None:  # noqa: ANN401
    """Unwrap a push payload to its `Notification`, or None.

    Both real push shapes carry the same `Notification`: plain
    `notification` pushes and the `media_enriched_notification` wrapper
    used by photo-on-demand. Returns None when the bytes aren't a
    dispatch event at all, which is the signal to fall back to the
    best-effort scan.
    """
    try:
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.service.push_notification_dispatch import (  # noqa: PLC0415, E501
            event_pb2,
        )
    except ImportError:  # pragma: no cover - vendored protos always ship
        _LOGGER.debug("Push dispatch proto not available")
        return None

    try:
        dispatch = event_pb2.PushNotificationDispatchEvent()
        dispatch.ParseFromString(raw)
    except Exception:
        return None

    push_case = dispatch.WhichOneof("push")
    if push_case == "notification":
        return dispatch.notification
    if push_case == "media_enriched_notification":
        return dispatch.media_enriched_notification.notification
    return None


def extract_space_id(raw: bytes) -> str | None:
    """Return the id of the space a push belongs to, or None (#358).

    `Notification.space.id` is the only field that names the space, and
    it matches `Space.id` from the spaces API byte for byte. The hub's
    own id is *not* usable for this: it rides `HubOrigin.hex_id` as an
    8-char ASCII string, so the pre-#358 router — which scanned the
    payload for `bytes.fromhex(hub_id)` — could never match a genuine
    capture and fell through to fanning every event out to every space.

    Read structurally rather than by scanning: an id is just hex text,
    and a substring search cannot tell one space's id from a device id
    or a notification id that happens to embed it.
    """
    notification = _dispatch_notification(raw)
    if notification is None:
        return None
    # Protobuf parsing is permissive enough that unrelated bytes can
    # decode into this shape, so the caller must still check the result
    # against the spaces it actually knows about.
    return notification.space.id or None


def _extract_event_with_compiled_protos(raw: bytes) -> tuple[str, dict[str, Any]] | None:
    """Resolve a push payload to an HA `(event_type, data)` pair.

    Real pushes are `PushNotificationDispatchEvent` messages, so the
    payload is decoded structurally first and the qualifier is read from
    the `NotificationContent` oneof that actually carries it. That
    wrapper is what identifies the qualifier's type; guessing the type
    by trying each one in turn relabels events, because the tag protos
    share field numbers (see `_CONTENT_TAG_MAPS`).

    Only payloads that don't decode as a dispatch notification fall
    through to `_resolve_event_by_scanning`, the best-effort candidate
    scan kept for partial or unrecognised shapes.
    """
    recognised, structured = _resolve_event_from_dispatch(raw)
    if recognised:
        return structured
    # Logged at DEBUG because non-event pushes (blank / company /
    # accounting content) legitimately land here; on a capture it tells us
    # whether a missing event came from the structured path or the scan.
    _LOGGER.debug("Push payload is not a typed dispatch notification; scanning candidates")
    return _resolve_event_by_scanning(raw)


def _resolve_event_from_dispatch(
    raw: bytes,
) -> tuple[bool, tuple[str, dict[str, Any]] | None]:
    """Resolve the event from the typed `NotificationContent`, if present.

    Returns `(True, event)` when the payload is a dispatch notification
    whose content type identifies the qualifier — `event` being `None`
    if that content carries no mapped tag, since reporting nothing beats
    reporting a cross-decoded arm/disarm. Returns `(False, None)` when
    the payload isn't a dispatch notification with a typed content, so
    the caller falls back to the best-effort scan.
    """
    notification = _dispatch_notification(raw)
    if notification is None:
        return False, None

    if not notification.HasField("content"):
        return False, None
    content_case = notification.content.WhichOneof("content")
    if not content_case:
        return False, None
    tag_map = _CONTENT_TAG_MAPS.get(content_case)
    if tag_map is None:
        if content_case not in _UNMAPPED_CONTENT_CASES:
            # A content type newer than the vendored protos know about.
            # WARNING because it means real events may be going unreported
            # and the protos need a refresh — but still no scan: resolving
            # an unknown vocabulary's bytes against the space vocabulary
            # is how phantom arm/disarm events got invented.
            _LOGGER.warning(
                "Push carries unrecognised notification content %r; no event reported. "
                "Please open an issue — the integration's protocol definitions likely "
                "need updating.",
                content_case,
            )
        else:
            _LOGGER.debug("Push content %s is not mapped to HA events", content_case)
        return True, None
    typed_content = getattr(notification.content, content_case)
    if not typed_content.HasField("qualifier"):
        _LOGGER.debug("Push %s carries no qualifier", content_case)
        return True, None
    event = _event_from_qualifier(typed_content.qualifier, tag_map)
    if event is None:
        _LOGGER.debug(
            "Push %s tag %s is not mapped to an HA event",
            content_case,
            typed_content.qualifier.tag.WhichOneof("event_tag_case"),
        )
    return True, event


def _resolve_event_by_scanning(raw: bytes) -> tuple[str, dict[str, Any]] | None:
    """Best-effort resolution for payloads of unrecognised shape.

    Walks every embedded protobuf candidate, tries to decode it against
    each of the four event qualifier types (Space / Hub / Video /
    SmartLock), and collects every successful match. The highest-
    priority match wins per `TAG_PRIORITY` — real incidents (alarm,
    panic) outrank critical detectors (tamper, smoke), which outrank
    sensor activity (motion, door open, doorbell), which outranks
    user-driven state transitions (`space_armed`, `space_night_mode_
    on`). This avoids misreading a payload like "PORTA opened during
    night mode" — which carries both a `HubEventQualifier(door_opened)`
    and a `SpaceEventQualifier(space_night_mode_on)` — as a state
    transition rather than as the door-open event a user automation
    actually wants to trigger on.

    Tags absent from `TAG_PRIORITY` default to weight 0 so they still
    participate but lose to anything ranked — preserving the previous
    first-match-wins behaviour for the unranked long tail.

    Type ambiguity is unavoidable here: with no content wrapper to read,
    a candidate that decodes as several qualifier types keeps the legacy
    Space > Hub > Video > SmartLock precedence.
    """
    from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.hub import (  # noqa: PLC0415, E501
        qualifier_pb2 as hub_qualifier_pb2,
    )
    from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.smartlock import (  # noqa: PLC0415, E501
        qualifier_pb2 as smartlock_qualifier_pb2,
    )
    from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.space import (  # noqa: PLC0415, E501
        qualifier_pb2 as space_qualifier_pb2,
    )
    from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.event.video import (  # noqa: PLC0415, E501
        qualifier_pb2 as video_qualifier_pb2,
    )

    # (qualifier_proto_class, tag → ha_event_type map) pairs walked for
    # every candidate. Order doesn't affect the result thanks to
    # priority ranking, but matches the legacy pass numbering for grep.
    qualifier_table: list[tuple[type, dict[str, str]]] = [
        (space_qualifier_pb2.SpaceEventQualifier, SPACE_EVENT_TAG_MAP),
        (hub_qualifier_pb2.HubEventQualifier, HUB_EVENT_TAG_MAP),
        (video_qualifier_pb2.VideoEventQualifier, VIDEO_EVENT_TAG_MAP),
        (smartlock_qualifier_pb2.SmartLockEventQualifier, SMARTLOCK_EVENT_TAG_MAP),
    ]

    matches: list[tuple[int, str, dict[str, Any]]] = []
    for candidate in _find_embedded_messages(raw):
        # Within a single candidate, take only the first qualifier
        # type that decodes — preserving the legacy Space > Hub >
        # Video > SmartLock precedence so the same bytes don't get
        # double-counted under two different interpretations (the
        # protobuf wire format is permissive enough that a payload
        # legitimately encoding `space_armed` can also parse as
        # `HubEventQualifier(door_opened)` by sheer field-number
        # coincidence). Priority then decides between *different*
        # candidates, which is the case the refactor is for.
        for qualifier_class, tag_map in qualifier_table:
            resolved = _resolve_qualifier(candidate, qualifier_class, tag_map)
            if resolved is None:
                continue
            event_type, data = resolved
            matches.append((TAG_PRIORITY.get(data["raw_tag"], 0), event_type, data))
            break

    if not matches:
        return None

    # `max` returns the first element at the maximum priority — i.e.
    # ties resolve in candidate-scan order, which matches the previous
    # first-match-wins behaviour for tags that share a tier.
    _, event_type, data = max(matches, key=lambda m: m[0])
    return event_type, data


def _resolve_qualifier(
    candidate: bytes,
    qualifier_class: type,
    tag_map: dict[str, str],
) -> tuple[str, dict[str, Any]] | None:
    """Try to decode `candidate` as `qualifier_class` and return its
    `(event_type, data)` if its tag is in `tag_map`. Returns None on
    parse failure, missing tag, or unmapped tag.
    """
    try:
        qualifier = qualifier_class()
        qualifier.ParseFromString(candidate)
    except Exception:
        return None
    return _event_from_qualifier(qualifier, tag_map)


def _event_from_qualifier(
    # One of the four generated `*EventQualifier` messages; they share no
    # base class beyond `Message`, which doesn't expose `tag`/`transition`.
    qualifier: Any,  # noqa: ANN401
    tag_map: dict[str, str],
) -> tuple[str, dict[str, Any]] | None:
    """Map an already-decoded qualifier message to `(event_type, data)`.

    Returns None when the qualifier carries no tag or a tag absent from
    `tag_map`.
    """
    if not qualifier.HasField("tag"):
        return None
    tag_field = qualifier.tag.WhichOneof("event_tag_case")
    if not tag_field or tag_field not in tag_map:
        return None
    data: dict[str, Any] = {"raw_tag": tag_field}
    if qualifier.HasField("transition"):
        trans_field = qualifier.transition.WhichOneof("transition")
        if trans_field:
            data["transition"] = trans_field
    return tag_map[tag_field], data


def _read_varint(raw: bytes, i: int) -> tuple[int, int] | None:
    """Read the varint at `i`, returning `(value, next_index)`.

    Returns None when the varint runs past the end of the buffer.
    """
    value = 0
    shift = 0
    while i < len(raw):
        byte = raw[i]
        value |= (byte & 0x7F) << shift
        i += 1
        if not (byte & 0x80):
            return value, i
        shift += 7
        if shift > 63:  # malformed: longer than any legal varint
            return None
    return None


def _find_embedded_messages(raw: bytes) -> list[bytes]:
    """Extract candidate embedded protobuf messages from raw bytes.

    Walks the protobuf wire format field by field, collecting the
    contents of every length-delimited field and descending into each
    one. Consuming each field's payload — varint, 64-bit and 32-bit
    included — is what keeps the walk aligned: reading a varint's value
    byte as the next field tag used to derail the scan and step over the
    event qualifier entirely (#339). Tags are read as varints too, since
    field numbers above 15 need more than one byte (`tamper_opened` is
    field 17, tag `8a 01`).

    Candidates are collected regardless of size. The old `4 < length <
    500` filter dropped both tag-only qualifiers (4 bytes) and, because
    the recursive descent sat inside the filter, every event inside a
    wrapper larger than 500 bytes.
    """
    candidates: list[bytes] = []
    _walk_wire_format(raw, candidates, 0)
    return candidates


def _walk_wire_format(raw: bytes, candidates: list[bytes], depth: int) -> None:
    """Append every length-delimited payload in `raw` to `candidates`."""
    if depth > _MAX_WALK_DEPTH:
        return
    i = 0
    end = len(raw)
    while i < end:
        if len(candidates) >= _MAX_WALK_CANDIDATES:
            return
        read = _read_varint(raw, i)
        if read is None:
            return
        tag, i = read
        wire_type = tag & 0x07
        if wire_type == 0:  # varint
            read = _read_varint(raw, i)
            if read is None:
                return
            _, i = read
        elif wire_type == 1:  # 64-bit
            i += 8
        elif wire_type == 5:  # 32-bit
            i += 4
        elif wire_type == 2:  # length-delimited
            read = _read_varint(raw, i)
            if read is None:
                return
            length, i = read
            if i + length > end:
                return
            candidate = raw[i : i + length]
            i += length
            if len(candidate) >= _MIN_QUALIFIER_LEN:
                candidates.append(candidate)
                _walk_wire_format(candidate, candidates, depth + 1)
        else:
            # Wire types 3/4 (deprecated groups) and 6/7 (illegal) don't
            # appear in Ajax payloads; encountering one means this buffer
            # isn't protobuf at this offset, so stop rather than guess.
            return
        if i > end:
            return


# `NotificationContent` oneof case → the source field(s) it carries, in
# preference order. Mirrors `_CONTENT_TAG_MAPS` and exists for the same
# reason: all four `*NotificationSource` messages share an identical wire
# layout (`1 type` varint, `2 id` string, `3 name` string), so scanning for
# one and finding another succeeds silently and translates the type with the
# wrong vocabulary (#367). Only the container says which message it is.
#
# `space_notification_content` carries both: `space_source` names who or what
# acted (a member, a keyfob, a scenario), `hub_source` the hub. Prefer the
# former — it is the one that answers "who armed this?" (#362, #359).
_CONTENT_SOURCE_FIELDS: dict[str, tuple[str, ...]] = {
    "hub_notification_content": ("source",),
    "space_notification_content": ("space_source", "hub_source"),
    "video_notification_content": ("source",),
    "smart_lock_notification_content": ("source",),
}


def _source_info_from_message(source: Any) -> dict[str, Any] | None:  # noqa: ANN401
    """Render one `*NotificationSource` message, or None if it carries nothing.

    The vocabulary is read off the message's **own** descriptor rather than a
    hard-coded table, so each source is necessarily translated with its own
    enum and the four can never be crossed again.
    """
    if not (source.name and source.id and source.type > 0):
        return None
    enum_type = source.DESCRIPTOR.fields_by_name["type"].enum_type
    value = enum_type.values_by_number.get(source.type) if enum_type else None
    result: dict[str, Any] = {
        "device_name": source.name,
        "device_id": source.id,
        "device_type": value.name if value else str(source.type),
    }
    room_name = getattr(source, "room_name", "")
    if room_name:
        result["room_name"] = room_name
    return result


def _source_info_from_dispatch(raw: bytes) -> dict[str, Any] | None:
    """Read the source from the container that identifies its vocabulary.

    Returns None when the payload isn't a typed dispatch notification, so the
    caller falls back to the legacy scan. Returns `{}` — not None — for a
    recognised content that carries no usable source: a known container is
    authoritative, and rescanning it is how a neighbouring message gets
    mistaken for the source in the first place.
    """
    notification = _dispatch_notification(raw)
    if notification is None or not notification.HasField("content"):
        return None
    content_case = notification.content.WhichOneof("content")
    if not content_case:
        return None
    field_names = _CONTENT_SOURCE_FIELDS.get(content_case)
    if field_names is None:
        # Company / accounting / blank content have no source at all.
        return {} if content_case in _UNMAPPED_CONTENT_CASES else None
    typed_content = getattr(notification.content, content_case)
    for field_name in field_names:
        try:
            if not typed_content.HasField(field_name):
                continue
        except ValueError:  # pragma: no cover - field absent from this build
            continue
        info = _source_info_from_message(getattr(typed_content, field_name))
        if info is not None:
            return info
    return {}


def _extract_source_info(raw: bytes) -> dict[str, Any]:
    """Extract the source (device, person, scenario) that caused an event.

    Reads it structurally from the notification content when the payload is a
    typed dispatch message, and only otherwise falls back to the legacy scan
    below, which looks for a `HubNotificationSource` anywhere in the bytes.
    """
    structured = _source_info_from_dispatch(raw)
    if structured is not None:
        return structured
    try:
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.hub import (  # noqa: PLC0415, E501
            source_pb2,
            source_type_pb2,
        )
    except ImportError:
        _LOGGER.debug("Source proto not available")
        return {}

    # Build reverse map from enum value to name
    source_type_enum = source_type_pb2.HubNotificationSourceType.DESCRIPTOR
    type_name_map = {v.number: v.name for v in source_type_enum.values}

    # Scan for field 1 varint (0x08 XX) which is the source type field.
    # Try parsing HubNotificationSource from each potential start.
    for i in range(len(raw) - 5):
        if raw[i] != 0x08:
            continue
        # Try multiple slice lengths to find a valid parse
        for end in range(i + 10, min(i + 80, len(raw) + 1)):
            try:
                source = source_pb2.HubNotificationSource()
                source.ParseFromString(raw[i:end])
                if source.name and source.id and source.type > 0:
                    result: dict[str, Any] = {
                        "device_name": source.name,
                        "device_id": source.id,
                        "device_type": type_name_map.get(source.type, str(source.type)),
                    }
                    if source.HasField("_room_name") and source.room_name:
                        result["room_name"] = source.room_name
                    return result
            except Exception:
                continue
    return {}


def _extract_space_source_info(raw: bytes) -> dict[str, Any]:
    """Extract group identifier from a SpaceNotificationSource (#148).

    `space_group_*` SpaceEventTag events are wrapped in a
    SpaceNotificationContent whose `space_source` is a
    SpaceNotificationSource with `type == GROUP (3)`, `id == <group_id>`
    and `name == <group_name>`. The parser scans for that shape and
    returns `{"group_id": ..., "group_name": ...}` when found, so the
    per-group alarm panel can be refreshed instantly from FCM. Returns
    `{}` when the payload doesn't carry a group source (typical for
    whole-space arm/disarm).
    """
    try:
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.space import (  # noqa: PLC0415, E501
            source_pb2,
            source_type_pb2,
        )
    except ImportError:
        _LOGGER.debug("SpaceNotificationSource proto not available")
        return {}

    group_type_value = source_type_pb2.SpaceNotificationSourceType.GROUP
    # Same scan strategy as `_extract_source_info`: look for field 1
    # varint (0x08 XX) and try parsing increasingly long slices as
    # SpaceNotificationSource. Require id + name to be populated to
    # avoid latching onto a short prefix that parses cleanly but is
    # missing the trailing fields.
    for i in range(len(raw) - 5):
        if raw[i] != 0x08:
            continue
        for end in range(i + 6, min(i + 80, len(raw) + 1)):
            try:
                source = source_pb2.SpaceNotificationSource()
                source.ParseFromString(raw[i:end])
            except Exception:
                continue
            if source.type != group_type_value:
                continue
            if not source.id or not source.name:
                continue
            return {"group_id": source.id, "group_name": source.name}
    return {}


def _extract_space_group_info(raw: bytes) -> dict[str, Any]:
    """Extract group identifier from `DisplayGroups.groups[0]` (#148).

    Beta.8 extractor was too permissive: it parsed bytes as
    `DisplayGroups.Group` directly, so any `(printable_string,
    printable_string)` pair in the payload could be mistaken for a
    group — and on a real install it latched onto the 24-char
    `space_id` instead of the actual 8-char group id.

    Beta.9 tightens two ways:
      * Parse as the PARENT `DisplayGroups` (not the inner `Group`).
        That requires the bytes to look like a length-delimited list
        of Group sub-messages, not just one matched pair.
      * Sanity-check the resolved `group_hex_id` shape: short
        (≤ 16 chars) and hex-only. Matches Ajax's actual id format
        and excludes the `space_id` 24-char hex by length alone.

    Returns the first valid `(group_id, group_name)` found, or `{}`.
    """
    try:
        from systems.ajax.api.ecosystem.v2.communicationsvc.mobile.commonmodels.notification.space.additional.data import (  # noqa: PLC0415, E501
            display_groups_pb2,
        )
    except ImportError:
        _LOGGER.debug("DisplayGroups proto not available")
        return {}

    display_class = display_groups_pb2.DisplayGroups
    max_id_len = _MAX_GROUP_HEX_ID_LEN
    hex_chars = _HEX_CHARS
    # Scan for `0x0a` (DisplayGroups.groups wire tag, field 1
    # length-delim). Window up to 200 bytes per candidate gives enough
    # room for one Group entry plus the outer length prefix.
    for i in range(len(raw) - 5):
        if raw[i] != 0x0A:
            continue
        for end in range(i + 6, min(i + 200, len(raw) + 1)):
            try:
                display = display_class()
                display.ParseFromString(raw[i:end])
            except Exception:
                continue
            if not display.groups:
                continue
            for group in display.groups:
                if not group.group_hex_id or not group.group_name:
                    continue
                if not group.group_name.isprintable():
                    continue
                if len(group.group_hex_id) > max_id_len:
                    continue
                if not all(c in hex_chars for c in group.group_hex_id):
                    continue
                return {
                    "group_id": group.group_hex_id,
                    "group_name": group.group_name,
                }
    return {}


def _extract_event_raw(raw: bytes) -> tuple[str, dict[str, Any]] | None:
    """Fallback: extract event tag from raw protobuf bytes by scanning for known patterns."""
    # This is a best-effort fallback when compiled protos aren't available
    return None
