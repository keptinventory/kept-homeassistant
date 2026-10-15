"""Kept for Home Assistant: what needs you, each vehicle's next service, and readings sent back."""

from __future__ import annotations

from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import KeptAuthError, KeptClient, KeptError
from .const import DOMAIN
from .coordinator import KeptConfigEntry, KeptCoordinator
from .entity import location_device
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once, for every Kept entry."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: KeptConfigEntry) -> bool:
    """Connect to Kept and start polling."""
    session = async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True))
    client = KeptClient(session, entry.data[CONF_URL], entry.data[CONF_API_TOKEN])
    try:
        version = (await client.version())["version"]
    except KeptAuthError as err:
        raise ConfigEntryAuthFailed(err.text) from err
    except KeptError as err:
        raise ConfigEntryNotReady(f"Kept can't be reached: {err}") from err

    coordinator = KeptCoordinator(hass, entry, client, version)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    # The locations first, so each vehicle can name its location's device as its parent.
    devices = dr.async_get(hass)
    for location in coordinator.data.locations.values():
        devices.async_get_or_create(
            config_entry_id=entry.entry_id, **location_device(coordinator, location)
        )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KeptConfigEntry) -> bool:
    """Stop polling."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
