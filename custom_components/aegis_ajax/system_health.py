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

from homeassistant.core import callback

from custom_components.aegis_ajax.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.components import system_health
    from homeassistant.core import HomeAssistant

# Maximum age (seconds) a successful poll can have before the card
# downgrades reachability to "unreachable". Defaults to 10 min, well
# above the 5-min default poll interval but tight enough that a
# genuine cloud outage shows up before users notice missing entities.
_REACHABILITY_STALE_AFTER = 600.0


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
    info: dict[str, Any] = {"configured_accounts": len(entries)}
    if not entries:
        info["can_reach_server"] = "no accounts configured"
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

    # Reachability is "is the integration talking to Ajax at all", derived
    # from our own data paths rather than a separate HTTPS HEAD probe — the
    # gRPC host doesn't answer plain HTTPS GET/HEAD, so `async_check_can_reach_url`
    # always returned "unreachable" even when polling worked (#106).
    #
    # Crucially, don't key it off the polled refresh alone: HTS and FCM updates
    # call `async_set_updated_data`, which resets HA's poll timer, so the polled
    # `_async_update_data` (the only thing that stamps `last_update_success_time`)
    # can be starved indefinitely while data keeps flowing — which left the card
    # reading "unreachable" on a perfectly healthy install (#236). A live HTS
    # connection is equally valid proof that sensor data is flowing;
    # `last_poll` below still reports its true age.
    poll_fresh = (
        last_update_age_seconds is not None and last_update_age_seconds <= _REACHABILITY_STALE_AFTER
    )
    push_fresh = (
        last_push_age_seconds is not None and last_push_age_seconds <= _REACHABILITY_STALE_AFTER
    )
    # The gRPC poll and the HTS stream are the paths that carry live
    # sensor/device state, so a fresh one of either means entities are
    # updating. FCM push only carries security events (arm/disarm, alarm,
    # motion, doorbell) — a recent push proves the cloud is reachable but does
    # NOT keep sensor values current. Report that distinctly so an "FCM alive,
    # poll + HTS dead" install can't masquerade as fully healthy while its
    # sensors quietly go stale (#236).
    if poll_fresh or hts_connected > 0:
        info["can_reach_server"] = "reachable"
    elif push_fresh:
        info["can_reach_server"] = "push only — sensor data may be stale"
    elif last_update_age_seconds is None:
        info["can_reach_server"] = "never polled"
    else:
        info["can_reach_server"] = "unreachable"

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
