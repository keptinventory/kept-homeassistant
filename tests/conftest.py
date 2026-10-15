"""Fixtures: a Kept answering with responses recorded from a seeded Kept (the households seed).

The JSON under tests/fixtures was recorded on 2026-10-15 from Kept's e2e harness
(apps/web/e2e/serve.mjs, `--seed --https`) with personal tokens, file URLs blanked.
"""

from __future__ import annotations

import json
from typing import Any

from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, load_fixture
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kept.const import DOMAIN

URL = "https://kept.test"
TOKEN = "kpt_testlookup0_testsecret0000000000000000000000"

HOME = "01a13f53-ef1e-7576-9471-e87ea981362a"
GARAGE = "01a13f53-f0ed-70d8-8fae-208718949c7d"
COROLLA = "01a13f53-fa71-7197-9a56-872c783f5b81"
COROLLA_METER = "01a13f53-fa76-7140-afc7-c9431233083a"
AC_FILTERS = "01a13f54-007c-75cb-9b4a-dc099dd416dc"


def fixture(name: str) -> Any:
    """A recorded response."""
    return json.loads(load_fixture(f"{name}.json"))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/kept."""


def mock_kept(aioclient_mock: AiohttpClientMocker, url: str = URL) -> None:
    """The read calls a poll makes, answered from the recordings."""
    aioclient_mock.get(f"{url}/version", json=fixture("version"))
    aioclient_mock.get(f"{url}/api/v1/summary", json=fixture("summary"))
    aioclient_mock.get(f"{url}/api/v1/vehicles", json=fixture("vehicles"))
    aioclient_mock.get(f"{url}/api/v1/agenda", json=fixture("agenda"))
    aioclient_mock.get(f"{url}/api/v1/schedules", json=fixture("schedules"))


@pytest.fixture
def kept(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """A Kept that answers every read."""
    mock_kept(aioclient_mock)
    return aioclient_mock


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A Kept config entry."""
    e = MockConfigEntry(
        domain=DOMAIN,
        title="Kept (kept.test)",
        unique_id=URL,
        data={CONF_URL: URL, CONF_API_TOKEN: TOKEN, CONF_VERIFY_SSL: True},
    )
    e.add_to_hass(hass)
    return e


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set the entry up and let it settle."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
