"""Setup, the coordinator and the sensors, from the recorded responses."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kept.const import DOMAIN

from .conftest import COROLLA, GARAGE, HOME, URL, fixture, setup


def _entity(hass: HomeAssistant, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id, unique_id
    return entity_id


async def test_counts_per_location(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    home = {loc["id"]: loc for loc in fixture("summary")["locations"]}[HOME]["counts"]

    for key, field in (("overdue", "overdue"), ("due", "due"), ("lent_out", "lentOut")):
        state = hass.states.get(_entity(hass, f"{HOME}_{key}"))
        assert state.state == str(home[field])
        assert state.attributes["state_class"] == "measurement"

    # Overdue lists its items, most urgent first, as Kept's agenda orders them.
    overdue = hass.states.get(_entity(hass, f"{HOME}_overdue"))
    titles = [i["title"] for i in overdue.attributes["items"]]
    assert titles[0] == "Clean the AC filters"
    assert len(titles) == home["overdue"]

    # Low stock is null in every recorded location (Consumables off): no sensor.
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{HOME}_low_stock") is None
    # Things and Unplaced exist but start disabled.
    things = registry.async_get(_entity(hass, f"{HOME}_things"))
    assert things.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_location_and_vehicle_devices(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    await setup(hass, entry)
    devices = dr.async_get(hass)
    garage = devices.async_get_device_by_identifier((DOMAIN, GARAGE), entry.entry_id)
    assert garage.name == "Garage"
    assert garage.manufacturer == "Kept"
    assert garage.sw_version == "0.0.0-dev"
    assert garage.configuration_url == URL
    corolla = devices.async_get_device_by_identifier((DOMAIN, COROLLA), entry.entry_id)
    assert corolla.name == "Toyota Corolla"
    assert corolla.via_device_id == garage.id
    assert corolla.serial_number == "4VC7HD"


async def test_vehicle_sensors(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    await setup(hass, entry)
    reading = hass.states.get(_entity(hass, f"{COROLLA}_reading"))
    assert reading.state == "52340.0" or float(reading.state) == 52340
    assert reading.attributes["unit_of_measurement"] == "km"
    assert reading.attributes["device_class"] == "distance"
    assert reading.attributes["state_class"] == "total_increasing"
    assert reading.attributes["meter_id"] == "01a13f53-fa76-7140-afc7-c9431233083a"
    assert reading.attributes["source"] == "manual"

    nxt = hass.states.get(_entity(hass, f"{COROLLA}_next_service"))
    assert nxt.state == "2027-02-10"
    assert nxt.attributes["task"] == "Oil change"
    assert nxt.attributes["estimated"] is True

    at = hass.states.get(_entity(hass, f"{COROLLA}_next_service_at"))
    assert float(at.state) == 60328
    assert at.attributes["unit_of_measurement"] == "km"

    # The generator's meter counts hours.
    hours = [
        s for s in hass.states.async_all("sensor") if s.attributes.get("unit_of_measurement") == "h"
    ]
    assert hours
    assert hours[0].attributes["device_class"] == "duration"

    # A bike with no meter gets only its next-service date (unknown).
    bike = fixture("vehicles")["items"][0]["thing"]["id"]
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{bike}_reading") is None
    assert hass.states.get(_entity(hass, f"{bike}_next_service")).state == "unknown"


async def test_revoked_token_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{URL}/version", json=fixture("version"))
    aioclient_mock.get(
        f"{URL}/api/v1/summary", status=HTTPStatus.UNAUTHORIZED, json=fixture("bad_token")
    )
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_kept_down_retries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{URL}/version", status=HTTPStatus.BAD_GATEWAY, text="Bad gateway")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unavailable_when_a_poll_fails(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    await setup(hass, entry)
    entity_id = _entity(hass, f"{HOME}_overdue")
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    kept.clear_requests()
    kept.get(
        f"{URL}/api/v1/summary",
        status=HTTPStatus.SERVICE_UNAVAILABLE,
        json={
            "error": "Kept can't reach its database right now. Try again in a minute.",
            "code": "database_unavailable",
        },
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
