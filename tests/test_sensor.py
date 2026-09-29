import pytest as pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.recorder.statistics import StatisticMeanType
from homeassistant.helpers.entity_registry import DATA_REGISTRY, EntityRegistry
from homeassistant.loader import DATA_COMPONENTS, DATA_INTEGRATIONS

from myPyllant.api import MyPyllantAPI
from myPyllant.models import DeviceData, DeviceDataBucket
from myPyllant.enums import CircuitState
from myPyllant.tests.generate_test_data import DATA_DIR
from myPyllant.tests.utils import list_test_data, load_test_data

from custom_components.mypyllant.sensor import (
    CircuitFlowTemperatureSensor,
    CircuitHeatingCurveSensor,
    CircuitMinFlowTemperatureSetpointSensor,
    CircuitStateSensor,
    DataSensor,
    DomesticHotWaterCurrentSpecialFunctionSensor,
    DomesticHotWaterOperationModeSensor,
    DomesticHotWaterSetPointSensor,
    DomesticHotWaterTankTemperatureSensor,
    HomeEntity,
    SystemOutdoorTemperatureSensor,
    ZoneCurrentRoomTemperatureSensor,
    ZoneCurrentSpecialFunctionSensor,
    ZoneDesiredRoomTemperatureSetpointSensor,
    ZoneHeatingOperatingModeSensor,
    ZoneHumiditySensor,
    SystemDeviceOnOffCyclesSensor,
    SystemDeviceOperationTimeSensor,
    create_system_sensors,
    SystemTopDHWTemperatureSensor,
    SystemBottomDHWTemperatureSensor,
    SystemTopCHTemperatureSensor,
    SystemDeviceCurrentPowerSensor,
    SystemAPIRequestCount,
    VaillantApiStatusSensor,
)
from custom_components.mypyllant.const import DOMAIN
from tests.utils import get_config_entry


def test_api_request_count_unique_id_is_config_entry_specific(hass):
    """API request counters from different config entries must not collide."""
    hass.data[DATA_REGISTRY] = EntityRegistry(hass)

    coordinator_1 = MagicMock()
    coordinator_1.hass = hass
    coordinator_1.entry.entry_id = "entry_1"

    coordinator_2 = MagicMock()
    coordinator_2.hass = hass
    coordinator_2.entry.entry_id = "entry_2"

    sensor_1 = SystemAPIRequestCount(coordinator_1)
    sensor_2 = SystemAPIRequestCount(coordinator_2)

    assert sensor_1.unique_id == f"{DOMAIN}_entry_1_api_request_count"
    assert sensor_2.unique_id == f"{DOMAIN}_entry_2_api_request_count"
    assert sensor_1.unique_id != sensor_2.unique_id


def test_vaillant_api_status_sensor_uses_hungarian_diagnostics():
    """Account API diagnostic state and attributes are presented in Hungarian."""
    config = MagicMock()
    config.entry_id = "entry_1"
    config.title = "kazan01@example.invalid"

    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.last_exception = None
    coordinator.homes_last_refresh = "2026-09-29T06:00:00+00:00"
    coordinator.empty_account = False
    coordinator._quota_hit_time = None
    coordinator._quota_exc_info = None

    quota = MagicMock()
    quota.is_active = False
    quota.state = None

    sensor = VaillantApiStatusSensor(config, coordinator, quota)

    assert sensor.native_value == "Kapcsolódva"
    assert sensor.name == "Vaillant API állapot"
    assert sensor.extra_state_attributes["Fiók"] == "kazan01@example.invalid"
    assert "HTTP állapot" in sensor.extra_state_attributes
    assert "Utolsó sikeres frissítés" in sensor.extra_state_attributes


def test_vaillant_api_status_sensor_exists_without_system_coordinator():
    """Quota diagnostics remain available before the normal coordinator loads."""
    config = MagicMock()
    config.entry_id = "entry_1"
    config.title = "kazan01@example.invalid"

    quota = MagicMock()
    quota.is_active = True
    quota.state = {
        "status": 429,
        "url": "https://api.example.invalid/homes",
        "message": "Rate limit is exceeded. Try again later.",
    }
    quota.until = datetime.now(timezone.utc) + timedelta(minutes=10)
    quota.remaining_seconds = 600

    sensor = VaillantApiStatusSensor(config, None, quota)

    assert sensor.native_value == "Korlátozva (API-korlát)"
    assert sensor.available is True
    assert sensor.extra_state_attributes["HTTP állapot"] == 429
    assert sensor.extra_state_attributes["Hátralévő idő (mp)"] == 600


def test_vaillant_api_status_sensor_reports_rate_limit_in_hungarian():
    """Persisted 429 backoff is surfaced as a Hungarian rate-limit state."""
    config = MagicMock()
    config.entry_id = "entry_1"
    config.title = "kazan01@example.invalid"

    coordinator = MagicMock()
    coordinator.last_update_success = False
    coordinator.last_exception = None
    coordinator.homes_last_refresh = None
    coordinator.empty_account = False
    coordinator._quota_hit_time = None
    coordinator._quota_exc_info = None

    quota = MagicMock()
    quota.is_active = True
    quota.state = {
        "status": 429,
        "url": "https://api.example.invalid/homes",
        "message": "Rate limit is exceeded. Try again in 42 seconds.",
    }
    quota.until = datetime.now(timezone.utc) + timedelta(seconds=42)
    quota.remaining_seconds = 42

    sensor = VaillantApiStatusSensor(config, coordinator, quota)
    attrs = sensor.extra_state_attributes

    assert sensor.native_value == "Korlátozva (API-korlát)"
    assert attrs["HTTP állapot"] == 429
    assert attrs["Hiba oka"] == "A Vaillant API túl sok kérést érzékelt"
    assert attrs["API-korlát miatti várakozás"] is True


def test_vaillant_api_status_sensor_extracts_403_quota_retry_time():
    """403 quota responses expose the server-provided replenishment time."""
    from aiohttp import ClientResponseError

    config = MagicMock()
    config.entry_id = "entry_1"
    config.title = "kazan01@example.invalid"

    request_info = MagicMock()
    request_info.real_url = "https://api.example.invalid/homes"
    exc = ClientResponseError(
        request_info=request_info,
        history=(),
        status=403,
        message=(
            'Quota Exceeded, response was: { "statusCode": 403, '
            '"message": "Out of call volume quota. '
            'Quota will be replenished in 00:08:54." }'
        ),
        headers={},
    )

    hit_at = datetime.now(timezone.utc)
    coordinator = MagicMock()
    coordinator.last_update_success = False
    coordinator.last_exception = exc
    coordinator.homes_last_refresh = "2026-09-29T12:21:10+00:00"
    coordinator.empty_account = False
    coordinator._quota_hit_time = hit_at
    coordinator._quota_end_time = hit_at + timedelta(minutes=8, seconds=54)
    coordinator._quota_exc_info = exc

    quota = MagicMock()
    quota.is_active = False
    quota.state = None
    quota.until = None

    sensor = VaillantApiStatusSensor(config, coordinator, quota)
    attrs = sensor.extra_state_attributes

    assert sensor.native_value == "Korlátozva (API-korlát)"
    assert attrs["HTTP állapot"] == 403
    assert attrs["Hiba oka"] == "A Vaillant API túl sok kérést érzékelt"
    assert attrs["Újrapróbálkozás időpontja"] is not None
    assert 530 <= attrs["Hátralévő idő (mp)"] <= 534
    assert attrs["API-korlát miatti várakozás"] is True


def test_vaillant_api_status_sensor_keeps_403_retry_timestamp_after_window():
    """The parsed quota replenishment timestamp remains visible after expiry."""
    from aiohttp import ClientResponseError

    config = MagicMock()
    config.entry_id = "entry_1"
    config.title = "kazan01@example.invalid"

    request_info = MagicMock()
    request_info.real_url = "https://api.example.invalid/homes"
    exc = ClientResponseError(
        request_info=request_info,
        history=(),
        status=403,
        message=(
            'Quota Exceeded, response was: { "statusCode": 403, '
            '"message": "Out of call volume quota. '
            'Quota will be replenished in 00:08:54." }'
        ),
        headers={},
    )

    hit_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    coordinator = MagicMock()
    coordinator.last_update_success = False
    coordinator.last_exception = exc
    coordinator.homes_last_refresh = "2026-09-29T12:21:10+00:00"
    coordinator.empty_account = False
    coordinator._quota_hit_time = hit_at
    coordinator._quota_end_time = hit_at + timedelta(minutes=8, seconds=54)
    coordinator._quota_exc_info = exc

    quota = MagicMock()
    quota.is_active = False
    quota.state = None
    quota.until = None

    sensor = VaillantApiStatusSensor(config, coordinator, quota)
    attrs = sensor.extra_state_attributes

    assert attrs["HTTP állapot"] == 403
    assert attrs["Újrapróbálkozás időpontja"] is not None
    assert attrs["Hátralévő idő (mp)"] == 0
    assert attrs["API-korlát miatti várakozás"] is False


@pytest.mark.parametrize("test_data", list_test_data(only_with_systems=True))
async def test_create_system_sensors(
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
        sensors = await create_system_sensors(hass, config_entry)
        assert len(sensors) > 0

        await mocked_api.aiohttp_session.close()


@pytest.mark.parametrize("test_data", list_test_data(only_with_systems=True))
async def test_system_sensors(
    mypyllant_aioresponses, mocked_api: MyPyllantAPI, system_coordinator_mock, test_data
):
    with mypyllant_aioresponses(test_data) as _:
        system_coordinator_mock.data = (
            await system_coordinator_mock._async_update_data()
        )
        if "outdoorTemperature" in str(test_data):
            assert isinstance(
                SystemOutdoorTemperatureSensor(0, system_coordinator_mock).native_value,
                float,
            )
        # TODO: No water pressure in no_cooling.yaml
        # assert isinstance(
        #    SystemWaterPressureSensor(0, system_coordinator_mock).native_value, float
        # )

        home = HomeEntity(0, system_coordinator_mock)
        assert isinstance(home.device_info, dict)
        assert (
            home.extra_state_attributes
            and "controller_type" in home.extra_state_attributes
        )

        await mocked_api.aiohttp_session.close()


async def test_zone_sensors(
    hass,
    mypyllant_aioresponses,
    mocked_api: MyPyllantAPI,
    system_coordinator_mock,
):
    test_data = load_test_data(DATA_DIR / "heatpump_cooling")
    with mypyllant_aioresponses(test_data) as _:
        system_coordinator_mock.data = (
            await system_coordinator_mock._async_update_data()
        )
        if "humidity" in str(test_data):
            assert isinstance(
                ZoneHumiditySensor(0, 0, system_coordinator_mock).native_value, float
            )
        if "currentTemperature" in str(test_data):
            assert isinstance(
                ZoneCurrentRoomTemperatureSensor(
                    0, 0, system_coordinator_mock
                ).native_value,
                float,
            )
        assert isinstance(
            ZoneDesiredRoomTemperatureSetpointSensor(
                0, 0, system_coordinator_mock
            ).native_value,
            float | int,
        )
        assert isinstance(
            ZoneCurrentSpecialFunctionSensor(
                0, 0, system_coordinator_mock
            ).native_value,
            str,
        )
        assert isinstance(
            ZoneHeatingOperatingModeSensor(0, 0, system_coordinator_mock).native_value,
            str,
        )
        await mocked_api.aiohttp_session.close()


@pytest.mark.parametrize("test_data", list_test_data(only_with_systems=True))
async def test_circuit_sensors(
    mypyllant_aioresponses, mocked_api: MyPyllantAPI, system_coordinator_mock, test_data
):
    with mypyllant_aioresponses(test_data) as _:
        system_coordinator_mock.data = (
            await system_coordinator_mock._async_update_data()
        )
        circuit_state = CircuitStateSensor(0, 0, system_coordinator_mock)
        assert isinstance(circuit_state.native_value, CircuitState)
        assert isinstance(circuit_state.extra_state_attributes, dict)
        if "room_temperature_control_mode" in test_data:
            assert (
                "room_temperature_control_mode" in circuit_state.extra_state_attributes
            )
        # Regression test for #422 / #440: heating_circuit_flow_setpoint was
        # surfaced via extra_fields before upstream promoted it to a typed
        # Circuit field. CircuitStateSensor must continue to expose it as an
        # attribute so existing automations/templates do not break silently.
        # Guarded against fixtures that lack the field (e.g. no_system) where
        # the assertion would otherwise be vacuous (None == None).
        if "heatingCircuitFlowSetpoint" in str(test_data):
            assert (
                "heating_circuit_flow_setpoint" in circuit_state.extra_state_attributes
            )
            assert (
                circuit_state.extra_state_attributes["heating_circuit_flow_setpoint"]
                == circuit_state.circuit.heating_circuit_flow_setpoint
            )
        assert isinstance(
            CircuitFlowTemperatureSensor(0, 0, system_coordinator_mock).native_value,
            (int, float, complex),
        )
        if "heatingCurve" in str(test_data):
            assert isinstance(
                CircuitHeatingCurveSensor(0, 0, system_coordinator_mock).native_value,
                (int, float, complex),
            )
        if "minFlowTemperatureSetpoint" in str(test_data):
            assert isinstance(
                CircuitMinFlowTemperatureSetpointSensor(
                    0, 0, system_coordinator_mock
                ).native_value,
                (int, float, complex),
            )
        await mocked_api.aiohttp_session.close()


@pytest.mark.parametrize(
    "test_data", list_test_data(only_with_systems=True, only_with_dhw=True)
)
async def test_domestic_hot_water_sensor(
    hass,
    mypyllant_aioresponses,
    mocked_api: MyPyllantAPI,
    system_coordinator_mock,
    test_data,
):
    with mypyllant_aioresponses(test_data) as _:
        system_coordinator_mock.data = (
            await system_coordinator_mock._async_update_data()
        )
        assert isinstance(
            DomesticHotWaterOperationModeSensor(
                0, 0, system_coordinator_mock
            ).native_value,
            str,
        )
        assert isinstance(
            DomesticHotWaterSetPointSensor(0, 0, system_coordinator_mock).native_value,
            (int, float, complex),
        )
        assert isinstance(
            DomesticHotWaterCurrentSpecialFunctionSensor(
                0, 0, system_coordinator_mock
            ).native_value,
            str,
        )
        if "currentDhwTankTemperature" in str(test_data):
            assert isinstance(
                DomesticHotWaterTankTemperatureSensor(
                    0, 0, system_coordinator_mock
                ).native_value,
                float,
            )
        await mocked_api.aiohttp_session.close()


@pytest.mark.parametrize("test_data", list_test_data(only_with_systems=True))
async def test_data_sensor(
    mypyllant_aioresponses,
    mocked_api: MyPyllantAPI,
    daily_data_coordinator_mock,
    test_data,
):
    with mypyllant_aioresponses(test_data) as _:
        system_coordinator = daily_data_coordinator_mock.hass_data["system_coordinator"]
        system_coordinator.data = await system_coordinator._async_update_data()
        daily_data_coordinator_mock.data = (
            await daily_data_coordinator_mock._async_update_data()
        )
        system_id = next(iter(daily_data_coordinator_mock.data), None)
        if system_id is None or not daily_data_coordinator_mock.data[system_id]:
            await mocked_api.aiohttp_session.close()
            pytest.skip(f"No devices in system {system_id}, skipping data sensor tests")
        data_sensor = DataSensor(system_id, 0, 0, daily_data_coordinator_mock)
        assert isinstance(
            data_sensor.device_data,
            DeviceData,
        )
        assert isinstance(
            data_sensor.native_value,
            (int, float, complex),
        )
        assert isinstance(
            data_sensor.name,
            str,
        )
        assert data_sensor.last_reset is None
        await mocked_api.aiohttp_session.close()


async def test_device_sensor(
    mypyllant_aioresponses,
    mocked_api: MyPyllantAPI,
    system_coordinator_mock,
):
    test_data = load_test_data(DATA_DIR / "vrc700_mpc_rts.yaml")
    with mypyllant_aioresponses(test_data) as _:
        system_coordinator_mock.data = (
            await system_coordinator_mock._async_update_data()
        )
        assert isinstance(
            SystemDeviceOnOffCyclesSensor(0, 0, system_coordinator_mock).native_value,
            int,
        )
        assert isinstance(
            SystemDeviceOperationTimeSensor(0, 0, system_coordinator_mock).native_value,
            float,
        )
        assert isinstance(
            SystemDeviceCurrentPowerSensor(0, 0, system_coordinator_mock).native_value,
            int,
        )
        await mocked_api.aiohttp_session.close()


async def test_additional_system_sensors(
    mypyllant_aioresponses,
    mocked_api: MyPyllantAPI,
    system_coordinator_mock,
):
    test_data = load_test_data(DATA_DIR / "two_systems")
    with mypyllant_aioresponses(test_data) as _:
        system_coordinator_mock.data = (
            await system_coordinator_mock._async_update_data()
        )
        assert isinstance(
            SystemTopDHWTemperatureSensor(0, system_coordinator_mock).native_value,
            float,
        )
        assert isinstance(
            SystemBottomDHWTemperatureSensor(0, system_coordinator_mock).native_value,
            float,
        )
        assert isinstance(
            SystemTopCHTemperatureSensor(0, system_coordinator_mock).native_value,
            float,
        )
        await mocked_api.aiohttp_session.close()


# ---------------------------------------------------------------------------
# Helpers for _write_hourly_statistics tests
# ---------------------------------------------------------------------------

_MIDNIGHT = datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc)
_SYSTEM_ID = "test_system"


def _make_buckets(values):
    buckets = []
    for i, value in enumerate(values):
        start = _MIDNIGHT + timedelta(hours=i)
        buckets.append(
            DeviceDataBucket(
                start_date=start,
                end_date=start + timedelta(hours=1),
                value=value,
            )
        )
    return buckets


def _make_sensor(hass, buckets, data_from=_MIDNIGHT):
    device = MagicMock()
    device.device_uuid = "test-uuid"
    device.name_display = "Arotherm Plus"
    device.brand_name = "Vaillant"
    device.product_name_display = "aroTHERM plus"

    device_data = DeviceData(
        operation_mode="HEATING",
        energy_type="CONSUMED_ELECTRICAL_ENERGY",
        data_from=data_from,
        total_consumption=sum(v for v in (b.value for b in buckets) if v is not None),
        device=device,
        data=buckets,
    )
    coordinator = MagicMock()
    coordinator.data = {
        _SYSTEM_ID: {
            "devices_data": [[device_data]],
            "home_name": "Test Home",
        }
    }
    sensor = DataSensor(_SYSTEM_ID, 0, 0, coordinator)
    sensor.hass = hass
    return sensor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_write_hourly_statistics_correct_data(hass):
    buckets = _make_buckets([100.0, 200.0, 300.0])
    sensor = _make_sensor(hass, buckets)

    with (
        patch("custom_components.mypyllant.sensor.get_instance") as mock_recorder,
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as mock_stats,
    ):
        mock_recorder.return_value.async_add_executor_job = AsyncMock(return_value={})
        await sensor._write_hourly_statistics()

    mock_stats.assert_called_once()
    _, metadata, stats = mock_stats.call_args[0]
    stats = list(stats)

    assert metadata["statistic_id"].startswith(f"{DOMAIN}:")
    assert metadata["mean_type"] == StatisticMeanType.NONE
    assert metadata["has_sum"] is True
    assert metadata["unit_class"] == "energy"

    assert len(stats) == 3
    assert stats[0]["start"] == buckets[0].start_date
    # state is day-cumulative (mirrors octopus_energy), equal to sum when baseline is 0
    assert stats[0]["state"] == 100.0
    assert stats[0]["sum"] == 100.0
    assert stats[1]["state"] == 300.0
    assert stats[1]["sum"] == 300.0
    assert stats[2]["state"] == 600.0
    assert stats[2]["sum"] == 600.0


async def test_write_hourly_statistics_skips_none_values(hass):
    buckets = _make_buckets([100.0, None, 300.0])
    sensor = _make_sensor(hass, buckets)

    with (
        patch("custom_components.mypyllant.sensor.get_instance") as mock_recorder,
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as mock_stats,
    ):
        mock_recorder.return_value.async_add_executor_job = AsyncMock(return_value={})
        await sensor._write_hourly_statistics()

    mock_stats.assert_called_once()
    _, _, stats = mock_stats.call_args[0]
    stats = list(stats)

    assert len(stats) == 2
    assert stats[0]["state"] == 100.0
    assert stats[0]["sum"] == 100.0
    assert stats[1]["state"] == 400.0  # day-cumulative: 100 + 300
    assert stats[1]["sum"] == 400.0


async def test_write_hourly_statistics_no_data(hass):
    sensor = _make_sensor(hass, [])

    with (
        patch("custom_components.mypyllant.sensor.get_instance") as mock_recorder,
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as mock_stats,
    ):
        mock_recorder.return_value.async_add_executor_job = AsyncMock(return_value={})
        await sensor._write_hourly_statistics()

    mock_stats.assert_not_called()


async def test_write_hourly_statistics_no_device_data(hass):
    coordinator = MagicMock()
    coordinator.data = {
        _SYSTEM_ID: {
            "devices_data": [],
            "home_name": "Test Home",
        }
    }
    sensor = DataSensor(_SYSTEM_ID, 0, 0, coordinator)
    sensor.hass = hass

    with patch(
        "custom_components.mypyllant.sensor.async_add_external_statistics"
    ) as mock_stats:
        await sensor._write_hourly_statistics()

    mock_stats.assert_not_called()


async def test_async_added_to_hass_writes_statistics(hass):
    buckets = _make_buckets([100.0, 200.0, 300.0])
    sensor = _make_sensor(hass, buckets)

    with (
        patch("custom_components.mypyllant.sensor.get_instance") as mock_recorder,
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as mock_stats,
        patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            new_callable=AsyncMock,
        ),
    ):
        mock_recorder.return_value.async_add_executor_job = AsyncMock(return_value={})
        await sensor.async_added_to_hass()

    mock_stats.assert_called_once()
    await sensor.async_will_remove_from_hass()


async def test_write_hourly_statistics_carries_forward_previous_sum(hass):
    """Yesterday's final sum is the baseline so statistics are always monotonically
    increasing across day boundaries, preventing a negative delta at BST midnight."""
    buckets = _make_buckets([100.0, 200.0])
    sensor = _make_sensor(hass, buckets)
    stat_id = f"{DOMAIN}:{sensor.unique_id}".lower().replace("-", "_")

    with (
        patch("custom_components.mypyllant.sensor.get_instance") as mock_recorder,
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as mock_stats,
    ):
        mock_recorder.return_value.async_add_executor_job = AsyncMock(
            return_value={stat_id: [{"sum": 3804.0}]}
        )
        await sensor._write_hourly_statistics()

    mock_stats.assert_called_once()
    _, _, stats = mock_stats.call_args[0]
    stats = list(stats)

    assert stats[0]["sum"] == 3904.0  # 3804 baseline + 100
    assert stats[1]["sum"] == 4104.0  # 3804 baseline + 100 + 200
    assert (
        stats[0]["state"] == 100.0
    )  # day-cumulative resets daily, independent of baseline
    assert stats[1]["state"] == 300.0


async def test_write_hourly_statistics_without_data_from(hass):
    """The myVAILLANT API does not always return `from`. Statistics must still be written,
    with day_start derived from the first bucket's timestamp (mirrors octopus_energy)."""
    buckets = _make_buckets([100.0, 200.0])
    sensor = _make_sensor(hass, buckets, data_from=None)

    with (
        patch("custom_components.mypyllant.sensor.get_instance") as mock_recorder,
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as mock_stats,
    ):
        mock_recorder.return_value.async_add_executor_job = AsyncMock(return_value={})
        await sensor._write_hourly_statistics()

    mock_stats.assert_called_once()
    _, _, stats = mock_stats.call_args[0]
    stats = list(stats)
    assert len(stats) == 2
    assert stats[0]["sum"] == 100.0
    assert stats[1]["sum"] == 300.0
    # last_reset derived from first bucket floored to midnight
    assert stats[0]["last_reset"] == _MIDNIGHT


async def test_async_added_to_hass_survives_statistics_failure(hass):
    """A failure writing statistics must not prevent the entity from loading."""
    buckets = _make_buckets([100.0, 200.0, 300.0])
    sensor = _make_sensor(hass, buckets)

    with (
        patch(
            "custom_components.mypyllant.sensor.DataSensor._write_hourly_statistics",
            new_callable=AsyncMock,
            side_effect=RuntimeError("recorder not ready"),
        ),
        patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            new_callable=AsyncMock,
        ),
    ):
        # Should not raise
        await sensor.async_added_to_hass()

    await sensor.async_will_remove_from_hass()


async def test_write_hourly_statistics_resets_state_across_day_boundary(hass):
    """The coordinator fetches a 2-day window (yesterday + today). sum must stay
    monotonically increasing across the midnight boundary, while state and last_reset
    reset at the start of each day (mirrors octopus_energy's day-cumulative state)."""
    day1 = datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 28, 0, 0, tzinfo=timezone.utc)
    buckets = [
        DeviceDataBucket(
            start_date=day1 + timedelta(hours=22),
            end_date=day1 + timedelta(hours=23),
            value=100.0,
        ),
        DeviceDataBucket(
            start_date=day1 + timedelta(hours=23),
            end_date=day2,
            value=200.0,
        ),
        DeviceDataBucket(
            start_date=day2,
            end_date=day2 + timedelta(hours=1),
            value=300.0,
        ),
        DeviceDataBucket(
            start_date=day2 + timedelta(hours=1),
            end_date=day2 + timedelta(hours=2),
            value=400.0,
        ),
    ]
    sensor = _make_sensor(hass, buckets, data_from=day1)

    with (
        patch("custom_components.mypyllant.sensor.get_instance") as mock_recorder,
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as mock_stats,
    ):
        mock_recorder.return_value.async_add_executor_job = AsyncMock(return_value={})
        await sensor._write_hourly_statistics()

    _, _, stats = mock_stats.call_args[0]
    stats = list(stats)
    assert len(stats) == 4
    # sum is cumulative across both days (monotonic)
    assert [s["sum"] for s in stats] == [100.0, 300.0, 600.0, 1000.0]
    # state resets at the day-2 boundary
    assert [s["state"] for s in stats] == [100.0, 300.0, 300.0, 700.0]
    # last_reset is the midnight of each bucket's own day
    assert stats[0]["last_reset"] == day1
    assert stats[1]["last_reset"] == day1
    assert stats[2]["last_reset"] == day2


async def test_write_hourly_statistics_skips_unchanged_buckets(hass):
    """Buckets already published with the same sum must not be rewritten, so stable
    history stays untouched and the baseline never gets re-anchored on every run."""
    buckets = _make_buckets([100.0, 200.0, 300.0])
    sensor = _make_sensor(hass, buckets)
    stat_id = f"{DOMAIN}:{sensor.unique_id}".lower().replace("-", "_")

    async def fake_job(func, hass, start_time, *rest):
        if start_time < buckets[0].start_date:
            # baseline lookup (window_start - 7 days .. window_start)
            return {}
        return {
            stat_id: [
                {"start": buckets[0].start_date.timestamp(), "sum": 100.0},
                {"start": buckets[1].start_date.timestamp(), "sum": 300.0},
            ]
        }

    with (
        patch("custom_components.mypyllant.sensor.get_instance") as mock_recorder,
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as mock_stats,
    ):
        mock_recorder.return_value.async_add_executor_job = AsyncMock(
            side_effect=fake_job
        )
        await sensor._write_hourly_statistics()

    mock_stats.assert_called_once()
    _, _, stats = mock_stats.call_args[0]
    stats = list(stats)
    assert len(stats) == 1
    assert stats[0]["start"] == buckets[2].start_date
    assert stats[0]["sum"] == 600.0


async def test_write_hourly_statistics_rewrites_corrected_bucket(hass):
    """A late API correction for an already-published hour (e.g. yesterday's final
    hour settling after midnight) must still be rewritten even though a stat already
    exists for it - the skip only applies to genuinely unchanged buckets."""
    buckets = _make_buckets([100.0, 250.0, 300.0])
    sensor = _make_sensor(hass, buckets)
    stat_id = f"{DOMAIN}:{sensor.unique_id}".lower().replace("-", "_")

    async def fake_job(func, hass, start_time, *rest):
        if start_time < buckets[0].start_date:
            # baseline lookup (window_start - 7 days .. window_start)
            return {}
        return {
            stat_id: [
                {"start": buckets[0].start_date.timestamp(), "sum": 100.0},
                {"start": buckets[1].start_date.timestamp(), "sum": 300.0},  # stale
            ]
        }

    with (
        patch("custom_components.mypyllant.sensor.get_instance") as mock_recorder,
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as mock_stats,
    ):
        mock_recorder.return_value.async_add_executor_job = AsyncMock(
            side_effect=fake_job
        )
        await sensor._write_hourly_statistics()

    mock_stats.assert_called_once()
    _, _, stats = mock_stats.call_args[0]
    stats = list(stats)
    assert len(stats) == 2
    assert stats[0]["start"] == buckets[1].start_date
    assert stats[0]["sum"] == 350.0
    assert stats[1]["start"] == buckets[2].start_date
    assert stats[1]["sum"] == 650.0


async def test_write_hourly_statistics_baseline_stable_across_repeated_polls(hass):
    """Regression test: baseline must be the last-published sum strictly BEFORE
    this window, not the single most-recent stat ever recorded. An unbounded
    "latest ever" lookup returns this same run's own previous output on the
    next poll, so baseline + sum(window's buckets) double-counts the window
    every time and `sum` grows on every single poll forever, even though
    nothing about the underlying consumption changed."""
    buckets = _make_buckets([100.0, 200.0, 300.0])
    sensor = _make_sensor(hass, buckets)
    stat_id = f"{DOMAIN}:{sensor.unique_id}".lower().replace("-", "_")

    published: dict = {}

    async def fake_job(func, hass, start_time, *rest):
        if start_time < buckets[0].start_date:
            # baseline lookup: nothing published before this window in this test
            return {}
        # existing-stats lookup: reflects whatever the previous poll wrote
        return {stat_id: published.get(stat_id, [])}

    with (
        patch("custom_components.mypyllant.sensor.get_instance") as mock_recorder,
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as mock_stats,
    ):
        mock_recorder.return_value.async_add_executor_job = AsyncMock(
            side_effect=fake_job
        )

        # first poll: writes all three buckets
        await sensor._write_hourly_statistics()
        _, _, first_stats = mock_stats.call_args[0]
        first_stats = list(first_stats)
        published[stat_id] = [
            {"start": s["start"].timestamp(), "sum": s["sum"]} for s in first_stats
        ]
        assert [s["sum"] for s in first_stats] == [100.0, 300.0, 600.0]

        # second poll: identical bucket data, nothing should have changed
        mock_stats.reset_mock()
        await sensor._write_hourly_statistics()

    # everything was already published with matching sums, so nothing new
    # gets written - the sums must not have grown
    mock_stats.assert_not_called()


def test_today_total_consumption_ignores_yesterdays_buckets(hass):
    """The coordinator fetches a 2-day window (yesterday + today) so
    _write_hourly_statistics can backfill yesterday's last hour. native_value
    must not inherit yesterday's total from that wider window - it should
    reset to only today's consumption, the same way the History graph is
    expected to reset at midnight."""
    day1 = datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 28, 0, 0, tzinfo=timezone.utc)
    buckets = [
        DeviceDataBucket(
            start_date=day1 + timedelta(hours=22),
            end_date=day1 + timedelta(hours=23),
            value=100.0,
        ),
        DeviceDataBucket(
            start_date=day1 + timedelta(hours=23),
            end_date=day2,
            value=200.0,
        ),
        DeviceDataBucket(
            start_date=day2,
            end_date=day2 + timedelta(hours=1),
            value=300.0,
        ),
        DeviceDataBucket(
            start_date=day2 + timedelta(hours=1),
            end_date=day2 + timedelta(hours=2),
            value=400.0,
        ),
    ]
    sensor = _make_sensor(hass, buckets, data_from=day1)

    with patch("custom_components.mypyllant.sensor.datetime") as mock_datetime:
        mock_datetime.now.return_value = day2 + timedelta(hours=2)

        # today_total_consumption only sums day2's buckets (300 + 400), not
        # the full 2-day window (100 + 200 + 300 + 400 = 1000)
        assert sensor.today_total_consumption == 700.0
        assert sensor.native_value == 700.0
        assert sensor.native_value != sensor.device_data.total_consumption_rounded


def test_today_total_consumption_survives_api_bucket_lag_at_midnight(hass):
    """Regression test: right after midnight the myVAILLANT API can briefly
    lag and not yet return a bucket for the new day, so the last fetched
    bucket is still dated yesterday. today_total_consumption must anchor
    "today" to the actual current time, not to data[-1]'s date, or it would
    wrongly sum yesterday's total again instead of resetting to 0."""
    day1 = datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 28, 0, 0, tzinfo=timezone.utc)
    buckets = [
        DeviceDataBucket(
            start_date=day1 + timedelta(hours=22),
            end_date=day1 + timedelta(hours=23),
            value=100.0,
        ),
        DeviceDataBucket(
            start_date=day1 + timedelta(hours=23),
            end_date=day2,
            value=200.0,
        ),
    ]
    sensor = _make_sensor(hass, buckets, data_from=day1)

    with patch("custom_components.mypyllant.sensor.datetime") as mock_datetime:
        # "now" is already a few minutes into day2, but the API's last
        # bucket is still dated day1 (23:00-00:00)
        mock_datetime.now.return_value = day2 + timedelta(minutes=5)

        assert sensor.today_total_consumption == 0.0
        assert sensor.native_value == 0.0


def test_today_total_consumption_no_data(hass):
    sensor = _make_sensor(hass, [])
    assert sensor.today_total_consumption == 0.0


def test_today_total_consumption_all_none_values(hass):
    buckets = _make_buckets([None, None])
    with patch("custom_components.mypyllant.sensor.datetime") as mock_datetime:
        mock_datetime.now.return_value = _MIDNIGHT + timedelta(hours=1)
        sensor = _make_sensor(hass, buckets)
        assert sensor.today_total_consumption == 0.0
