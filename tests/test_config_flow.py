"""The config, reauth and options flows."""

from __future__ import annotations

from http import HTTPStatus

import aiohttp
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kept.const import CONF_LOCATIONS, CONF_SCAN_MINUTES, DOMAIN

from .conftest import GARAGE, HOME, TOKEN, URL, fixture, mock_kept, setup

USER_INPUT = {CONF_URL: f"{URL}/", CONF_API_TOKEN: f" {TOKEN} ", CONF_VERIFY_SSL: True}


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_user_flow_shows_the_server_then_creates(
    hass: HomeAssistant, kept: AiohttpClientMocker
) -> None:
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    placeholders = result["description_placeholders"]
    assert placeholders["version"] == "0.0.0-dev"
    assert placeholders["host"] == "kept.test"
    assert placeholders["count"] == "4"
    assert "Garage" in placeholders["locations"]

    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kept (kept.test)"
    # The trailing slash and the spaces around the token are gone.
    assert result["data"] == {CONF_URL: URL, CONF_API_TOKEN: TOKEN, CONF_VERIFY_SSL: True}
    assert result["result"].unique_id == URL
    # The token went to Kept as a bearer token.
    sent = [headers for _, url, _, headers in kept.mock_calls if url.path == "/api/v1/summary"]
    assert sent[0]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    ("mocks", "user_input", "error"),
    [
        ({}, {CONF_URL: "kept.test", CONF_API_TOKEN: TOKEN}, "invalid_url"),
        ({}, {CONF_URL: URL, CONF_API_TOKEN: "abc"}, "invalid_token_format"),
        ({"version": {"exc": aiohttp.ClientConnectionError()}}, None, "cannot_connect"),
        ({"version": {"text": "<html>router</html>"}}, None, "not_kept"),
        ({"version": {"json": {"status": "ok"}}}, None, "not_kept"),
        (
            {"summary": {"status": HTTPStatus.UNAUTHORIZED, "json": fixture("bad_token")}},
            None,
            "invalid_auth",
        ),
        (
            # A Kept from before D244: the route isn't there, so the token is refused it.
            {"summary": {"status": HTTPStatus.FORBIDDEN, "json": fixture("home_scope")}},
            None,
            "kept_too_old",
        ),
        (
            {"version": {"status": HTTPStatus.MOVED_PERMANENTLY, "headers": {"Location": URL}}},
            {CONF_URL: "http://kept.test", CONF_API_TOKEN: TOKEN},
            "use_https",
        ),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mocks: dict,
    user_input: dict | None,
    error: str,
) -> None:
    user_input = user_input or USER_INPUT
    base = user_input[CONF_URL].rstrip("/")
    for name, kwargs in mocks.items():
        path = "/version" if name == "version" else f"/api/v1/{name}"
        aioclient_mock.get(f"{base}{path}", **kwargs)
    if base.startswith("https://kept.test"):
        mock_kept(aioclient_mock)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # Fixed, the same flow goes on.
    aioclient_mock.clear_requests()
    mock_kept(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["step_id"] == "confirm"


async def test_ssl_errors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    connection_key = aiohttp.client_reqrep.ConnectionKey(
        "kept.test", 443, True, True, None, None, None
    )
    for exc, error in (
        (
            aiohttp.ClientConnectorCertificateError(connection_key, Exception("self-signed")),
            "ssl_certificate",
        ),
        (aiohttp.ClientSSLError(connection_key, OSError("wrong version number")), "ssl_error"),
    ):
        aioclient_mock.clear_requests()
        aioclient_mock.get(f"{URL}/version", exc=exc)
        flow_id = await _start(hass)
        result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
        assert result["errors"] == {"base": error}


async def test_same_server_twice_aborts(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    await setup(hass, entry)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "nope"}
    )
    assert result["errors"] == {"base": "invalid_token_format"}

    new = "kpt_newlookup00_newsecret00000000000000000000000"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: new}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_TOKEN] == new


async def test_options(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    await setup(hass, entry)
    assert hass.states.get("sensor.home_overdue") is not None
    assert hass.states.get("sensor.garage_due") is not None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LOCATIONS: [GARAGE], CONF_SCAN_MINUTES: 15}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_LOCATIONS: [GARAGE], CONF_SCAN_MINUTES: 15}
    await hass.async_block_till_done()

    # Reloaded with the Garage only.
    coordinator = entry.runtime_data
    assert set(coordinator.data.locations) == {GARAGE}
    assert coordinator.update_interval.total_seconds() == 15 * 60
    assert HOME not in coordinator.data.agenda


async def test_options_all_locations_is_stored_as_all(
    hass: HomeAssistant, kept: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    await setup(hass, entry)
    every = [loc["id"] for loc in fixture("summary")["locations"]]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LOCATIONS: every, CONF_SCAN_MINUTES: 5}
    )
    assert entry.options[CONF_LOCATIONS] == []
