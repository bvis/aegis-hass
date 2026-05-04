"""System Health card for Aegis for Ajax — in-UI diagnostics summary.

Surfaces a one-line snapshot of cloud reachability, gRPC poll
freshness, HTS stream state, and FCM push status under
**Settings → System → Repairs → System Information**, so triage
questions like "are my events arriving?" / "is the cloud reachable?"
can be answered without log archaeology or downloading the full
diagnostics JSON.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from homeassistant.components import system_health
from homeassistant.core import callback

from custom_components.aegis_ajax.const import DOMAIN, GRPC_HOST

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@callback
def async_register(
    hass: HomeAssistant,
    register: system_health.SystemHealthRegistration,
) -> None:
    """Called by HA when the system_health component scans integrations."""
    register.async_register_info(_system_health_info)


async def _system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Build the System Health dict for the integration's row."""
    entries = hass.config_entries.async_entries(DOMAIN)
    info: dict[str, Any] = {
        "can_reach_server": system_health.async_check_can_reach_url(hass, f"https://{GRPC_HOST}"),
        "configured_accounts": len(entries),
    }
    if not entries:
        return info

    spaces_total = 0
    hts_connected = 0
    fcm_connected = 0
    pushes_total = 0
    last_push_age_seconds: float | None = None
    last_update_age_seconds: float | None = None

    # `last_update_success_time` is wall-clock (datetime), while
    # `last_push_at` is monotonic — keep them on their own clocks.
    now_mono = time.monotonic()
    now_wall = time.time()
    for entry in entries:
        coordinator = getattr(entry, "runtime_data", None)
        if coordinator is None:
            continue
        spaces_total += len(coordinator.spaces)
        if coordinator.is_hts_connected:
            hts_connected += 1
        last_update = coordinator.last_update_success_time
        if last_update is not None and hasattr(last_update, "timestamp"):
            age = now_wall - last_update.timestamp()
            if age >= 0 and (last_update_age_seconds is None or age < last_update_age_seconds):
                last_update_age_seconds = age
        listener = coordinator.notification_listener
        if listener is None:
            continue
        if listener.is_fcm_connected:
            fcm_connected += 1
        pushes_total += listener.pushes_received
        if listener.last_push_at is not None:
            age = now_mono - listener.last_push_at
            if last_push_age_seconds is None or age < last_push_age_seconds:
                last_push_age_seconds = age

    info["spaces"] = spaces_total
    info["hts_connected"] = f"{hts_connected}/{len(entries)}"
    info["fcm_connected"] = f"{fcm_connected}/{len(entries)}"
    info["pushes_received"] = pushes_total
    info["last_push"] = _format_age(last_push_age_seconds)
    info["last_poll"] = _format_age(last_update_age_seconds)
    return info


def _format_age(seconds: float | None) -> str:
    """Render a duration as 'never' / '12s ago' / '4m ago' / '3h ago'."""
    if seconds is None:
        return "never"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"
