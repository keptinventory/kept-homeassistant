"""Set up Kept from the UI: the server's address and a personal token, or from the Kept app.

With the Kept app in the same Home Assistant, the app sends a Supervisor discovery message
(keptinventory/kept#376): Kept's name on Home Assistant's internal network, its port, whether it
serves https there, and the app's link secret. The flow then asks Kept for a token with that
secret; Kept shows the request, with a code, to the people signed in to it, and someone allows it
with the locations and access they choose. Kept hands the token over once, to this flow only (the
claim key from the request), and it is stored like a token typed in by hand.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import time
from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import (
    SOURCE_REAUTH,
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
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
import voluptuous as vol

from . import const
from .api import (
    KeptApiError,
    KeptAuthError,
    KeptCertificateError,
    KeptClient,
    KeptConnectionError,
    KeptError,
    KeptNotKeptError,
    KeptRedirectError,
    KeptScopeError,
    KeptSSLError,
    normalize_url,
)
from .const import (
    CONF_LINK_SECRET,
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


class LinkEndedError(Exception):
    """Kept no longer has the request: it ran out of time, or Kept restarted."""


class LinkDeclinedError(Exception):
    """Someone in Kept declined the request."""


class KeptConfigFlow(ConfigFlow, domain=DOMAIN):
    """Kept's config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Hold what the first step found until it is confirmed."""
        self._input: dict[str, Any] = {}
        self._version = ""
        self._summary: dict[str, Any] = {}
        # From the Kept app's discovery message, or the entry being re-linked.
        self._link: dict[str, Any] = {}
        self._addon = ""
        self._request: dict[str, Any] = {}
        self._link_task: asyncio.Task[tuple[str, str, dict[str, Any]]] | None = None
        self._link_error = ""

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
            # From the app: its internal host name means nothing to a person.
            title = "Kept" if self._addon else f"Kept ({host})"
            return self.async_create_entry(title=title, data=self._input)
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

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """The Kept app in this Home Assistant offers itself: confirm before asking Kept."""
        config = discovery_info.config
        try:
            host = str(config["host"]).strip()
            port = int(config["port"])
            secret = str(config["link_secret"])
        except KeyError, TypeError, ValueError:
            return self.async_abort(reason="not_kept_app")
        if not host or not 0 < port < 65536 or not secret:
            return self.async_abort(reason="not_kept_app")
        ssl = config.get("ssl") is True
        url = f"{'https' if ssl else 'http'}://{host}:{port}"
        await self.async_set_unique_id(url.lower())
        # Already set up from the app: keep its link secret current, nothing else.
        self._abort_if_unique_id_configured(updates={CONF_LINK_SECRET: secret})
        # https on the internal network: the certificate names Kept's own address, not this host.
        self._link = {CONF_URL: url, CONF_LINK_SECRET: secret, CONF_VERIFY_SSL: not ssl}
        self._addon = discovery_info.name
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask Kept to link, then wait for someone in Kept to allow it."""
        if user_input is None:
            return self.async_show_form(
                step_id="hassio_confirm", description_placeholders={"addon": self._addon}
            )
        return await self._open_link()

    async def _open_link(self) -> ConfigFlowResult:
        """POST the link request; then the waiting step."""
        try:
            self._request = await self._link_client().open_link(self._link[CONF_LINK_SECRET])
        except KeptApiError as err:
            if err.status == 403:
                return self.async_abort(reason="link_refused")
            return self.async_abort(
                reason="kept_error", description_placeholders={"message": err.text}
            )
        except KeptConnectionError, KeptNotKeptError, KeptRedirectError:
            return self.async_abort(reason="cannot_connect")
        return await self.async_step_link()

    def _link_client(self, token: str = "") -> KeptClient:
        session = async_get_clientsession(self.hass, verify_ssl=self._link[CONF_VERIFY_SSL])
        return KeptClient(session, self._link[CONF_URL], token)

    async def _wait_for_token(self) -> tuple[str, str, dict[str, Any]]:
        """The token once someone in Kept allows the request, with Kept's version and summary."""
        client = self._link_client()
        deadline = time.monotonic() + const.LINK_WAIT_SECONDS
        while time.monotonic() < deadline:
            try:
                answer = await client.claim_link(self._request["id"], self._request["claimKey"])
            except KeptApiError as err:
                if err.status == 404:
                    raise LinkEndedError from err
                raise
            except KeptConnectionError:
                # Kept restarting: its next answer says whether the request survived.
                answer = {"state": "pending"}
            if answer["state"] == "declined":
                raise LinkDeclinedError
            if answer["state"] == "approved" and isinstance(answer.get("token"), str):
                token = answer["token"]
                try:
                    version, summary = await validate(
                        self.hass, self._link[CONF_URL], token, self._link[CONF_VERIFY_SSL]
                    )
                except CannotConnect as err:
                    raise KeptConnectionError(err.key) from err
                return token, version, summary
            await asyncio.sleep(const.LINK_POLL_SECONDS)
        raise LinkEndedError

    async def async_step_link(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the code and wait while someone in Kept allows the request."""
        if self._link_task is None:
            # Not eager: the first answer may already be in, and the code is shown either way.
            self._link_task = self.hass.async_create_task(self._wait_for_token(), eager_start=False)
        if not self._link_task.done():
            return self.async_show_progress(
                step_id="link",
                progress_action="allow_in_kept",
                progress_task=self._link_task,
                description_placeholders={"code": self._request["code"]},
            )
        try:
            token, self._version, self._summary = await self._link_task
        except LinkDeclinedError:
            self._link_error = "link_declined"
        except LinkEndedError:
            self._link_error = "link_ended"
        except KeptError:
            self._link_error = "cannot_connect"
        else:
            self._input = {
                CONF_URL: self._link[CONF_URL],
                CONF_API_TOKEN: token,
                CONF_VERIFY_SSL: self._link[CONF_VERIFY_SSL],
                CONF_LINK_SECRET: self._link[CONF_LINK_SECRET],
            }
            return self.async_show_progress_done(next_step_id="linked")
        finally:
            self._link_task = None
        return self.async_show_progress_done(next_step_id="link_failed")

    async def async_step_link_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Declined, ended, or Kept stopped answering."""
        return self.async_abort(reason=self._link_error)

    async def async_step_linked(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A new entry shows what the token reaches; a re-link swaps the token and reloads."""
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates={CONF_API_TOKEN: self._input[CONF_API_TOKEN]}
            )
        return await self.async_step_confirm()

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The token stopped working: ask Kept again (set up from the app), or for a new one."""
        if entry_data.get(CONF_LINK_SECRET):
            self._link = {
                CONF_URL: entry_data[CONF_URL],
                CONF_LINK_SECRET: entry_data[CONF_LINK_SECRET],
                CONF_VERIFY_SSL: entry_data.get(CONF_VERIFY_SSL, True),
            }
            return await self.async_step_reauth_link()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up from the Kept app: ask Kept for a new token, allowed again in Kept."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_link",
                description_placeholders={"host": urlsplit(self._link[CONF_URL]).netloc},
            )
        return await self._open_link()

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
