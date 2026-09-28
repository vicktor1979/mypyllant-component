"""Tests for fixtures that yield no System objects.

These cover the case where every home in the test data has an unsupported
control identifier.  The upstream API skips such homes (api.get_systems uses
`if control_identifier.is_unsupported: continue`), so coordinator.data is
empty — and DailyDataCoordinator raises UpdateFailed explicitly.

Using list_test_data(only_with_systems=False) targets exactly these fixtures
so the regular parametrized tests can require non-empty coordinator.data
without worrying about silently masking real failures.
"""

import pytest
from unittest import mock
from homeassistant.helpers.update_coordinator import UpdateFailed

from myPyllant.api import MyPyllantAPI
from myPyllant.tests.utils import list_test_data


@pytest.mark.parametrize("test_data", list_test_data(only_with_systems=False))
async def test_unsupported_control_identifier_yields_no_systems(
    mypyllant_aioresponses,
    mocked_api: MyPyllantAPI,
    system_coordinator_mock,
    test_data,
):
    """SystemCoordinator.data must be empty when all homes have unsupported identifiers."""
    with mypyllant_aioresponses(test_data) as _:
        system_coordinator_mock.data = (
            await system_coordinator_mock._async_update_data()
        )
        assert system_coordinator_mock.data == [], (
            f"Expected no systems for {test_data['_directory']}, "
            f"got {system_coordinator_mock.data}"
        )
    await mocked_api.aiohttp_session.close()


@pytest.mark.parametrize("test_data", list_test_data(only_with_systems=False))
async def test_daily_data_coordinator_fails_without_systems(
    mypyllant_aioresponses,
    mocked_api: MyPyllantAPI,
    daily_data_coordinator_mock,
    test_data,
):
    """DailyDataCoordinator must raise UpdateFailed when no systems are available."""
    with mypyllant_aioresponses(test_data) as _:
        system_coordinator = daily_data_coordinator_mock.hass_data["system_coordinator"]
        system_coordinator.data = await system_coordinator._async_update_data()
        assert system_coordinator.data == []
        with pytest.raises(UpdateFailed):
            await daily_data_coordinator_mock._async_update_data()
    await mocked_api.aiohttp_session.close()


async def test_empty_account_is_valid_and_does_not_retry(
    mocked_api: MyPyllantAPI,
    system_coordinator_mock,
):
    """A successful empty /homes response is a valid account state."""

    async def empty_homes():
        if False:  # pragma: no cover - keep this an async generator
            yield None

    system_coordinator_mock._refresh_session = mock.AsyncMock()
    system_coordinator_mock.api.get_homes = mock.Mock(return_value=empty_homes())

    data = await system_coordinator_mock._async_update_data()

    assert data == []
    assert system_coordinator_mock.empty_account is True
    assert system_coordinator_mock.system_failures == {}
    assert system_coordinator_mock.system_last_success == {}

    await mocked_api.aiohttp_session.close()


async def test_daily_data_skips_api_for_empty_account(
    mocked_api: MyPyllantAPI,
    daily_data_coordinator_mock,
):
    """An empty account must not spend API quota on daily-data refreshes."""
    system_coordinator = daily_data_coordinator_mock.hass_data["system_coordinator"]
    system_coordinator.empty_account = True
    system_coordinator.data = []
    daily_data_coordinator_mock._refresh_session = mock.AsyncMock()

    data = await daily_data_coordinator_mock._async_update_data()

    assert data == {}
    daily_data_coordinator_mock._refresh_session.assert_not_awaited()

    await mocked_api.aiohttp_session.close()
