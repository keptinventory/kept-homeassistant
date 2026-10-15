"""A calendar per location: Kept's agenda items that have a due day, as all-day events."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import KeptError
from .coordinator import KeptConfigEntry, KeptCoordinator
from .entity import KeptLocationEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KeptConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One calendar per location."""
    coordinator = entry.runtime_data
    async_add_entities(
        KeptCalendar(coordinator, location) for location in coordinator.data.locations.values()
    )


def to_event(item: dict[str, Any]) -> CalendarEvent | None:
    """An agenda item as an all-day event; None for one due only at a meter reading."""
    try:
        day = date.fromisoformat(item["dueOn"])
    except KeyError, TypeError, ValueError:
        return None
    title = item.get("title") or ""
    subject = item.get("subject") or {}
    name = subject.get("name") or ""
    summary = f"{title} · {name}" if name and name != title else (title or name)
    lines = [f"{item.get('state', '')} · {item.get('sourceType', '')}".strip(" ·")]
    if subject.get("path"):
        lines.append(str(subject["path"]))
    if item.get("dueValue"):
        lines.append(f"{item['dueValue']} {item.get('unit') or ''}".strip())
    return CalendarEvent(
        start=day,
        end=day + timedelta(days=1),
        summary=summary,
        description="\n".join(lines),
        uid=item.get("key"),
    )


class KeptCalendar(KeptLocationEntity, CalendarEntity):
    """What is due in one location: schedules, warranties, documents, loans."""

    _attr_translation_key = "agenda"

    def __init__(self, coordinator: KeptCoordinator, location: dict[str, Any]) -> None:
        """Bind to the location."""
        super().__init__(coordinator, location)
        self._attr_unique_id = f"{location['id']}_agenda"

    @property
    def event(self) -> CalendarEvent | None:
        """Today's or the next dated item from the latest poll."""
        today = dt_util.now().date()
        upcoming = [
            ev
            for ev in map(to_event, self.coordinator.data.agenda.get(self.location_id, []))
            if ev is not None and ev.start >= today
        ]
        return min(upcoming, key=lambda ev: ev.start, default=None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Ask Kept for the items due between the two days."""
        try:
            items = await self.coordinator.client.agenda(
                location_id=self.location_id,
                start=start_date.date().isoformat(),
                end=end_date.date().isoformat(),
            )
        except KeptError as err:
            raise HomeAssistantError(f"Kept can't be reached: {err}") from err
        mine = [i for i in items if i.get("locationId") == self.location_id]
        events = [ev for ev in map(to_event, mine) if ev is not None]
        return sorted(events, key=lambda ev: ev.start)
