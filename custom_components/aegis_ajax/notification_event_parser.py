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


def extract_notification_id(encoded_data: str) -> str | None:
    """Extract notification_id from base64-encoded push notification data."""
    try:
        raw = base64.b64decode(encoded_data)
        # PushNotificationDispatchEvent field 1 (Notification) is at tag 0x0a
        # Inside Notification, field 1 (id) is also tag 0x0a
        # We look for a 64-char hex string which is the notification ID format
        matches = re.findall(rb"[0-9A-Fa-f]{64}", raw)
        if matches:
            result: str = matches[0].decode("ascii")
            return result
    except Exception:
        _LOGGER.debug("Failed to extract notification_id from push")
    return None


def _extract_event_with_compiled_protos(raw: bytes) -> tuple[str, dict[str, Any]] | None:
    """Resolve a push payload to an HA `(event_type, data)` pair.

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


def _find_embedded_messages(raw: bytes) -> list[bytes]:
    """Extract candidate embedded protobuf messages from raw bytes.

    Scans for length-delimited fields (wire type 2) and extracts their content.
    Returns candidates from deepest nesting first (most likely to be the qualifier).
    """
    candidates: list[bytes] = []
    i = 0
    while i < len(raw) - 2:
        wire_type = raw[i] & 0x07
        if wire_type == 2:  # length-delimited
            # Read varint length
            j = i + 1
            length = 0
            shift = 0
            while j < len(raw):
                byte = raw[j]
                length |= (byte & 0x7F) << shift
                shift += 7
                j += 1
                if not (byte & 0x80):
                    break
            if j + length <= len(raw) and 4 < length < 500:
                candidate = raw[j : j + length]
                candidates.append(candidate)
                # Also recurse into the candidate
                inner = _find_embedded_messages(candidate)
                candidates.extend(inner)
            i = j + length if j + length <= len(raw) else i + 1
        else:
            i += 1
    return candidates


def _extract_source_info(raw: bytes) -> dict[str, Any]:
    """Extract device source information from raw protobuf bytes.

    Scans for HubNotificationSource by looking for the field pattern
    (type varint + id string + name string) and attempting proto parsing
    at each potential start position.
    """
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
