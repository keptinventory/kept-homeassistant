"""Actions: send a meter reading to Kept, mark a schedule done."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import KeptApiError, KeptError
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DEVICE_ID,
    ATTR_DONE_ON,
    ATTR_METER_ID,
    ATTR_SCHEDULE_ID,
    ATTR_TAKEN_AT,
    ATTR_VALUE,
    DOMAIN,
    SERVICE_COMPLETE_SCHEDULE,
    SERVICE_LOG_READING,
)
from .coordinator import KeptConfigEntry

Value = vol.All(vol.Coerce(float), vol.Range(min=0, max=99_999_999_999))

LOG_READING_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
            vol.Optional(ATTR_DEVICE_ID): cv.string,
            vol.Optional(ATTR_METER_ID): cv.string,
            vol.Required(ATTR_VALUE): Value,
            vol.Optional(ATTR_TAKEN_AT): cv.datetime,
        }
    ),
    cv.has_at_least_one_key(ATTR_DEVICE_ID, ATTR_METER_ID),
)

COMPLETE_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_SCHEDULE_ID): cv.string,
        vol.Optional(ATTR_DONE_ON): cv.date,
        vol.Optional(ATTR_VALUE): Value,
    }
)


def _loaded(hass: HomeAssistant) -> list[KeptConfigEntry]:
    return [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED
    ]


def _entry(hass: HomeAssistant, call: ServiceCall) -> KeptConfigEntry | None:
    """The entry the call names, or the only one; None when it must be searched."""
    entries = _loaded(hass)
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_entry")
    wanted = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if wanted:
        for e in entries:
            if e.entry_id == wanted:
                return e
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_entry")
    return entries[0] if len(entries) == 1 else None


def _kept_error(err: KeptApiError) -> HomeAssistantError:
    """Kept's own words; a refusal (4xx) is the caller's to fix, anything else is not."""
    cls = ServiceValidationError if 400 <= err.status < 500 else HomeAssistantError
    return cls(
        translation_domain=DOMAIN,
        translation_key="kept_error",
        translation_placeholders={"message": err.text},
    )


def _unreachable(err: KeptError) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="unreachable",
        translation_placeholders={"error": str(err)},
    )


def _meter_of_device(
    hass: HomeAssistant, entries: list[KeptConfigEntry], device_id: str
) -> tuple[KeptConfigEntry, str]:
    """The entry and meter of a Kept vehicle device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is not None:
        thing_ids = {ident for domain, ident in device.identifiers if domain == DOMAIN}
        for entry in entries:
            for thing_id in thing_ids:
                row = entry.runtime_data.data.vehicles.get(thing_id)
                meter = (row or {}).get("meter")
                if meter:
                    return entry, meter["id"]
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_meter")


async def _log_reading(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    entry = _entry(hass, call)
    if ATTR_METER_ID in call.data:
        meter_id = call.data[ATTR_METER_ID]
        if entry is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="several_entries"
            )
    else:
        entry, meter_id = _meter_of_device(
            hass, [entry] if entry else _loaded(hass), call.data[ATTR_DEVICE_ID]
        )
    taken_at = call.data.get(ATTR_TAKEN_AT) or dt_util.now()
    if taken_at.tzinfo is None:
        taken_at = taken_at.replace(tzinfo=dt_util.get_default_time_zone())
    coordinator = entry.runtime_data
    try:
        result = await coordinator.client.log_reading(
            meter_id, call.data[ATTR_VALUE], taken_at.isoformat()
        )
    except KeptApiError as err:
        raise _kept_error(err) from err
    except KeptError as err:
        raise _unreachable(err) from err
    await coordinator.async_request_refresh()
    reading = result.get("reading", {})
    return {
        "reading_id": reading.get("id"),
        "value": reading.get("value"),
        "taken_at": reading.get("takenAt"),
        # `needs_review`: Kept found it out of line (a jump) and put it in the Inbox.
        "state": result.get("state"),
        "reason": result.get("reason"),
    }


async def _complete_schedule(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    entry = _entry(hass, call)
    schedule_id = call.data[ATTR_SCHEDULE_ID]
    found: tuple[KeptConfigEntry, dict[str, Any]] | None = None
    for candidate in [entry] if entry else _loaded(hass):
        try:
            schedule = await candidate.runtime_data.client.schedule(schedule_id)
        except KeptApiError as err:
            raise _kept_error(err) from err
        except KeptError as err:
            raise _unreachable(err) from err
        if schedule is not None:
            found = (candidate, schedule)
            break
    if found is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="schedule_not_found"
        )
    entry, schedule = found
    done_on = call.data.get(ATTR_DONE_ON)
    coordinator = entry.runtime_data
    try:
        result = await coordinator.client.complete_schedule(
            schedule["id"],
            int(schedule["rowVersion"]),
            done_on=done_on.isoformat() if done_on else None,
            value=call.data.get(ATTR_VALUE),
        )
    except KeptApiError as err:
        raise _kept_error(err) from err
    except KeptError as err:
        raise _unreachable(err) from err
    await coordinator.async_request_refresh()
    after = result.get("schedule", {})
    nxt = after.get("next") or {}
    return {
        "schedule_id": after.get("id"),
        "service_record_id": (result.get("serviceRecord") or {}).get("id"),
        "next_due_on": nxt.get("dueOn"),
        "next_due_value": nxt.get("dueValue"),
    }


def async_setup_services(hass: HomeAssistant) -> None:
    """Register kept.log_reading and kept.complete_schedule."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_LOG_READING,
        _log_reading,
        schema=LOG_READING_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_COMPLETE_SCHEDULE,
        _complete_schedule,
        schema=COMPLETE_SCHEDULE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
