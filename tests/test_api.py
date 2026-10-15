"""The client's small helpers."""

from __future__ import annotations

import pytest

from custom_components.kept.api import KeptApiError, format_value, normalize_url


@pytest.mark.parametrize(
    ("value", "text"),
    [(52400, "52400"), (52400.0, "52400"), (134.5, "134.5"), (1.23456, "1.235"), (0, "0")],
)
def test_format_value(value: float, text: str) -> None:
    assert format_value(value) == text


def test_normalize_url() -> None:
    assert normalize_url(" https://kept.example.org/ ") == "https://kept.example.org"
    assert normalize_url("http://nas.lan:8080/kept/") == "http://nas.lan:8080/kept"
    for bad in ("kept.example.org", "ftp://kept", "https://"):
        with pytest.raises(ValueError, match=bad):
            normalize_url(bad)


def test_error_text_reads_as_kept_wrote_it() -> None:
    assert (
        KeptApiError(409, "conflict", "That conflicts.", "Lower than before (52425 km).").text
        == "That conflicts. Lower than before (52425 km)."
    )
    assert (
        KeptApiError(
            401,
            "token_revoked",
            "This token was revoked or has expired.",
            "create a new token in Settings → Connections",
        ).text
        == "This token was revoked or has expired. Create a new token in Settings → Connections."
    )
    assert KeptApiError(404, "not_found", "Not found.").text == "Not found."
