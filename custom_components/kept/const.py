"""Constants for the Kept integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "kept"

CONF_LOCATIONS: Final = "locations"
CONF_SCAN_MINUTES: Final = "scan_minutes"

DEFAULT_SCAN_MINUTES: Final = 5
MIN_SCAN_MINUTES: Final = 1
MAX_SCAN_MINUTES: Final = 60

DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=DEFAULT_SCAN_MINUTES)

# The counts GET /api/v1/summary returns per location (Kept D244), in Home's order (D240).
COUNT_KEYS: Final = (
    "overdue",
    "due",
    "expiring",
    "lent_out",
    "uncertain",
    "low_stock",
    "borrowed_in",
    "long_unseen",
    "unplaced",
    "things",
)
# snake_case key here -> the camelCase field in Kept's response.
COUNT_FIELDS: Final = {
    "overdue": "overdue",
    "due": "due",
    "expiring": "expiring",
    "lent_out": "lentOut",
    "uncertain": "uncertain",
    "low_stock": "lowStock",
    "borrowed_in": "borrowedIn",
    "long_unseen": "longUnseen",
    "unplaced": "unplaced",
    "things": "things",
}

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_DEVICE_ID: Final = "device_id"
ATTR_METER_ID: Final = "meter_id"
ATTR_VALUE: Final = "value"
ATTR_TAKEN_AT: Final = "taken_at"
ATTR_SCHEDULE_ID: Final = "schedule_id"
ATTR_DONE_ON: Final = "done_on"

SERVICE_LOG_READING: Final = "log_reading"
SERVICE_COMPLETE_SCHEDULE: Final = "complete_schedule"
