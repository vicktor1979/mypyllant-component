from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from myPyllant.models import System, AmbisenseDevice, Home

from . import SystemCoordinator
from .const import DOMAIN
from .utils import (
    EntityList,
    ZoneCoordinatorEntity,
    AmbisenseDeviceCoordinatorEntity,
    CircuitEntity,
)

_LOGGER = logging.getLogger(__name__)


def _get_home_online_state(home: Home) -> str | None:
    """Return the gateway cloud state from current and legacy Home model shapes."""
    state = getattr(home, "online_state", None)
    if state is None:
        extra_fields = getattr(home, "extra_fields", None)
        if isinstance(extra_fields, Mapping):
            state = extra_fields.get("online_state")
    if state is None:
        return None
    if hasattr(state, "value"):
        state = state.value
    return str(state).upper()


async def async_setup_entry(
    hass: HomeAssistant, config: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensor platform."""
    coordinator: SystemCoordinator = hass.data[DOMAIN][config.entry_id][
        "system_coordinator"
    ]
    sensors: EntityList[BinarySensorEntity] = EntityList()

    # Gateway/API status is based on the homes list instead of coordinator.data,
    # so a home that already fails during initial setup can still be identified.
    for home in coordinator.homes:
        sensors.append(lambda home=home: GatewayApiConnection(home, coordinator))
        sensors.append(lambda home=home: GatewayOnline(home, coordinator))

    if not coordinator.data:
        _LOGGER.warning("No system data, only adding gateway status sensors")
        async_add_entities(sensors)  # type: ignore
        return

    for index, system in enumerate(coordinator.data):
        sensors.append(lambda: ControlError(index, coordinator))
        sensors.append(lambda: ControlOnline(index, coordinator))
        sensors.append(lambda: FirmwareUpdateRequired(index, coordinator))
        sensors.append(lambda: FirmwareUpdateEnabled(index, coordinator))
        if system.eebus:
            sensors.append(lambda: EebusEnabled(index, coordinator))
            sensors.append(lambda: EebusCapable(index, coordinator))
        for circuit_index, _ in enumerate(system.circuits):
            sensors.append(
                lambda: CircuitIsCoolingAllowed(index, circuit_index, coordinator)
            )
        for zone_index, zone in enumerate(system.zones):
            if zone.is_manual_cooling_active is not None:
                sensors.append(
                    lambda: ZoneIsManualCoolingActive(index, zone_index, coordinator)
                )
        if system.ambisense_rooms:
            for room_index, room in enumerate(system.ambisense_rooms):
                for device in room.room_configuration.devices:
                    if device.unreach is not None:
                        sensors.append(
                            lambda: AmbisenseDeviceLowBattery(
                                index, room_index, device, coordinator
                            )
                        )
                    if device.low_bat is not None:
                        sensors.append(
                            lambda: AmbisenseDeviceUnreachable(
                                index, room_index, device, coordinator
                            )
                        )

    async_add_entities(sensors)  # type: ignore


class GatewayApiConnection(CoordinatorEntity, BinarySensorEntity):
    """Per-home status for the latest system API fetch."""

    coordinator: SystemCoordinator
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, home: Home, coordinator: SystemCoordinator) -> None:
        super().__init__(coordinator)
        self.home = home
        self.system_id = str(home.system_id)

    @property
    def current_home(self) -> Home:
        """Use the latest refreshed Home object for live gateway state."""
        return self.coordinator.get_home(self.system_id) or self.home

    @property
    def available(self) -> bool:
        # Keep this diagnostic entity available when the associated system fetch
        # failed, otherwise the useful failure details would be hidden.
        return self.system_id in self.coordinator.system_failures or (
            super().available
            and self.system_id in self.coordinator.system_last_success
        )

    @property
    def is_on(self) -> bool:
        # This entity intentionally represents API fetch health, not the
        # gateway's physical cloud connectivity. An offline gateway may still
        # have cached system data returned successfully by Vaillant.
        return (
            self.system_id in self.coordinator.system_last_success
            and self.coordinator.is_system_available(self.system_id)
        )

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        online_state = _get_home_online_state(self.current_home)
        return self.coordinator.get_system_diagnostics(self.system_id) | {
            "api_fetch_success": self.is_on,
            "gateway_online_state": online_state,
        }

    @property
    def name_prefix(self) -> str:
        home = self.current_home
        return home.home_name or home.nomenclature or self.system_id

    @property
    def name(self) -> str:
        return f"{self.name_prefix} Gateway API Connection"

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self.system_id}_gateway_api_connection"

    @property
    def device_info(self) -> DeviceInfo:
        home = self.current_home
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.system_id}_home")},
            name=self.name_prefix,
            model=home.nomenclature,
        )


class GatewayOnline(CoordinatorEntity, BinarySensorEntity):
    """Latest gateway ONLINE/OFFLINE state reported by Vaillant homes API."""

    coordinator: SystemCoordinator
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, home: Home, coordinator: SystemCoordinator) -> None:
        super().__init__(coordinator)
        self.home = home
        self.system_id = str(home.system_id)

    @property
    def current_home(self) -> Home:
        return self.coordinator.get_home(self.system_id) or self.home

    @property
    def gateway_online_state(self) -> str | None:
        return _get_home_online_state(self.current_home)

    @property
    def available(self) -> bool:
        # The state is independent from per-system API fetch availability. Keep
        # the last known ONLINE/OFFLINE state visible even when a system call
        # fails; home_state_last_refreshed shows how fresh it is.
        return self.gateway_online_state is not None

    @property
    def is_on(self) -> bool:
        return self.gateway_online_state == "ONLINE"

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        return {
            "system_id": self.system_id,
            "gateway_online_state": self.gateway_online_state,
            "home_state_last_refreshed": self.coordinator.homes_last_refresh,
            "api_fetch_success": (
                self.system_id in self.coordinator.system_last_success
                and self.coordinator.is_system_available(self.system_id)
            ),
        }

    @property
    def name_prefix(self) -> str:
        home = self.current_home
        return home.home_name or home.nomenclature or self.system_id

    @property
    def name(self) -> str:
        return f"{self.name_prefix} Gateway Online"

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self.system_id}_gateway_online"

    @property
    def device_info(self) -> DeviceInfo:
        home = self.current_home
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.system_id}_home")},
            name=self.name_prefix,
            model=home.nomenclature,
        )


class SystemControlEntity(CoordinatorEntity, BinarySensorEntity):
    coordinator: SystemCoordinator

    def __init__(
        self,
        system_index: int,
        coordinator: SystemCoordinator,
    ):
        super().__init__(coordinator)
        self.system_index = system_index

    @property
    def system(self) -> System:
        return self.coordinator.data[self.system_index]

    @property
    def entity_category(self) -> EntityCategory | None:
        return EntityCategory.DIAGNOSTIC

    @property
    def id_infix(self) -> str:
        return f"{self.system.id}_home"

    @property
    def name_prefix(self) -> str:
        return f"{self.system.home.home_name or self.system.home.nomenclature}"

    @property
    def device_info(self) -> DeviceInfo | None:
        return {"identifiers": {(DOMAIN, self.id_infix)}}

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.is_system_available(self.system.id)


class ControlError(SystemControlEntity):
    def __init__(
        self,
        system_index: int,
        coordinator: SystemCoordinator,
    ):
        super().__init__(system_index, coordinator)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        attr = {
            "diagnostic_trouble_codes": self.system.diagnostic_trouble_codes,
        }
        return attr

    @property
    def is_on(self) -> bool | None:
        return self.system.has_diagnostic_trouble_codes

    @property
    def name(self) -> str:
        return f"{self.name_prefix} Hibakódok"

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self.id_infix}_control_error"

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        return BinarySensorDeviceClass.PROBLEM


class ControlOnline(SystemControlEntity):
    """Optional system connection status returned by the dedicated API call."""

    def __init__(
        self,
        system_index: int,
        coordinator: SystemCoordinator,
    ):
        super().__init__(system_index, coordinator)

    @property
    def available(self) -> bool:
        # `System.connected` is None when the optional "Fetch system connection
        # status" setting is disabled or the API did not provide a value. Do
        # not turn None into False/"Disconnected", because that is misleading.
        return super().available and self.system.connected is not None

    @property
    def is_on(self) -> bool:
        return self.system.connected is True

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        return {
            "connection_status_fetched": self.system.connected is not None,
            "connection_status_value": self.system.connected,
        }

    @property
    def name(self) -> str:
        return f"{self.name_prefix} System Connection Status"

    @property
    def unique_id(self) -> str:
        # Keep the existing unique_id so entity registry references and
        # automations are not broken by the clearer friendly name.
        return f"{DOMAIN}_{self.id_infix}_control_online"

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        return BinarySensorDeviceClass.CONNECTIVITY


class FirmwareUpdateRequired(SystemControlEntity):
    def __init__(
        self,
        system_index: int,
        coordinator: SystemCoordinator,
    ):
        super().__init__(system_index, coordinator)

    @property
    def is_on(self) -> bool | None:
        return self.system.home.firmware.get("update_required", None)

    @property
    def name(self) -> str:
        return f"{self.name_prefix} Firmware Update Required"

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self.id_infix}_firmware_update_required"

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        return BinarySensorDeviceClass.UPDATE


class FirmwareUpdateEnabled(SystemControlEntity):
    def __init__(
        self,
        system_index: int,
        coordinator: SystemCoordinator,
    ):
        super().__init__(system_index, coordinator)

    @property
    def is_on(self) -> bool | None:
        return self.system.home.firmware.get("update_enabled", None)

    @property
    def name(self) -> str:
        return f"{self.name_prefix} Firmware Update Enabled"

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self.id_infix}_firmware_update_enabled"


class EebusCapable(SystemControlEntity):
    _attr_icon = "mdi:check-network"

    def __init__(
        self,
        system_index: int,
        coordinator: SystemCoordinator,
    ):
        super().__init__(system_index, coordinator)

    @property
    def is_on(self) -> bool | None:
        return (
            self.system.eebus.get("spine_capable", False)
            if self.system.eebus
            else False
        )

    @property
    def name(self) -> str:
        return f"{self.name_prefix} EEBUS Capable"

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self.id_infix}_eebus_capable"


class EebusEnabled(SystemControlEntity):
    _attr_icon = "mdi:check-network"

    def __init__(
        self,
        system_index: int,
        coordinator: SystemCoordinator,
    ):
        super().__init__(system_index, coordinator)

    @property
    def is_on(self) -> bool | None:
        return (
            self.system.eebus.get("spine_enabled", False)
            if self.system.eebus
            else False
        )

    @property
    def name(self) -> str:
        return f"{self.name_prefix} EEBUS Enabled"

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self.id_infix}_eebus_enabled"


class CircuitIsCoolingAllowed(CircuitEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool | None:
        return self.circuit.is_cooling_allowed

    @property
    def name(self) -> str:
        return f"{self.name_prefix} Cooling Allowed"

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN} {self.id_infix}_cooling_allowed"


class ZoneIsManualCoolingActive(ZoneCoordinatorEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool | None:
        return self.zone.is_manual_cooling_active

    @property
    def name(self) -> str:
        return f"{self.name_prefix} Manual Cooling Active"

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN} {self.id_infix}_manual_cooling_active"


class AmbisenseDeviceLowBattery(AmbisenseDeviceCoordinatorEntity, BinarySensorEntity):
    def __init__(
        self,
        system_index: int,
        room_index: int,
        device: AmbisenseDevice,
        coordinator: SystemCoordinator,
    ) -> None:
        super().__init__(system_index, room_index, device, coordinator)

    @property
    def is_on(self) -> bool | None:
        return self.device.low_bat

    @property
    def unique_id(self) -> str:
        return self.unique_id_fragment + "_low_bat"

    @property
    def name(self) -> str:
        return f"{self.name_prefix} battery low"

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        return BinarySensorDeviceClass.BATTERY


class AmbisenseDeviceUnreachable(AmbisenseDeviceCoordinatorEntity, BinarySensorEntity):
    def __init__(
        self,
        system_index: int,
        room_index: int,
        device: AmbisenseDevice,
        coordinator: SystemCoordinator,
    ) -> None:
        super().__init__(system_index, room_index, device, coordinator)

    @property
    def is_on(self) -> bool | None:
        return not self.device.unreach

    @property
    def unique_id(self) -> str:
        return self.unique_id_fragment + "_unreach"

    @property
    def name(self) -> str:
        return f"{self.name_prefix} reachable"

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        return BinarySensorDeviceClass.CONNECTIVITY
