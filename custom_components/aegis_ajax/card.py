"""Serve the activity card and load it in every dashboard."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

CARD_PATH = Path(__file__).parent / "frontend" / "aegis-activity-card.js"
CARD_URL = "/aegis_ajax/aegis-activity-card.js"


async def async_register_card(hass: HomeAssistant) -> None:
    """Register the card's static path and add it to the frontend."""
    content = await hass.async_add_executor_job(CARD_PATH.read_bytes)
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_PATH), True)])
    # Served with a month-long cache, so the URL carries a hash of the content:
    # a new version of the card reaches browsers on the next page load.
    add_extra_js_url(hass, f"{CARD_URL}?v={hashlib.sha256(content).hexdigest()[:12]}")
