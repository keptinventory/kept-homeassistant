"""Polls Kept: the summary, the vehicles and the first page of the agenda."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KeptApiError, KeptAuthError, KeptClient, KeptError, KeptScopeError
from .const import CONF_LOCATIONS, CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES, DOMAIN

_LOGGER = logging.getLogger(__name__)

type KeptConfigEntry = ConfigEntry[KeptCoordinator]


@dataclass
class KeptData:
    """One poll's answer, narrowed to the locations chosen in the options."""

    version: str
    # GET /api/v1/summary's locations, by id.
    locations: dict[str, dict[str, Any]]
    # GET /api/v1/vehicles's rows with a meter or a next service, by thing id.
    vehicles: dict[str, dict[str, Any]]
    # The agenda's first page (overdue, due, expiring, then upcoming), by location id.
    agenda: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class KeptCoordinator(DataUpdateCoordinator[KeptData]):
    """Reads Kept every few minutes (the options' poll interval, 5 by default)."""

    config_entry: KeptConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: KeptConfigEntry, client: KeptClient, version: str
    ) -> None:
        """Set up from the entry's options."""
        minutes = int(entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )
        self.client = client
        self.version = version

    @property
    def chosen(self) -> set[str] | None:
        """The location ids the options keep; None for all the token reaches."""
        chosen = self.config_entry.options.get(CONF_LOCATIONS)
        return set(chosen) if chosen else None

    async def _async_update_data(self) -> KeptData:
        try:
            summary = await self.client.summary()
            vehicles = await self.client.vehicles()
            agenda = await self.client.agenda(pages=1)
        except KeptAuthError as err:
            raise ConfigEntryAuthFailed(err.text) from err
        except KeptScopeError as err:
            # The token works but a route is closed to it: a Kept older than these routes.
            raise UpdateFailed(err.text) from err
        except KeptApiError as err:
            raise UpdateFailed(err.text) from err
        except KeptError as err:
            raise UpdateFailed(f"Kept can't be reached: {err}") from err

        chosen = self.chosen
        locations = {
            loc["id"]: loc
            for loc in summary.get("locations", [])
            if chosen is None or loc["id"] in chosen
        }
        by_vehicle = {
            row["thing"]["id"]: row
            for row in vehicles
            if row["thing"].get("locationId") in locations
        }
        by_location: dict[str, list[dict[str, Any]]] = {loc_id: [] for loc_id in locations}
        for item in agenda:
            if item.get("locationId") in by_location:
                by_location[item["locationId"]].append(item)
        return KeptData(
            version=self.version,
            locations=locations,
            vehicles=by_vehicle,
            agenda=by_location,
        )
