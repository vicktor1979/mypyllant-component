"""Home Assistant regressions for the history opt-out and quota separation."""
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientResponseError, RequestInfo
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from custom_components.mypyllant import async_setup_entry, SystemCoordinator
from custom_components.mypyllant.api_queue import get_api_refresh_queue
from custom_components.mypyllant.const import DOMAIN, OPTION_FETCH_ENERGY_HISTORY
from custom_components.mypyllant.quota import (
    QuotaBackoffStore, async_migrate_energy_quota, is_energy_quota_exception,
)
from custom_components.mypyllant.utils import extract_quota_duration
from tests.utils import get_config_entry


def quota_error(path: str, *, status: int = 403) -> ClientResponseError:
    url = URL(f"https://api.vaillant-group.com/{path}")
    return ClientResponseError(
        RequestInfo(url, "GET", CIMultiDictProxy(CIMultiDict())),
        (), status=status,
        message="Out of call volume quota. Quota will be replenished in 4.15:58:14.",
    )


@pytest.mark.parametrize("path,status,expected", [
    ("emf/v2/system/devices/device/buckets", 403, True),
    ("emf/v2/system/devices/device/buckets", 429, False),
    ("emf/v2/system/currentSystem", 403, False),
    ("homes", 403, False),
])
def test_energy_scope_is_not_assumed_for_other_endpoints(path, status, expected):
    assert is_energy_quota_exception(quota_error(path, status=status)) is expected


def test_day_prefixed_replenishment_time():
    assert extract_quota_duration(quota_error("homes")) == 403094


async def test_old_energy_quota_is_migrated_without_shortening(hass):
    account = QuotaBackoffStore(hass, "scope-test")
    energy = QuotaBackoffStore(hass, "scope-test", scope="energy")
    await account.async_set_from_exception(
        quota_error("emf/v2/system/devices/device/buckets")
    )
    original = account.state
    assert await async_migrate_energy_quota(account, energy)
    assert not account.is_active
    assert energy.state == original
    restored = QuotaBackoffStore(hass, "scope-test", scope="energy")
    await restored.async_load()
    assert restored.state == original


async def test_energy_off_skips_startup_coordinator(hass):
    entry = get_config_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={OPTION_FETCH_ENERGY_HISTORY: False})
    api = MagicMock()
    api.login = AsyncMock()
    api.aiohttp_session.close = AsyncMock()

    async def initial(coordinator):
        coordinator.data = [MagicMock()]

    with (
        patch("custom_components.mypyllant.MyPyllantAPI", return_value=api),
        patch.object(SystemCoordinator, "async_config_entry_first_refresh", initial),
        patch("custom_components.mypyllant.DailyDataCoordinator") as daily_factory,
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        assert await async_setup_entry(hass, entry)
    daily_factory.assert_not_called()
    assert "daily_data_coordinator" not in hass.data[DOMAIN][entry.entry_id]
    assert hass.data[DOMAIN][entry.entry_id]["energy_history_enabled"] is False


async def test_queue_is_shared_and_reentrant(hass):
    queue = get_api_refresh_queue(hass)
    assert queue is get_api_refresh_queue(hass)
    async with queue.slot("one", "setup"):
        async with queue.slot("one", "system refresh"):
            assert queue.status("one")["active"]
    assert not queue.status("one")["active"]


async def test_disabled_daily_coordinator_makes_no_api_calls(hass):
    from custom_components.mypyllant.coordinator import DailyDataCoordinator

    entry = get_config_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={OPTION_FETCH_ENERGY_HISTORY: False})
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}
    api = MagicMock()
    coordinator = DailyDataCoordinator(hass, api, entry, timedelta(hours=3))
    assert await coordinator._async_update_data() == {}
    assert not api.mock_calls
