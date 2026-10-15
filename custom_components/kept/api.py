"""A small client for Kept's HTTP API, the calls this integration makes.

Every call carries a personal token (`Authorization: Bearer kpt_…`, Settings → Connections in
Kept). Kept answers errors as `{"error", "code", "hint"?}`; `KeptApiError` keeps all three so a
service can show Kept's own words.

Calls (Kept 1.x, D244):
- GET  /version                           public: is it Kept, which version
- GET  /api/v1/summary                    Home's counts per location
- GET  /api/v1/vehicles                   each vehicle's meter, latest reading, next service
- GET  /api/v1/agenda                     dated items (the calendar)
- GET  /api/v1/schedules                  a schedule's rowVersion, for If-Match
- POST /api/v1/meters/{id}/readings       log a reading (write token)
- POST /api/v1/schedules/{id}/complete    mark a schedule done (write token)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from yarl import URL

PAGE_LIMIT = 200
# Lists are cursor-paged; a household never needs more than this many pages of 200.
MAX_PAGES = 10
TIMEOUT = aiohttp.ClientTimeout(total=20)


class KeptError(Exception):
    """Base error."""


class KeptConnectionError(KeptError):
    """Kept could not be reached."""


class KeptCertificateError(KeptConnectionError):
    """The HTTPS certificate was not trusted."""


class KeptSSLError(KeptConnectionError):
    """HTTPS failed, e.g. the server answers plain HTTP on that port."""


class KeptRedirectError(KeptError):
    """The URL redirects (usually http → https); `location` says where."""

    def __init__(self, location: str | None) -> None:
        super().__init__(location or "")
        self.location = location


class KeptNotKeptError(KeptError):
    """Something answered, but not Kept."""


class KeptApiError(KeptError):
    """Kept answered with an error body."""

    def __init__(self, status: int, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(f"{message} ({code})" + (f": {hint}" if hint else ""))
        self.status = status
        self.code = code
        self.message = message
        self.hint = hint

    @property
    def text(self) -> str:
        """Kept's message and hint, as a person reads them."""
        if not self.hint:
            return self.message
        hint = self.hint[0].upper() + self.hint[1:]
        return f"{self.message} {hint}" + ("" if hint.endswith((".", "!", "?")) else ".")


class KeptAuthError(KeptApiError):
    """401: the token was revoked, expired or is not a Kept token."""


class KeptScopeError(KeptApiError):
    """403 `token_scope`: the route is closed to tokens, or needs a read+write token."""


def normalize_url(url: str) -> str:
    """The base URL without a trailing slash, path kept (Kept may sit under a path)."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(url)
    return url.rstrip("/")


def format_value(value: float) -> str:
    """A reading as Kept takes it: a decimal string with at most 3 decimals, no exponent."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


class KeptClient:
    """One Kept server, one token."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self.url = normalize_url(url)
        self._token = token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        auth: bool = True,
    ) -> Any:
        hdrs = {"Accept": "application/json"}
        if auth:
            hdrs["Authorization"] = f"Bearer {self._token}"
        if headers:
            hdrs.update(headers)
        url = URL(self.url + path)
        if params:
            url = url.with_query(params)
        try:
            async with self._session.request(
                method,
                url,
                json=json,
                headers=hdrs,
                timeout=TIMEOUT,
                # A redirect would carry the token to wherever it points; say so instead.
                allow_redirects=False,
            ) as resp:
                if 300 <= resp.status < 400:
                    raise KeptRedirectError(resp.headers.get("Location"))
                try:
                    body = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise KeptNotKeptError(f"{resp.status}: not JSON") from err
                if resp.status >= 400:
                    raise _api_error(resp.status, body)
                return body
        except aiohttp.ClientConnectorCertificateError as err:
            raise KeptCertificateError(str(err)) from err
        except aiohttp.ClientSSLError as err:
            raise KeptSSLError(str(err)) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise KeptConnectionError(str(err)) from err

    async def _pages(self, path: str, params: Mapping[str, str] | None = None) -> list[Any]:
        items: list[Any] = []
        query = {"limit": str(PAGE_LIMIT), **(params or {})}
        for _ in range(MAX_PAGES):
            body = await self._request("GET", path, params=query)
            items.extend(body.get("items", []))
            cursor = body.get("next_cursor")
            if not cursor:
                break
            query = {**query, "cursor": cursor}
        return items

    async def version(self) -> dict[str, Any]:
        """GET /version (no token): `{version, revision, source}`; KeptNotKeptError otherwise."""
        body = await self._request("GET", "/version", auth=False)
        if not isinstance(body, dict) or not isinstance(body.get("version"), str):
            raise KeptNotKeptError("no version")
        return body

    async def summary(self) -> dict[str, Any]:
        """GET /api/v1/summary."""
        body = await self._request("GET", "/api/v1/summary")
        if not isinstance(body, dict) or "locations" not in body:
            raise KeptNotKeptError("no summary")
        return body

    async def vehicles(self) -> list[dict[str, Any]]:
        """GET /api/v1/vehicles, every page."""
        return await self._pages("/api/v1/vehicles")

    async def agenda(
        self,
        *,
        location_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        pages: int = MAX_PAGES,
    ) -> list[dict[str, Any]]:
        """GET /api/v1/agenda: overdue first, then due, expiring, upcoming; by due day."""
        params: dict[str, str] = {"limit": str(PAGE_LIMIT)}
        if location_id:
            params["locationId"] = location_id
        if start:
            params["from"] = start
        if end:
            params["to"] = end
        items: list[dict[str, Any]] = []
        for _ in range(pages):
            body = await self._request("GET", "/api/v1/agenda", params=params)
            items.extend(body.get("items", []))
            cursor = body.get("next_cursor")
            if not cursor:
                break
            params = {**params, "cursor": cursor}
        return items

    async def schedule(self, schedule_id: str) -> dict[str, Any] | None:
        """The schedule with this id from GET /api/v1/schedules, or None."""
        wanted = schedule_id.lower()
        for item in await self._pages("/api/v1/schedules"):
            if str(item.get("id", "")).lower() == wanted:
                return item
        return None

    async def log_reading(self, meter_id: str, value: float, taken_at: str) -> dict[str, Any]:
        """POST /api/v1/meters/{id}/readings, as Home Assistant's reading."""
        return await self._request(
            "POST",
            f"/api/v1/meters/{meter_id}/readings",
            json={
                "value": format_value(value),
                "takenAt": taken_at,
                "source": "home_assistant",
            },
        )

    async def complete_schedule(
        self,
        schedule_id: str,
        row_version: int,
        *,
        done_on: str | None = None,
        value: float | None = None,
        taken_at: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/schedules/{id}/complete with If-Match."""
        body: dict[str, Any] = {}
        if done_on:
            body["servicedOn"] = done_on
        if value is not None:
            reading: dict[str, Any] = {"value": format_value(value)}
            if taken_at:
                reading["takenAt"] = taken_at
            body["reading"] = reading
        return await self._request(
            "POST",
            f"/api/v1/schedules/{schedule_id}/complete",
            json=body,
            headers={"If-Match": str(row_version)},
        )


def _api_error(status: int, body: Any) -> KeptApiError:
    if not isinstance(body, dict) or not isinstance(body.get("code"), str):
        raise KeptNotKeptError(f"{status}: no error code")
    code = body["code"]
    message = str(body.get("error") or code)
    hint = body.get("hint") if isinstance(body.get("hint"), str) else None
    if status == 401:
        return KeptAuthError(status, code, message, hint)
    if status == 403 and code == "token_scope":
        return KeptScopeError(status, code, message, hint)
    return KeptApiError(status, code, message, hint)
