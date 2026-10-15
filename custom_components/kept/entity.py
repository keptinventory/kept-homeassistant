"""Devices: one per Kept location, and one per vehicle inside it."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KeptCoordinator


def location_device(coordinator: KeptCoordinator, location: dict[str, Any]) -> DeviceInfo:
    """A Kept location (a home, a garage, an office) as a device."""
    return DeviceInfo(
        identifiers={(DOMAIN, location["id"])},
        name=location["name"],
        manufacturer="Kept",
        model=str(location.get("kind", "location")).capitalize(),
        sw_version=coordinator.version,
        configuration_url=coordinator.client.url,
    )


def vehicle_device(coordinator: KeptCoordinator, row: dict[str, Any]) -> DeviceInfo:
    """A vehicle as a device under its location's (registered at setup, __init__.py)."""
    thing = row["thing"]
    info = DeviceInfo(
        identifiers={(DOMAIN, thing["id"])},
        name=thing["name"],
        manufacturer="Kept",
        model="Vehicle",
        serial_number=thing.get("shortCode"),
        configuration_url=coordinator.client.url,
    )
    parent = dr.async_get(coordinator.hass).async_get_device_by_identifier(
        (DOMAIN, thing["locationId"]), coordinator.config_entry.entry_id
    )
    if parent is not None:
        info["via_device_id"] = parent.id
    return info


class KeptLocationEntity(CoordinatorEntity[KeptCoordinator]):
    """An entity of one location."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KeptCoordinator, location: dict[str, Any]) -> None:
        """Bind to the location."""
        super().__init__(coordinator)
        self.location_id: str = location["id"]
        self._attr_device_info = location_device(coordinator, location)

    @property
    def location(self) -> dict[str, Any] | None:
        """The location in the latest poll."""
        return self.coordinator.data.locations.get(self.location_id)

    @property
    def available(self) -> bool:
        """Unavailable when the location left the token's reach."""
        return super().available and self.location is not None


class KeptVehicleEntity(CoordinatorEntity[KeptCoordinator]):
    """An entity of one vehicle."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KeptCoordinator, row: dict[str, Any]) -> None:
        """Bind to the vehicle."""
        super().__init__(coordinator)
        self.thing_id: str = row["thing"]["id"]
        self._attr_device_info = vehicle_device(coordinator, row)

    @property
    def row(self) -> dict[str, Any] | None:
        """The vehicle's row in the latest poll."""
        return self.coordinator.data.vehicles.get(self.thing_id)

    @property
    def available(self) -> bool:
        """Unavailable when the vehicle is gone from the list."""
        return super().available and self.row is not None
