"""The activity card is served and loaded by the integration."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.card import CARD_PATH, CARD_URL, async_register_card


@pytest.mark.asyncio
async def test_card_is_served_and_loaded_with_a_content_hash() -> None:
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda func: func())
    hass.http.async_register_static_paths = AsyncMock()

    with patch("custom_components.aegis_ajax.card.add_extra_js_url") as add_url:
        await async_register_card(hass)

    (paths,) = hass.http.async_register_static_paths.await_args.args
    assert (paths[0].url_path, paths[0].path) == (CARD_URL, str(CARD_PATH))
    digest = hashlib.sha256(CARD_PATH.read_bytes()).hexdigest()[:12]
    add_url.assert_called_once_with(hass, f"{CARD_URL}?v={digest}")


def test_card_defines_its_element() -> None:
    source = CARD_PATH.read_text(encoding="utf-8")
    assert 'customElements.define("aegis-activity-card"' in source


@pytest.mark.asyncio
@pytest.mark.parametrize("components", [set(), {"frontend"}])
async def test_setup_registers_the_card_only_with_a_frontend(components: set[str]) -> None:
    from custom_components.aegis_ajax import async_setup

    hass = MagicMock()
    hass.config.components = components
    with patch("custom_components.aegis_ajax.async_register_card", AsyncMock()) as register:
        assert await async_setup(hass, {}) is True

    assert register.await_count == len(components)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_card_logic_self_check() -> None:
    script = Path(__file__).parents[1] / "js" / "activity_card_check.js"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert result.returncode == 0, result.stderr
