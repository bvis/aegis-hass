"""Tests for the on-disk CRA company store (#561)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.api.models import MonitoringCompany, MonitoringCompanyStatus
from custom_components.aegis_ajax.monitoring_companies import MonitoringCompaniesStore

_ONE = MonitoringCompany(name="Central One", status=MonitoringCompanyStatus.APPROVED, hex_id="A1")


def _store(raw: object) -> tuple[MonitoringCompaniesStore, MagicMock]:
    backing = MagicMock()
    backing.async_load = AsyncMock(return_value=raw)
    backing.async_save = AsyncMock()
    with patch("custom_components.aegis_ajax.monitoring_companies.Store", return_value=backing):
        return MonitoringCompaniesStore(MagicMock(), "entry-1"), backing


@pytest.mark.asyncio
async def test_round_trip() -> None:
    store, backing = _store(None)
    await store.async_save({"s1": (_ONE,), "s2": ()})
    saved = backing.async_save.await_args.args[0]
    assert saved == {"s1": [{"name": "Central One", "status": 2, "hex_id": "A1"}], "s2": []}

    backing.async_load = AsyncMock(return_value=saved)
    assert await store.async_load() == {"s1": (_ONE,), "s2": ()}


@pytest.mark.asyncio
async def test_unreadable_space_is_dropped_so_it_gets_fetched_again() -> None:
    store, _ = _store(
        {
            "s1": [{"name": "Central One", "status": 2, "hex_id": "A1"}],
            "s2": [{"name": "X", "status": 99, "hex_id": "B2"}],
            "s3": [{"name": "Y"}],
        }
    )
    assert await store.async_load() == {"s1": (_ONE,)}


@pytest.mark.asyncio
async def test_missing_or_broken_file_is_empty() -> None:
    store, backing = _store(None)
    assert await store.async_load() == {}
    backing.async_load = AsyncMock(side_effect=ValueError("bad json"))
    assert await store.async_load() == {}
