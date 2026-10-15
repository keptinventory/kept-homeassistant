"""Sensors: each location's counts, and each vehicle's reading and next service."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import COUNT_FIELDS, COUNT_KEYS
from .coordinator import KeptConfigEntry, KeptCoordinator
from .entity import KeptLocationEntity, KeptVehicleEntity

PARALLEL_UPDATES = 0

# How many of a state's items a count sensor lists in its `items` attribute.
ITEMS_SHOWN = 10
# The counts whose items the agenda lists (its states).
AGENDA_STATES = {"overdue", "due", "expiring"}

# A meter's unit in Kept → Home Assistant's unit and device class. Anything else is shown as is.
UNITS: dict[str, tuple[str, SensorDeviceClass]] = {
    "km": (UnitOfLength.KILOMETERS, SensorDeviceClass.DISTANCE),
    "mi": (UnitOfLength.MILES, SensorDeviceClass.DISTANCE),
    "h": (UnitOfTime.HOURS, SensorDeviceClass.DURATION),
}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KeptConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors for what the first poll found; new locations and vehicles need a reload."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for location in coordinator.data.locations.values():
        counts = location.get("counts", {})
        entities.extend(
            KeptCountSensor(coordinator, location, key)
            for key in COUNT_KEYS
            # A null count is a module switched off in that location: no sensor.
            if counts.get(COUNT_FIELDS[key]) is not None
        )
    for row in coordinator.data.vehicles.values():
        if row.get("meter"):
            entities.append(KeptReadingSensor(coordinator, row))
            entities.append(KeptNextServiceValueSensor(coordinator, row))
        entities.append(KeptNextServiceDateSensor(coordinator, row))
    async_add_entities(entities)


class KeptCountSensor(KeptLocationEntity, SensorEntity):
    """One of Home's counts for one location (overdue, due, expiring, lent out, …)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = None

    def __init__(self, coordinator: KeptCoordinator, location: dict[str, Any], key: str) -> None:
        """Bind to one count of one location."""
        super().__init__(coordinator, location)
        self.key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{location['id']}_{key}"
        # The everyday six are on; the rest start disabled.
        self._attr_entity_registry_enabled_default = key not in {"things", "unplaced"}

    @property
    def native_value(self) -> int | None:
        """The count, or None when its module is off now."""
        loc = self.location
        return None if loc is None else loc["counts"].get(COUNT_FIELDS[self.key])

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """For overdue, due and expiring: the first items, most urgent first."""
        if self.key not in AGENDA_STATES:
            return None
        items = [
            i
            for i in self.coordinator.data.agenda.get(self.location_id, [])
            if i.get("state") == self.key
        ]
        return {
            "items": [
                {
                    "title": i.get("title"),
                    "subject": (i.get("subject") or {}).get("name"),
                    "type": i.get("sourceType"),
                    "due_on": i.get("dueOn"),
                    "due_value": i.get("dueValue"),
                    "unit": i.get("unit"),
                }
                for i in items[:ITEMS_SHOWN]
            ]
        }


def _meter_unit(row: dict[str, Any]) -> tuple[str | None, SensorDeviceClass | None]:
    unit = (row.get("meter") or {}).get("unit")
    if unit in UNITS:
        return UNITS[unit]
    return unit, None


class KeptReadingSensor(KeptVehicleEntity, SensorEntity):
    """The vehicle's latest meter reading (an odometer, engine hours)."""

    _attr_translation_key = "reading"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: KeptCoordinator, row: dict[str, Any]) -> None:
        """Bind to the vehicle's meter."""
        super().__init__(coordinator, row)
        self._attr_unique_id = f"{self.thing_id}_reading"
        unit, device_class = _meter_unit(row)
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class

    @property
    def native_value(self) -> float | None:
        """The latest reading's value."""
        latest = ((self.row or {}).get("meter") or {}).get("latest")
        return _number(latest.get("value")) if latest else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """When it was taken, how it arrived, and the meter's id (for kept.log_reading)."""
        meter = (self.row or {}).get("meter") or {}
        latest = meter.get("latest") or {}
        return {
            "meter_id": meter.get("id"),
            "taken_at": latest.get("takenAt"),
            "source": latest.get("source"),
        }


def _next_due(row: dict[str, Any] | None) -> dict[str, Any]:
    return (row or {}).get("nextDue") or {}


class KeptNextServiceDateSensor(KeptVehicleEntity, SensorEntity):
    """The day the next service is due (or Kept's estimate of it from the readings)."""

    _attr_translation_key = "next_service"
    _attr_device_class = SensorDeviceClass.DATE

    def __init__(self, coordinator: KeptCoordinator, row: dict[str, Any]) -> None:
        """Bind to the vehicle's next due schedule."""
        super().__init__(coordinator, row)
        self._attr_unique_id = f"{self.thing_id}_next_service"

    @property
    def native_value(self) -> date | None:
        """The due day; unknown when only a reading is due or nothing is scheduled."""
        due_on = _next_due(self.row).get("dueOn")
        try:
            return date.fromisoformat(due_on) if due_on else None
        except ValueError:
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The task, its state, and whether the day is estimated from the readings."""
        nxt = _next_due(self.row)
        return {
            "task": nxt.get("name"),
            "state": nxt.get("state"),
            "estimated": nxt.get("estimated"),
            "due_value": _number(nxt.get("dueValue")),
        }


class KeptNextServiceValueSensor(KeptVehicleEntity, SensorEntity):
    """The meter reading the next service is due at."""

    _attr_translation_key = "next_service_at"

    def __init__(self, coordinator: KeptCoordinator, row: dict[str, Any]) -> None:
        """Bind to the vehicle's next due schedule."""
        super().__init__(coordinator, row)
        self._attr_unique_id = f"{self.thing_id}_next_service_at"
        unit, device_class = _meter_unit(row)
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class

    @property
    def native_value(self) -> float | None:
        """The due reading; unknown for a schedule by date only."""
        return _number(_next_due(self.row).get("dueValue"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The task and its state."""
        nxt = _next_due(self.row)
        return {"task": nxt.get("name"), "state": nxt.get("state")}
