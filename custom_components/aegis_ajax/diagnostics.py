"""Diagnostics support for Ajax Security."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD
from homeassistant.util import dt as dt_util

from custom_components.aegis_ajax.api.models import (
    device_deactivation_kinds,
    is_device_deactivated,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.aegis_ajax import AjaxCobrandedConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    "password_hash",
    "email",
    "session_token",
    "device_id",
    "push_token",
    "fcm_api_key",
    "fcm_project_id",
    "fcm_app_id",
    "fcm_sender_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: AjaxCobrandedConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    # Probe the VideoEdge ONVIF/RTSP settings for each distinct video_edge_id
    # seen across the devices' source lists (#282). Read-only and best-effort:
    # it maps what's available towards a real camera entity without affecting
    # normal operation. Skipped entirely when there are no video devices.
    video_edge_kinds: dict[str, set[str]] = {}
    for device in coordinator.devices.values():
        for source in device.statuses.get("video_sources", []):
            ve_id = source.get("video_edge_id")
            if ve_id:
                video_edge_kinds.setdefault(ve_id, set()).add(source.get("kind"))

    video_edge_probe: dict[str, Any] = {}
    for ve_id, kinds in video_edge_kinds.items():
        settings: dict[str, Any] | None = None
        owning_space: str | None = None
        for space_id in coordinator.spaces:
            settings = await coordinator.devices_api.get_video_edge_onvif_rtsp_settings(
                space_id, ve_id
            )
            # Stop at the space that actually owns this VideoEdge.
            if settings is not None and "error" not in settings:
                owning_space = space_id
                break
        # Read the LAN IP / MAC (#282) so the dump has the full connection info
        # (IP + ONVIF/RTSP ports) to point HA's native ONVIF integration at.
        network = await coordinator.devices_api.get_video_edge_network(
            owning_space or next(iter(coordinator.spaces), ""), ve_id
        )
        # Read-only WebRTC feasibility probe (#322): does the account get past
        # the permission gate to start the app-style remote live stream? PII-free
        # (no credentials/URLs/SDP); no media is negotiated. This is the go/no-go
        # signal for a future camera entity for cloud-only (VPS) Home Assistant.
        webrtc = await coordinator.devices_api.probe_webrtc_initiate(
            owning_space or next(iter(coordinator.spaces), ""), ve_id
        )
        video_edge_probe[ve_id] = {
            "kinds": sorted(k for k in kinds if k),
            **(settings or {"error": "not_probed"}),
            "network": network,
            "webrtc": webrtc,
        }

    # Every user-chosen name below is reported as a LENGTH, never a value.
    # These downloads get pasted into public issues, and Ajax names are free
    # text that people set to where a thing is: street names and home
    # addresses have turned up as device names in practice. The length still
    # separates "named" from "empty", and the ids — which are the dump's own
    # keys — are what identify a device across sections. Same rule the IMEI
    # already follows below.
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        # The #419 deactivation carry, which self-corrects within one status
        # refresh and therefore used to leave no trace a user could send after
        # the fact. See `deactivation_carry_state`.
        "deactivation_carry": coordinator.deactivation_carry_state(),
        "spaces": {
            sid: {
                "name_length": len(s.name or ""),
                "security_state": s.security_state.name,
                # The panel's `triggered` is a client-side overlay (#426), not
                # part of the served security_state above — a dump that hides
                # it can't explain a panel showing (or missing) an alarm.
                "intrusion_alarm_active": sid in coordinator.alarmed_space_ids,
                # Same for the exit / entry delay overlay (#454): the option
                # and what is currently shown, so a panel reading `arming`
                # or `pending` (or not) can be explained from the dump.
                "delay_panel_states": coordinator.delay_panel_states is True,
                "delay_overlay": (
                    {
                        "kind": coordinator.delay_overlays[sid].kind.value,
                        "ends_at": coordinator.delay_overlays[sid].ends_at.isoformat(),
                        "from_hub": coordinator.delay_overlays[sid].from_hub,
                    }
                    if sid in coordinator.delay_overlays
                    else None
                ),
                "online": s.is_online,
                "malfunctions": s.malfunctions_count,
                "group_mode_enabled": s.group_mode_enabled,
                "night_mode_enabled": s.night_mode_enabled,
                "chime_status": s.chime_status.name,
                "groups": [
                    {
                        "id": g.id,
                        "name_length": len(g.name or ""),
                        "security_state": g.security_state.name,
                    }
                    for g in s.groups
                ],
            }
            for sid, s in coordinator.spaces.items()
        },
        "devices": {
            did: {
                "name_length": len(d.name or ""),
                "type": d.device_type,
                "state": d.state,
                "online": d.is_online,
                "malfunctions": d.malfunctions,
                "bypassed": d.bypassed,
                # Ajax group membership (#366), the per-device direction of
                # what the group alarm panel lists. `None` on a space with
                # group mode off, where the concept does not apply. Distinct
                # from the room, which a device has independently.
                "group_id": d.group_id,
                # What the bypass switch actually shows (#338). `bypassed` is
                # only one of the two sources: a device deactivated from the
                # Ajax app leaves it False and reports `*_deactivation_*`
                # statuses instead, so both are dumped side by side.
                "deactivated": is_device_deactivated(d),
                "deactivation_kinds": device_deactivation_kinds(d),
                "battery": (
                    {"level": d.battery.level, "low": d.battery.is_low} if d.battery else None
                ),
                "statuses": list(d.statuses.keys()),
                # Raw video-channel identity (#282/#290): the `About.Type`
                # value behind a `video_edge_*` device_type and the
                # source list (primary / nvr / cloud_archive + ids) that
                # links a camera channel to the NVR re-publishing it.
                # Keys absent on non-video devices.
                **(
                    {"video_edge_type": d.statuses["video_edge_type"]}
                    if "video_edge_type" in d.statuses
                    else {}
                ),
                **(
                    {"video_sources": d.statuses["video_sources"]}
                    if "video_sources" in d.statuses
                    else {}
                ),
                # LifeQuality environmental readings (#302): dump the actual
                # `lq_*` values (temperature / humidity / CO₂ + threshold/fault
                # enums) so a diagnostics download confirms which data path a
                # real device uses and in what units, before sensors are added.
                **{k: v for k, v in d.statuses.items() if k.startswith("lq_")},
            }
            for did, d in coordinator.devices.items()
        },
        "keyfobs": {
            kid: {
                "name_length": len(k.name or ""),
                "index": k.index,
                "active": k.active,
                "flags_hex": k.flags_hex,
            }
            for kid, k in coordinator.keyfobs.items()
        },
        "video_edge_onvif_rtsp": video_edge_probe,
        # What decides whether a hub gets an IMEI sensor at all (#379). A
        # `null` here is the answer to "why is my IMEI sensor unavailable":
        # the read never succeeded, so the entity was never offered this
        # start — and Home Assistant leaves one created on an earlier start
        # showing `unavailable` rather than removing it.
        # The IMEI itself identifies the hub's modem and these dumps get
        # pasted into public issues, so only its shape is reported: a length
        # of 15 says the read worked, 0 says it returned an empty string.
        "sim_info": {
            hub_id: (
                {
                    "status": info.status_name,
                    "active_sim": info.active_sim,
                    "imei_length": len(info.imei),
                }
                if (info := coordinator.sim_info.get(hub_id))
                else None
            )
            for hub_id in sorted(
                {space.hub_id for space in coordinator.spaces.values() if space.hub_id}
            )
        },
        # The version each hub says it is *running* (#388), read from its
        # status row rather than from the Ajax cloud. `null` means the hub
        # has not reported it — which is a real outcome, since hub firmwares
        # differ in which sub-keys they include, so the key is emitted for
        # every hub to keep "did not report" distinguishable from "build
        # that never looked".
        "hub_installed_firmware": {
            hub_id: ((state := coordinator.hub_network.get(hub_id)) and state.firmware_version)
            or None
            for hub_id in sorted(
                {space.hub_id for space in coordinator.spaces.values() if space.hub_id}
            )
        },
        # The packed integer the version above was decoded from. Dumped even
        # when the decode failed: a hub that packs it differently is then
        # answerable from this file instead of needing a capture session,
        # which is what this took to work out in the first place.
        "hub_installed_firmware_raw": {
            hub_id: ((state := coordinator.hub_network.get(hub_id)) and state.firmware_version_raw)
            or None
            for hub_id in sorted(
                {space.hub_id for space in coordinator.spaces.values() if space.hub_id}
            )
        },
        # Hub siren behaviour settings (#438), read from the hub's
        # SETTINGS_BODY row. `null` per hub means no SETTINGS_BODY has been
        # parsed yet; a null *field* inside the block means that hub's
        # firmware didn't include the sub-key. The raw wire integers ride
        # along because the byte width of these keys is unobserved — a hub
        # that packs them differently must be answerable from this file.
        "hub_siren_settings": {
            hub_id: (
                {
                    "on_panic_button": state.siren_on_panic_button,
                    "on_any_tamper": state.siren_on_any_tamper,
                    "on_panic_button_raw": state.siren_on_panic_button_raw,
                    "on_any_tamper_raw": state.siren_on_any_tamper_raw,
                }
                if (state := coordinator.hub_network.get(hub_id)) is not None
                else None
            )
            for hub_id in sorted(
                {space.hub_id for space in coordinator.spaces.values() if space.hub_id}
            )
        },
        # Firmware update state feeding the `update.*` entities (project
        # rule: every entity-driving field is dumped here). Both maps are
        # empty most of the time — Ajax only lists a hub/device while an
        # update is queued or in flight.
        "hub_firmware_updates": {
            hid: {"target_version": fw.target_version, "state": fw.state}
            for hid, fw in coordinator.hub_firmware_updates.items()
        },
        "device_firmware_updates": {
            did: {
                "target_version": dfu.target_version,
                "state": dfu.state,
                "progress": dfu.progress,
                "is_critical": dfu.is_critical,
            }
            for did, dfu in coordinator.device_firmware_updates.items()
        },
        # Last press epoch per Button, which is what drives the Button press
        # event entity (#348). Rendered as an ISO timestamp because that is what
        # makes it checkable: if the entity never fires, this says whether the
        # hub is reporting presses at all and when the last one landed.
        "button_press_epochs": {
            did: dt_util.utc_from_timestamp(seconds).isoformat()
            for did, seconds in coordinator._button_press_epochs.items()
        },
        "stream_tasks": len(coordinator._stream_tasks),
        "notification_listener": coordinator.notification_listener is not None,
        # Push delivery health (#437). A registration Ajax accepts and then
        # never delivers to produces no error anywhere: the client connects,
        # stays connected, and simply receives nothing. `ever_delivered` is the
        # field that separates that from a quiet house, because it is persisted
        # per credential set instead of resetting on every restart. The
        # fingerprint identifies the credential set without carrying any of the
        # four values, all of which are in `TO_REDACT` above.
        "push": _push_diagnostics(coordinator.notification_listener),
    }


def _push_diagnostics(listener: Any) -> dict[str, Any]:  # noqa: ANN401
    """Summarise push delivery for the dump (#437).

    Ages are relative because the underlying stamps are `time.monotonic()`,
    which has no meaning as a wall clock. Relative is also the more useful
    form here: "connected 4 h, never delivered" is the whole diagnosis.
    """
    if listener is None:
        return {"configured": False}
    started_at = listener._fcm_client_started_at
    last_push_at = listener.last_push_at
    now = time.monotonic()
    return {
        "configured": True,
        "connected": listener.is_fcm_connected,
        # The denominator for `ever_delivered` (#437): zero deliveries says
        # nothing until you know how many space events push could have carried.
        "hub_events_while_connected": listener.hub_events_while_connected,
        "client_connected_for_seconds": (
            round(now - started_at) if started_at is not None else None
        ),
        "pushes_received_since_start": listener.pushes_received,
        "last_push_seconds_ago": (round(now - last_push_at) if last_push_at is not None else None),
        "ever_delivered": listener.ever_delivered,
        "first_delivery_at": listener.first_delivery_at,
        "creds_fingerprint": listener.creds_fingerprint,
        "cache_creds_fingerprint": listener.cache_creds_fingerprint,
    }
