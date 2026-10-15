"""The calendar: dated agenda items as all-day events."""

from __future__ import annotations

from datetime import date
from http import HTTPStatus

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kept.calendar import to_event
from custom_components.kept.const import DOMAIN

from .conftest import HOME, URL, fixture, mock_kept, setup


def _calendar(hass: HomeAssistant) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("calendar", DOMAIN, f"{HOME}_agenda")
    assert entity_id
    return entity_id


def test_to_event() -> None:
    item = fixture("agenda")["items"][0]
    event = to_event(item)
    assert event is not None
    assert event.start == date(2026, 10, 7)
    assert event.end == date(2026, 10, 8)
    assert event.summary == "Clean the AC filters · Living room"
    assert event.uid == item["key"]
    assert event.description.startswith("overdue · schedule")
    # Due only at a reading: no day, no event.
    assert to_event({**item, "dueOn": None, "dueValue": "60000"}) is None


@pytest.mark.freeze_time("2026-10-15 12:00:00+00:00")
async def test_next_event_and_range(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    hass_client,
) -> None:
    # The range first: a mock without a query would answer it too.
    aioclient_mock.get(
        f"{URL}/api/v1/agenda",
        params={"locationId": HOME, "from": "2026-10-01", "to": "2026-12-31"},
        json=fixture("agenda_range"),
    )
    mock_kept(aioclient_mock)
    await setup(hass, entry)
    state = hass.states.get(_calendar(hass))
    # The next item from today on (the overdue ones are behind it).
    upcoming = sorted(
        i["dueOn"]
        for i in fixture("agenda")["items"]
        if i["locationId"] == HOME and i["dueOn"] and i["dueOn"] >= "2026-10-15"
    )
    assert state.attributes["start_time"].startswith(upcoming[0])

    client = await hass_client()
    resp = await client.get(
        f"/api/calendars/{_calendar(hass)}?start=2026-10-01T12:00:00Z&end=2026-12-31T12:00:00Z"
    )
    assert resp.status == HTTPStatus.OK
    events = await resp.json()
    expected = [i for i in fixture("agenda_range")["items"] if i["dueOn"]]
    assert len(events) == len(expected)
    assert [e["start"]["date"] for e in events] == sorted(i["dueOn"] for i in expected)
