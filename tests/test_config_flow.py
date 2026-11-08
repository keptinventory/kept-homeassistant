"""The config, reauth and options flows."""

from __future__ import annotations

from http import HTTPStatus

import aiohttp
from homeassistant.config_entries import SOURCE_HASSIO, SOURCE_USER
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kept import const
from custom_components.kept.const import (
    CONF_LINK_SECRET,
    CONF_LOCATIONS,
    CONF_SCAN_MINUTES,
    DOMAIN,
)

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


# ----- set up from the Kept app (keptinventory/kept#376) -----------------------------------------

APP_URL = "http://a0d7b954-kept:8080"
LINK_SECRET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ"
LINKS = f"{APP_URL}/api/v1/home-assistant/link-requests"
OPENED = fixture("link_opened")
CLAIM = f"{LINKS}/{OPENED['id']}/claim"


def _discovery(**config: object) -> HassioServiceInfo:
    return HassioServiceInfo(
        config={
            "host": "a0d7b954-kept",
            "port": 8080,
            "ssl": False,
            "link_secret": LINK_SECRET,
            "addon": "Kept",
            **config,
        },
        name="Kept",
        slug="a0d7b954_kept",
        uuid="0123456789abcdef0123456789abcdef",
    )


async def _discovered(hass: HomeAssistant, info: HassioServiceInfo | None = None) -> dict:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_HASSIO}, data=info or _discovery()
    )


async def _wait(hass: HomeAssistant, flow_id: str) -> dict:
    """Let the waiting step's task finish, then take the flow's next step."""
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(flow_id)


@pytest.fixture
def fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """No two seconds between asks."""
    monkeypatch.setattr(const, "LINK_POLL_SECONDS", 0)


async def test_discovered_app_links_without_a_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(LINKS, json=OPENED, status=HTTPStatus.CREATED)
    aioclient_mock.post(CLAIM, json=fixture("link_approved"))
    mock_kept(aioclient_mock, APP_URL)

    result = await _discovered(hass)
    # Asks first: a discovery never sets anything up on its own.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "hassio_confirm"
    assert result["description_placeholders"] == {"addon": "Kept"}
    assert not [c for c in aioclient_mock.mock_calls if c[1].path.startswith("/api/v1/home")]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "link"
    assert result["description_placeholders"] == {"code": OPENED["code"]}

    result = await _wait(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["count"] == "4"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kept"
    assert result["data"] == {
        CONF_URL: APP_URL,
        CONF_API_TOKEN: TOKEN,
        CONF_VERIFY_SSL: True,
        CONF_LINK_SECRET: LINK_SECRET,
    }
    assert result["result"].unique_id == APP_URL
    # The link secret went in the ask, the claim key in the claim, and no token in either.
    asks = [(url, data, headers) for _, url, data, headers in aioclient_mock.mock_calls]
    opened = next(a for a in asks if str(a[0]) == LINKS)
    assert opened[1] == {"linkSecret": LINK_SECRET}
    assert "Authorization" not in opened[2]
    claimed = next(a for a in asks if str(a[0]) == CLAIM)
    assert claimed[1] == {"claimKey": OPENED["claimKey"]}
    assert "Authorization" not in claimed[2]


async def test_discovered_app_waits_until_someone_allows_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, fast_poll: None
) -> None:
    answers = [fixture("link_pending"), fixture("link_pending"), fixture("link_approved")]

    async def claim(method: str, url: object, data: object) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(method, url, json=answers.pop(0))

    aioclient_mock.post(LINKS, json=OPENED, status=HTTPStatus.CREATED)
    aioclient_mock.post(CLAIM, side_effect=claim)
    mock_kept(aioclient_mock, APP_URL)
    result = await _discovered(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _wait(hass, result["flow_id"])
    assert result["step_id"] == "confirm"
    assert answers == []


@pytest.mark.parametrize(
    ("claim", "reason"),
    [
        ({"json": fixture("link_declined")}, "link_declined"),
        ({"status": HTTPStatus.NOT_FOUND, "json": fixture("link_ended")}, "link_ended"),
    ],
)
async def test_discovered_app_declined_or_ended(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, claim: dict, reason: str
) -> None:
    aioclient_mock.post(LINKS, json=OPENED, status=HTTPStatus.CREATED)
    aioclient_mock.post(CLAIM, **claim)
    result = await _discovered(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _wait(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_discovered_app_with_a_secret_kept_refuses(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(LINKS, status=HTTPStatus.FORBIDDEN, json=fixture("link_refused"))
    result = await _discovered(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "link_refused"


@pytest.mark.parametrize(
    "config",
    [{"link_secret": None}, {"host": ""}, {"port": "not a port"}, {"port": 0}],
)
async def test_discovered_message_that_isnt_the_kept_apps(
    hass: HomeAssistant, config: dict
) -> None:
    info = _discovery(**config)
    if config.get("link_secret", "") is None:
        del info.config["link_secret"]
    result = await _discovered(hass, info)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_kept_app"


async def test_discovered_app_over_https_skips_the_certificate_check(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    url = "https://a0d7b954-kept:8080"
    aioclient_mock.post(f"{url}/api/v1/home-assistant/link-requests", json=OPENED, status=201)
    aioclient_mock.post(
        f"{url}/api/v1/home-assistant/link-requests/{OPENED['id']}/claim",
        json=fixture("link_approved"),
    )
    mock_kept(aioclient_mock, url)
    result = await _discovered(hass, _discovery(ssl=True))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _wait(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["data"][CONF_URL] == url
    assert result["data"][CONF_VERIFY_SSL] is False


async def test_discovered_again_only_refreshes_the_link_secret(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=APP_URL,
        data={
            CONF_URL: APP_URL,
            CONF_API_TOKEN: TOKEN,
            CONF_VERIFY_SSL: True,
            CONF_LINK_SECRET: "old" * 15,
        },
    )
    entry.add_to_hass(hass)
    result = await _discovered(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_LINK_SECRET] == LINK_SECRET
    assert entry.data[CONF_API_TOKEN] == TOKEN


async def test_reauth_of_an_app_entry_asks_kept_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=APP_URL,
        data={
            CONF_URL: APP_URL,
            CONF_API_TOKEN: "kpt_revokedxx_revokedsecret000000000000000000000",
            CONF_VERIFY_SSL: True,
            CONF_LINK_SECRET: LINK_SECRET,
        },
    )
    entry.add_to_hass(hass)
    aioclient_mock.post(LINKS, json=OPENED, status=HTTPStatus.CREATED)
    aioclient_mock.post(CLAIM, json=fixture("link_approved"))
    mock_kept(aioclient_mock, APP_URL)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_link"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    result = await _wait(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_TOKEN] == TOKEN
    assert entry.data[CONF_LINK_SECRET] == LINK_SECRET
    # The entry reloads with the new token; let it, then stop it.
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
