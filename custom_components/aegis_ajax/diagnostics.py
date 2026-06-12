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
        "stream_tasks": len(coordinator._stream_tasks),
        "notification_listener": coordinator.notification_listener is not None,
    }
