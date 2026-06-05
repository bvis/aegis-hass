"""Tests for the SpaceControl keyfob parser (HTS SETTINGS_BODY rows → Keyfob).

The `kv` fixtures mirror the byte SHAPE of real captured rows (sub-key set,
field lengths) — what the classifier keys on — but with synthetic text content
(names, account fields, company labels) so no personal data lives in the repo.
"""

from __future__ import annotations

from custom_components.aegis_ajax.api.hts.keyfobs import (
    KEYFOB_SHAPE,
    Keyfob,
    looks_like_keyfob_candidate,
    parse_keyfob,
)

HUB_ID = "002B1A51"


def _kv(hexmap: dict[int, str]) -> dict[int, bytes]:
    return {k: bytes.fromhex(v) for k, v in hexmap.items()}


def _hx(text: str) -> str:
    return text.encode("utf-8").hex()


# --- Keyfob row template (active install reads 0x0b=01) ----------------------

_KEYFOB_TEMPLATE = {
    0x02: _hx("ALICE"),  # name
    0x07: "00000000ffffffff",
    0x08: "00000000ffffffff",
    0x09: "00000000",
    0x0A: "02ef",  # per-device index/handle
    0x0B: "01",
    0x0C: "01",
    0x0D: "01",
    0x0E: "01",
    0x0F: "0000000000000000",
    0x10: "00",
    0x11: "00000000",
    0x13: "00000000000000000000000000000000",
    0x14: "00000000000000000000000000000000",
    0x16: "ffff",
}


def _keyfob_row(name: str, index_hex: str) -> dict[int, bytes]:
    row = dict(_KEYFOB_TEMPLATE)
    row[0x02] = _hx(name)
    row[0x0A] = index_hex
    return _kv(row)


KEYFOBS = {
    "2ACCB91C": ("ALICE", "02ef"),
    "2A70EFF7": ("BOB", "02f0"),
    "2A4B126E": ("T3", "02f1"),
    "2A11F080": ("T4", "02f2"),
    "2AE66FEC": ("T5", "02f3"),
    "2A64AECB": ("T6", "02f4"),
}


# --- Negative rows (same shapes as real markers/members, synthetic content) ---

# User/member row — carries a name (0x01), email (0x02) and phone (0x03).
USER_ROW = _kv(
    {
        0x01: _hx("Home Assistant"),
        0x02: _hx("user@example.com"),
        0x03: _hx("+10000000000"),
        0x09: "01",
        0x0E: _hx("en"),
        0x21: "4cd98b62",
    }
)

# Company/installer marker row — single key, low id.
COMPANY_MARKER = _kv({0x01: _hx("ACME")})

# Electrical device row (WallSwitch family) — distinct sub-keys.
ELECTRICAL_ROW = _kv({0x42: "00000028", 0x43: "00000969", 0x35: "00e6"})


class TestParseKeyfob:
    def test_all_six_keyfobs(self) -> None:
        for dev_id, (name, index_hex) in KEYFOBS.items():
            kv = _keyfob_row(name, index_hex)
            kf = parse_keyfob(dev_id, HUB_ID, kv)
            assert kf is not None, f"{name} not classified"
            assert kf == Keyfob(
                id=dev_id,
                hub_id=HUB_ID,
                name=name,
                index=int(index_hex, 16),
                active=True,
                flags_hex="01:01:01:01",
            )

    def test_shape_matches_fixture(self) -> None:
        assert frozenset(_keyfob_row("ALICE", "02ef")) == KEYFOB_SHAPE

    def test_deactivated_flag_value_flip(self) -> None:
        # Same keyset, 0x0b flipped to 00 → still a keyfob, but active=False.
        row = dict(_KEYFOB_TEMPLATE)
        row[0x0B] = "00"
        kf = parse_keyfob("2ACCB91C", HUB_ID, _kv(row))
        assert kf is not None
        assert kf.active is False
        assert kf.flags_hex == "00:01:01:01"

    def test_user_row_rejected(self) -> None:
        assert parse_keyfob("1B99007F", HUB_ID, USER_ROW) is None

    def test_company_marker_rejected(self) -> None:
        assert parse_keyfob("0000016A", HUB_ID, COMPANY_MARKER) is None

    def test_low_int_marker_rejected_even_with_keyfob_shape(self) -> None:
        # A keyfob-shaped row at a low id is a section marker, not a keyfob.
        kv = _keyfob_row("T3", "0001")
        assert parse_keyfob("00000001", HUB_ID, kv) is None

    def test_electrical_row_rejected(self) -> None:
        assert parse_keyfob("311B058D", HUB_ID, ELECTRICAL_ROW) is None

    def test_non_hex_id_rejected(self) -> None:
        kv = _keyfob_row("T3", "02f1")
        assert parse_keyfob("ZZZZ", HUB_ID, kv) is None

    def test_empty_name_rejected(self) -> None:
        row = dict(_KEYFOB_TEMPLATE)
        row[0x02] = ""
        assert parse_keyfob("2ACCB91C", HUB_ID, _kv(row)) is None


class TestLooksLikeKeyfobCandidate:
    def test_strict_keyfob_is_candidate(self) -> None:
        kv = _keyfob_row("ALICE", "02ef")
        assert looks_like_keyfob_candidate("2ACCB91C", kv) is True

    def test_shape_variant_keyfob_still_candidate(self) -> None:
        # A keyfob whose keyset diverges (e.g. drops 0x16) must still be logged
        # so a deactivated-keyfob capture is not silently dropped.
        row = dict(_KEYFOB_TEMPLATE)
        del row[0x16]
        assert looks_like_keyfob_candidate("2ACCB91C", _kv(row)) is True

    def test_user_row_not_candidate(self) -> None:
        assert looks_like_keyfob_candidate("1B99007F", USER_ROW) is False

    def test_company_marker_not_candidate(self) -> None:
        assert looks_like_keyfob_candidate("0000016A", COMPANY_MARKER) is False

    def test_low_int_not_candidate(self) -> None:
        kv = _keyfob_row("T3", "0001")
        assert looks_like_keyfob_candidate("00000001", kv) is False
