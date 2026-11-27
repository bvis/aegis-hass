"""Names in debug dumps are masked whatever alphabet they use (#689)."""

from __future__ import annotations

import pytest

from custom_components.aegis_ajax.redaction import is_text, mask_text_runs


@pytest.mark.parametrize(
    "name", ["João", "Salón", "Garça", "Кухня", "Kitchen", "João\u00a0Silva", "Ana\u200bB"]
)
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


def test_keep_ids_leaves_hex_ids_readable_and_masks_the_rest() -> None:
    data = b"\x0a\x080012ABCD\x12\x07Keypad1\x1a\x0b34600112233"
    out = mask_text_runs(data, keep_ids=True)
    assert out == "0a08<id:0012ABCD>1207<text:7b>1a0b<text:11b>"
    # Off by default: nothing readable.
    assert "0012ABCD" not in mask_text_runs(data)
    # A 32-character id: its length byte is a space, so it joins the run.
    long_id = b"0123456789abcdef0123456789abcdef"
    assert mask_text_runs(b"\x0a " + long_id + b"\x00", keep_ids=True) == (
        f"0a<text:1b><id:{long_id.decode()}>00"
    )


def test_keep_ids_only_trusts_a_whole_string_field() -> None:
    # A phone number followed by a field tag that is a hex letter ("B").
    assert mask_text_runs(b"\x1a\x0b34600112233\x42\x01X", keep_ids=True) == ("1a0b<text:12b>0158")
    # Hex digits inside a longer text are part of that text.
    assert mask_text_runs(b"\x0a\x0fSal\xc3\xb3n 00ab12cd", keep_ids=True) == "0a0f<text:15b>"
    # A short name glued to hex digits: nothing of it comes out, not even as hex.
    assert mask_text_runs(b"\x00Jo0012ABCD\x00", keep_ids=True) == "00<text:10b>00"
