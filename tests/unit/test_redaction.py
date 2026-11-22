"""Names in debug dumps are masked whatever alphabet they use (#689)."""

from __future__ import annotations

import pytest

from custom_components.aegis_ajax.redaction import is_text, mask_text_runs


@pytest.mark.parametrize("name", ["João", "Salón", "Garça", "Кухня", "Kitchen"])
def test_a_name_inside_binary_is_masked_whole(name: str) -> None:
    raw = name.encode()
    out = mask_text_runs(b"\x00\x01" + raw + b"\x02")
    assert out == f"0001<text:{len(raw)}b>02"
    assert raw.hex() not in out
    assert is_text(raw)


def test_binary_and_short_runs_stay_as_hex() -> None:
    assert mask_text_runs(b"\x00\x01\x02") == "000102"
    assert mask_text_runs(b"\x00AB\x00") == "00414200"
    # One accented letter alone is two bytes: below the run length.
    assert mask_text_runs(b"\x00\xc3\xa9\x00") == "00c3a900"
    assert mask_text_runs(b"") == ""


def test_bytes_that_are_not_utf8_are_not_text() -> None:
    # A lead byte without its continuation, a bare continuation, a control char.
    assert mask_text_runs(b"\xc3ABC") == "c3<text:3b>"
    assert mask_text_runs(b"\xa9\xa9\xa9") == "a9a9a9"
    assert mask_text_runs(b"\xc2\x85\xc2\x85") == "c285c285"
    assert not is_text(b"\x00\x01\x02")
    assert not is_text(b"ab")
    assert not is_text(b"abc\x00")
