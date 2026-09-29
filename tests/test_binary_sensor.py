from unittest.mock import Mock

import pytest
from homeassistant.helpers.entity_registry import DATA_REGISTRY, EntityRegistry
from homeassistant.loader import DATA_COMPONENTS, DATA_INTEGRATIONS

from custom_components.mypyllant import SystemCoordinator
from myPyllant.api import MyPyllantAPI
from myPyllant.models import System
from myPyllant.tests.generate_test_data import DATA_DIR
from myPyllant.tests.utils import list_test_data, load_test_data

from custom_components.mypyllant.binary_sensor import (
    CircuitIsCoolingAllowed,
    ControlError,
    ControlOnline,
    GatewayApiConnection,
    GatewayOnline,
    SystemControlEntity,
    async_setup_entry,
    ZoneIsManualCoolingActive,
)
from custom_components.mypyllant.utils import CircuitEntity
from custom_components.mypyllant.const import DOMAIN
from tests.utils import get_config_entry


@pytest.mark.parametrize("test_data", list_test_data(only_with_systems=True))
async def test_async_setup_binary_sensors(
    hass,
    mypyllant_aioresponses,
    mocked_api: MyPyllantAPI,
    system_coordinator_mock,
    test_data,
):
    hass.data[DATA_COMPONENTS] = {}
    hass.data[DATA_INTEGRATIONS] = {}
    hass.data[DATA_REGISTRY] = EntityRegistry(hass)
    with mypyllant_aioresponses(test_data) as _:
        config_entry = get_config_entry()
        system_coordinator_mock.data = (
            await system_coordinator_mock._async_update_data()
        )
        hass.data[DOMAIN] = {
            config_entry.entry_id: {"system_coordinator": system_coordinator_mock}
        }
        mock = Mock(return_value=None)
        await async_setup_entry(hass, config_entry, mock)
        mock.assert_called_once()
        assert len(mock.call_args.args[0]) > 0

        await mocked_api.aiohttp_session.close()


@pytest.mark.parametrize("test_data", list_test_data(only_with_systems=True))
async def test_system_binary_sensors(
    mypyllant_aioresponses, mocked_api: MyPyllantAPI, system_coordinator_mock, test_data
):
    with mypyllant_aioresponses(test_data) as _:
        system_coordinator_mock.data = (
            await system_coordinator_mock._async_update_data()
        )
        system = SystemControlEntity(0, system_coordinator_mock)
        assert isinstance(system.device_info, dict)

        circuit = CircuitEntity(0, 0, system_coordinator_mock)
        assert isinstance(circuit.device_info, dict)
        assert isinstance(circuit.system, System)

        assert isinstance(ControlError(0, system_coordinator_mock).is_on, bool)
        assert isinstance(ControlError(0, system_coordinator_mock).name, str)
        assert ControlOnline(0, system_coordinator_mock).is_on is True
        assert isinstance(ControlOnline(0, system_coordinator_mock).name, str)
        # TODO: May  moved to zones, see no_cooling.yaml
        # assert isinstance(
        #    CircuitIsCoolingAllowed(0, 0, system_coordinator_mock).is_on, bool
        # )
        assert isinstance(
            CircuitIsCoolingAllowed(0, 0, system_coordinator_mock).name, str
        )
        await mocked_api.aiohttp_session.close()


async def test_control_error(
    mypyllant_aioresponses,
    mocked_api: MyPyllantAPI,
    system_coordinator_mock: SystemCoordinator,
):
    test_data = load_test_data(DATA_DIR / "ambisense2.yaml")
    with mypyllant_aioresponses(test_data) as _:
        system_coordinator_mock.data = (
            await system_coordinator_mock._async_update_data()
        )
        control_error = ControlError(0, system_coordinator_mock)
        assert control_error.is_on
        await mocked_api.aiohttp_session.close()


async def test_is_manual_cooling_active(
    mypyllant_aioresponses,
    mocked_api: MyPyllantAPI,
    system_coordinator_mock: SystemCoordinator,
):
    test_data = load_test_data(DATA_DIR / "ventilation")
    with mypyllant_aioresponses(test_data) as _:
        system_coordinator_mock.data = (
            await system_coordinator_mock._async_update_data()
        )
        manual_cooling = ZoneIsManualCoolingActive(0, 0, system_coordinator_mock)
        assert not manual_cooling.is_on
        await mocked_api.aiohttp_session.close()


async def test_gateway_api_health_and_gateway_online_state_are_independent(
    mypyllant_aioresponses,
    mocked_api: MyPyllantAPI,
    system_coordinator_mock: SystemCoordinator,
):
    """Cached API data may succeed even while the physical gateway is offline."""
    test_data = list_test_data(only_with_systems=True)[0]
    with mypyllant_aioresponses(test_data) as _:
        system_coordinator_mock.data = (
            await system_coordinator_mock._async_update_data()
        )

    home = system_coordinator_mock.homes[0]
    offline_home = Mock(
        system_id=home.system_id,
        home_name=home.home_name,
        nomenclature=home.nomenclature,
        extra_fields={"online_state": "OFFLINE"},
    )
    # Mock would otherwise synthesize a fake attribute for getattr().
    offline_home.online_state = None
    system_coordinator_mock.get_home = Mock(return_value=offline_home)

    api_connection = GatewayApiConnection(home, system_coordinator_mock)
    gateway_online = GatewayOnline(home, system_coordinator_mock)

    assert api_connection.is_on is True
    assert gateway_online.is_on is False
    assert gateway_online.gateway_online_state == "OFFLINE"
    assert api_connection.extra_state_attributes["api_fetch_success"] is True
    assert (
        api_connection.extra_state_attributes["gateway_online_state"]
        == "OFFLINE"
    )

    await mocked_api.aiohttp_session.close()


def test_gateway_online_reads_online_state_from_extra_fields(system_coordinator_mock):
    """myPyllant Home currently stores online_state in extra_fields."""
    home = Mock(
        system_id="system-1",
        home_name="Test home",
        nomenclature="VR 921",
        extra_fields={"online_state": "ONLINE"},
    )
    home.online_state = None
    system_coordinator_mock.get_home = Mock(return_value=home)

    gateway_online = GatewayOnline(home, system_coordinator_mock)

    assert gateway_online.available is True
    assert gateway_online.is_on is True
    assert gateway_online.gateway_online_state == "ONLINE"


def test_control_online_unknown_is_unavailable_not_disconnected(
    system_coordinator_mock,
):
    """Do not display None connection data as a false/disconnected status."""
    system = Mock()
    system.id = "system-1"
    system.connected = None
    system.home.home_name = "Test home"
    system.home.nomenclature = "VR 921"
    system_coordinator_mock.data = [system]
    system_coordinator_mock.last_update_success = True
    system_coordinator_mock.is_system_available = Mock(return_value=True)

    entity = ControlOnline(0, system_coordinator_mock)

    assert entity.available is False
    assert entity.is_on is False
    assert entity.name == "Test home System Connection Status"
    assert entity.extra_state_attributes["connection_status_fetched"] is False
