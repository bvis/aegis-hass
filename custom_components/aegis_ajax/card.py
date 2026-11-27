"""Serve the dashboard cards and load them in every dashboard."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

FRONTEND_DIR = Path(__file__).parent / "frontend"
# The activity card; the devices card with the security view built around it.
CARD_FILES = ("aegis-activity-card.js", "aegis-devices-card.js")
CARD_PATH = FRONTEND_DIR / CARD_FILES[0]
CARD_URL = f"/aegis_ajax/{CARD_FILES[0]}"


async def async_register_card(hass: HomeAssistant) -> None:
    """Register each card's static path and add it to the frontend."""
    paths = []
    for name in CARD_FILES:
        path = FRONTEND_DIR / name
        url = f"/aegis_ajax/{name}"
        content = await hass.async_add_executor_job(path.read_bytes)
        paths.append(StaticPathConfig(url, str(path), True))
        # Served with a month-long cache, so the URL carries a hash of the content:
        # a new version of the card reaches browsers on the next page load.
        add_extra_js_url(hass, f"{url}?v={hashlib.sha256(content).hexdigest()[:12]}")
    await hass.http.async_register_static_paths(paths)
