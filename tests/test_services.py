"""kept.log_reading and kept.complete_schedule."""

from __future__ import annotations

from http import HTTPStatus
import json

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import voluptuous as vol

from custom_components.kept.const import DOMAIN

from .conftest import AC_FILTERS, COROLLA, COROLLA_METER, URL, fixture, setup

READINGS = f"{URL}/api/v1/meters/{COROLLA_METER}/readings"
COMPLETE = f"{URL}/api/v1/schedules/{AC_FILTERS}/complete"


def _posts(mock: AiohttpClientMocker, url: str) -> list[tuple[dict, dict]]:
    return [
        (data, headers)
        for method, u, data, headers in mock.mock_calls
        if method.lower() == "post" and str(u) == url
    ]


async def test_log_reading_by_vehicle(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    kept.post(READINGS, status=HTTPStatus.CREATED, json=fixture("reading_created"))
    await setup(hass, entry)
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, COROLLA), entry.entry_id)

    response = await hass.services.async_call(
        DOMAIN,
        "log_reading",
        {"device_id": device.id, "value": 52400, "taken_at": "2026-10-15T14:37:57+03:00"},
        blocking=True,
        return_response=True,
    )
    assert response == {
        "reading_id": "01a13f5a-cc1a-75c7-970b-4dab4f3e1ae7",
        "value": "52400",
        "taken_at": "2026-10-15T11:37:57.000Z",
        "state": "accepted",
        "reason": None,
    }
    [(body, headers)] = _posts(kept, READINGS)
    assert body == {
        "value": "52400",
        "takenAt": "2026-10-15T14:37:57+03:00",
        "source": "home_assistant",
    }
    assert headers["Authorization"].startswith("Bearer kpt_")


async def test_log_reading_by_meter_id_with_decimals(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    kept.post(READINGS, status=HTTPStatus.CREATED, json=fixture("reading_created"))
    await setup(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "log_reading",
        {"meter_id": COROLLA_METER, "value": "52400.12345"},
        blocking=True,
    )
    [(body, _)] = _posts(kept, READINGS)
    assert body["value"] == "52400.123"
    assert body["takenAt"]  # now, with its offset


async def test_log_reading_shows_kept_refusal(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    kept.post(READINGS, status=HTTPStatus.CONFLICT, json=fixture("reading_backwards"))
    await setup(hass, entry)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "log_reading", {"meter_id": COROLLA_METER, "value": 50000}, blocking=True
        )
    assert err.value.translation_key == "kept_error"
    message = err.value.translation_placeholders["message"]
    assert message.startswith("That conflicts with the current state.")
    assert "Lower than the reading before it (52400 km)" in message


async def test_log_reading_with_a_read_token(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    kept.post(READINGS, status=HTTPStatus.FORBIDDEN, json=fixture("reading_read_token"))
    await setup(hass, entry)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "log_reading", {"meter_id": COROLLA_METER, "value": 1}, blocking=True
        )
    assert "Use a token with write access" in err.value.translation_placeholders["message"]


async def test_log_reading_input_checks(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    await setup(hass, entry)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "log_reading", {"value": 1}, blocking=True)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "log_reading", {"meter_id": COROLLA_METER, "value": -1}, blocking=True
        )
    garage = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "01a13f53-f0ed-70d8-8fae-208718949c7d"), entry.entry_id
    )
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "log_reading", {"device_id": garage.id, "value": 1}, blocking=True
        )
    assert err.value.translation_key == "no_meter"


async def test_complete_schedule(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    kept.post(COMPLETE, json=fixture("complete_done"))
    await setup(hass, entry)
    response = await hass.services.async_call(
        DOMAIN,
        "complete_schedule",
        {"schedule_id": AC_FILTERS.upper(), "done_on": "2026-10-15"},
        blocking=True,
        return_response=True,
    )
    assert response == {
        "schedule_id": AC_FILTERS,
        "service_record_id": "01a13f5a-cca5-7339-a313-80b841b9110e",
        "next_due_on": "2027-01-15",
        "next_due_value": None,
    }
    [(body, headers)] = _posts(kept, COMPLETE)
    assert body == {"servicedOn": "2026-10-15"}
    # The rowVersion the schedules list gave.
    assert headers["If-Match"] == "1"


async def test_complete_schedule_errors(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    kept.post(COMPLETE, status=HTTPStatus.PRECONDITION_FAILED, json=fixture("complete_stale"))
    await setup(hass, entry)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "complete_schedule", {"schedule_id": AC_FILTERS}, blocking=True
        )
    assert "This changed since you opened it." in err.value.translation_placeholders["message"]

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "complete_schedule",
            {"schedule_id": "01a13f54-0000-7000-8000-000000000000"},
            blocking=True,
        )
    assert err.value.translation_key == "schedule_not_found"


def test_fixtures_hold_no_token() -> None:
    for name in ("summary", "vehicles", "agenda", "schedules", "reading_created"):
        assert "kpt_" not in json.dumps(fixture(name))
