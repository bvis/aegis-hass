"""Diagnostics support for Ajax Security."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD

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

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "spaces": {
            sid: {
                "name": s.name,
                "security_state": s.security_state.name,
                "online": s.is_online,
                "malfunctions": s.malfunctions_count,
                "group_mode_enabled": s.group_mode_enabled,
                "night_mode_enabled": s.night_mode_enabled,
                "chime_status": s.chime_status.name,
                "groups": [
                    {
                        "id": g.id,
                        "name": g.name,
                        "security_state": g.security_state.name,
                    }
                    for g in s.groups
                ],
            }
            for sid, s in coordinator.spaces.items()
        },
        "devices": {
            did: {
                "name": d.name,
                "type": d.device_type,
                "state": d.state,
                "online": d.is_online,
                "malfunctions": d.malfunctions,
                "bypassed": d.bypassed,
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
                "name": k.name,
                "index": k.index,
                "active": k.active,
                "flags_hex": k.flags_hex,
            }
            for kid, k in coordinator.keyfobs.items()
        },
        "video_edge_onvif_rtsp": video_edge_probe,
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
        "stream_tasks": len(coordinator._stream_tasks),
        "notification_listener": coordinator.notification_listener is not None,
    }
