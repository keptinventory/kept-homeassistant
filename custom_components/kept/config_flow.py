"""Set up Kept from the UI: the server's address and a personal token."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    KeptApiError,
    KeptAuthError,
    KeptCertificateError,
    KeptClient,
    KeptConnectionError,
    KeptNotKeptError,
    KeptRedirectError,
    KeptScopeError,
    KeptSSLError,
    normalize_url,
)
from .const import (
    CONF_LOCATIONS,
    CONF_SCAN_MINUTES,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    MAX_SCAN_MINUTES,
    MIN_SCAN_MINUTES,
)
from .coordinator import KeptConfigEntry

TOKEN_PREFIX = "kpt_"

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        vol.Required(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_VERIFY_SSL, default=True): bool,
    }
)
REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class CannotConnect(Exception):
    """Carries the form error key and the placeholders it needs."""

    def __init__(self, key: str, placeholders: dict[str, str] | None = None) -> None:
        super().__init__(key)
        self.key = key
        self.placeholders = placeholders or {}


async def validate(
    hass: HomeAssistant, url: str, token: str, verify_ssl: bool
) -> tuple[str, dict[str, Any]]:
    """Kept's version and summary for this URL and token; CannotConnect says what is wrong."""
    try:
        url = normalize_url(url)
    except ValueError as err:
        raise CannotConnect("invalid_url") from err
    if not token.strip().startswith(TOKEN_PREFIX):
        raise CannotConnect("invalid_token_format")
    client = KeptClient(async_get_clientsession(hass, verify_ssl=verify_ssl), url, token.strip())
    try:
        version = (await client.version())["version"]
        summary = await client.summary()
    except KeptRedirectError as err:
        target = err.location or ""
        if target.startswith("https://") and url.startswith("http://"):
            raise CannotConnect("use_https", {"location": target}) from err
        raise CannotConnect("redirected", {"location": target}) from err
    except KeptCertificateError as err:
        raise CannotConnect("ssl_certificate") from err
    except KeptSSLError as err:
        raise CannotConnect("ssl_error") from err
    except KeptConnectionError as err:
        raise CannotConnect("cannot_connect") from err
    except KeptNotKeptError as err:
        raise CannotConnect("not_kept") from err
    except KeptAuthError as err:
        raise CannotConnect("invalid_auth") from err
    except KeptScopeError as err:
        # GET /api/v1/summary is open to every token from the Kept that has it.
        raise CannotConnect("kept_too_old") from err
    except KeptApiError as err:
        raise CannotConnect("kept_error", {"message": err.text}) from err
    return version, summary


class KeptConfigFlow(ConfigFlow, domain=DOMAIN):
    """Kept's config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Hold what the first step found until it is confirmed."""
        self._input: dict[str, Any] = {}
        self._version = ""
        self._summary: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: KeptConfigEntry) -> KeptOptionsFlow:
        """Which locations, how often."""
        return KeptOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the address and the token, and check both."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"example": "https://kept.example.org"}
        if user_input is not None:
            try:
                self._version, self._summary = await validate(
                    self.hass,
                    user_input[CONF_URL],
                    user_input[CONF_API_TOKEN],
                    user_input.get(CONF_VERIFY_SSL, True),
                )
            except CannotConnect as err:
                errors["base"] = err.key
                placeholders.update(err.placeholders)
            else:
                url = normalize_url(user_input[CONF_URL])
                await self.async_set_unique_id(url.lower())
                self._abort_if_unique_id_configured()
                self._input = {
                    CONF_URL: url,
                    CONF_API_TOKEN: user_input[CONF_API_TOKEN].strip(),
                    CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, True),
                }
                return await self.async_step_confirm()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show which Kept and which locations the token reaches, then add it."""
        host = urlsplit(self._input[CONF_URL]).netloc
        if user_input is not None:
            return self.async_create_entry(title=f"Kept ({host})", data=self._input)
        names = [loc["name"] for loc in self._summary.get("locations", [])]
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "host": host,
                "version": self._version,
                "count": str(len(names)),
                "locations": ", ".join(names) or "-",
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The token stopped working: ask for a new one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check the new token against the same server."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"host": urlsplit(entry.data[CONF_URL]).netloc}
        if user_input is not None:
            try:
                await validate(
                    self.hass,
                    entry.data[CONF_URL],
                    user_input[CONF_API_TOKEN],
                    entry.data.get(CONF_VERIFY_SSL, True),
                )
            except CannotConnect as err:
                errors["base"] = err.key
                placeholders.update(err.placeholders)
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN].strip()}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders=placeholders,
        )


class KeptOptionsFlow(OptionsFlowWithReload):
    """Which of the token's locations to show, and the poll interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """One form."""
        coordinator = self.config_entry.runtime_data
        try:
            summary = await coordinator.client.summary()
            locations = summary.get("locations", [])
        except KeptApiError, KeptConnectionError, KeptNotKeptError:
            locations = list(coordinator.data.locations.values())
        options = [SelectOptionDict(value=loc["id"], label=loc["name"]) for loc in locations]
        if user_input is not None:
            chosen = list(user_input.get(CONF_LOCATIONS, []))
            # All of them is stored as none chosen, so a location added later shows up too.
            if set(chosen) >= {o["value"] for o in options}:
                chosen = []
            return self.async_create_entry(
                data={
                    CONF_LOCATIONS: chosen,
                    CONF_SCAN_MINUTES: int(user_input[CONF_SCAN_MINUTES]),
                }
            )
        current = self.config_entry.options.get(CONF_LOCATIONS) or [o["value"] for o in options]
        schema = vol.Schema(
            {
                vol.Optional(CONF_LOCATIONS, default=current): SelectSelector(
                    SelectSelectorConfig(options=options, multiple=True)
                ),
                vol.Required(
                    CONF_SCAN_MINUTES,
                    default=self.config_entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_MINUTES,
                        max=MAX_SCAN_MINUTES,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
