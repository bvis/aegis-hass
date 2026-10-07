"""CRA companies per space, fetched once and kept on disk (#561).

From client version 3.57 Ajax no longer puts the companies in the space
snapshot, and the endpoint that has them also returns the country's whole
sign-up list. A space changes CRA rarely, so the companies are fetched when
the entry is set up or reconfigured and read from disk on every other start.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from custom_components.aegis_ajax.api.models import MonitoringCompany, MonitoringCompanyStatus
from custom_components.aegis_ajax.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_STORAGE_VERSION = 1


def _storage_key(entry_id: str) -> str:
    return f"{DOMAIN}_monitoring_companies_{entry_id}"


class MonitoringCompaniesStore:
    """Per-entry `{space_id: companies}`, best-effort like the device cache."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, _STORAGE_VERSION, _storage_key(entry_id))

    async def async_load(self) -> dict[str, tuple[MonitoringCompany, ...]]:
        """Return the stored companies; a space that can't be read is left out."""
        try:
            raw = await self._store.async_load()
        except Exception:  # noqa: BLE001
            return {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, tuple[MonitoringCompany, ...]] = {}
        for space_id, rows in raw.items():
            try:
                result[space_id] = tuple(
                    MonitoringCompany(
                        name=str(row["name"]),
                        status=MonitoringCompanyStatus(row["status"]),
                        hex_id=str(row["hex_id"]),
                    )
                    for row in rows
                )
            except (KeyError, TypeError, ValueError):
                continue
        return result

    async def async_save(self, companies: dict[str, tuple[MonitoringCompany, ...]]) -> None:
        await self._store.async_save(
            {
                space_id: [
                    {"name": c.name, "status": int(c.status), "hex_id": c.hex_id} for c in rows
                ]
                for space_id, rows in companies.items()
            }
        )


async def async_remove_monitoring_companies(hass: HomeAssistant, entry_id: str) -> None:
    """Drop the stored companies so the next setup fetches them again."""
    await Store(hass, _STORAGE_VERSION, _storage_key(entry_id)).async_remove()
