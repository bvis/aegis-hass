"""Parser for Ajax SpaceControl keyfob rows in the HTS SETTINGS_BODY.

Keyfobs (the physical "llaveros") are HTS-only **on some hubs, not all** — and
this file only handles that class. Where they are HTS-only they never appear in
the gRPC `StreamLightDevices` snapshot that populates `coordinator.devices`, and
arrive instead as device rows inside `SETTINGS_BODY` (HTS sub-key 5) over the
same `on_device_kv` callback used for #123 electrical readings.

**The other class exists and this parser never sees it (#311).** `ObjectType`
carries both `space_control` (25) and `space_control_s` (224), so a hub can
report its keyfob as an ordinary modeled device. @wip3out3r's install does: his
SpaceControl is in the snapshot with a group id, gets a normal HA device and
the bypass switch that already reports deactivation, and its 47-sub-key
SETTINGS_BODY row — carrying every one of `SpaceControl`'s own field numbers
(0x2e, 0x31, 0x33, 0x34, 0x35, 0xc3) — never reaches this module, because
`_on_hts_device_kv` only takes the keyfob branch for rows *absent* from the
snapshot. That is why such an install has a SpaceControl and no keyfob entity;
the shape not matching `KEYFOB_SHAPE` is a consequence, not the cause. Nothing
here should be widened to absorb that row: it is a different family's device
row, and the modeled path already surfaces the device properly. The
coordinator's `_log_hts_space_control_settings` probe is where that class is
observed.

Empirically (live capture, 6 keyfobs) every keyfob row carries the same
sub-key set; only the name (`0x02`) and a per-device index (`0x0a`) differ:

    0x02 = name (UTF-8)                 the keyfob's display name
    0x0a = per-device index/handle      sequential, distinct per keyfob
    0x0b..0x0e = four 1-byte flags      all observed as 0x01
    0x07/0x08  = 00000000ffffffff       constant placeholders
    0x09/0x10/0x11/0x0f/0x13/0x14 = zeros
    0x16 = ffff
    0x17 = 0000                         firmware 2.42.120 and later only

`KEYFOB_SHAPE` is the set a row must CONTAIN, not equal (#504): `0x17` appeared
with a hub firmware update and, while the match was an equality, it dropped
every keyfob on that hub — six entities that had been reporting since June went
`unavailable`, and the only trace was the DEBUG candidate line below.

**The "active" flag is EXPERIMENTAL/unverified.** Every observed keyfob reads
`0x0b == 0x01`; we have no `inactive` sample. Only a CRA admin can deactivate a
keyfob server-side — the app's *forced deactivation* toggle is a different
mechanism, measured on hardware: it moves `0xb6`/`0xb7` and reports
`temporary_deactivation_whole`, leaving `0x0b` untouched (#311). We assume
`0x0b == 0x01` means "active" and expose it as an experimental diagnostic, while
DEBUG-logging the full row (`looks_like_keyfob_candidate`) so a user who *does*
have a deactivated keyfob can paste their log and let us confirm the real flag.
"""

from __future__ import annotations

import dataclasses

from custom_components.aegis_ajax.api.hts.hub_state import _bool_val, _int_be_val, _str_val

# The sub-keys every captured keyfob row carries. A row must CONTAIN all of
# them (#504) — later firmwares add their own, e.g. `0x17`. No other device row,
# user/member row, or section marker observed in SETTINGS_BODY carries them all.
KEYFOB_SHAPE: frozenset[int] = frozenset(
    {0x02, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x13, 0x14, 0x16}
)

# Sub-keys, all single-byte. `0x02` name, `0x0a` index, `0x0b..0x0e` flags.
KEYFOB_SUBKEY_NAME = 0x02
KEYFOB_SUBKEY_INDEX = 0x0A
# Assumed-experimental "active" byte (see module docstring). The 0x0b..0x0e
# quartet is more likely the four button-function flags; 0x0b is treated as the
# activation indicator until a deactivated-keyfob capture says otherwise.
KEYFOB_ACTIVE_SUBKEY = 0x0B
KEYFOB_FLAG_SUBKEYS = (0x0B, 0x0C, 0x0D, 0x0E)

# User/member rows carry both a name (0x01) and a phone (0x03); keyfob rows
# never do. Used to exclude members from the debug-candidate predicate.
_USER_ROW_MARKER_SUBKEYS = frozenset({0x01, 0x03})

# Section/company marker rows use low integer ids (e.g. 0x00000001, 0x0000016A);
# real device ids are well above this. Excludes those from both predicates.
_MIN_DEVICE_ID = 0x10000


@dataclasses.dataclass(frozen=True)
class Keyfob:
    """Immutable snapshot of a single Ajax SpaceControl keyfob.

    `active` is EXPERIMENTAL — derived from the unverified `0x0b` flag (see
    module docstring). `flags_hex` preserves the raw `0x0b..0x0e` quartet so a
    user diagnostic / DEBUG log can later confirm which byte truly encodes the
    CRA-controlled activation state.
    """

    id: str
    hub_id: str
    name: str
    index: int | None
    active: bool
    flags_hex: str


def _flags_hex(kv: dict[int, bytes]) -> str:
    """Render the raw `0x0b..0x0e` flag quartet as a stable hex string."""
    return ":".join(kv.get(sk, b"").hex() for sk in KEYFOB_FLAG_SUBKEYS)


def parse_keyfob(device_id_hex: str, hub_id: str, kv: dict[int, bytes]) -> Keyfob | None:
    """Return a `Keyfob` iff *kv* is a keyfob row, else `None`.

    Classification is install-independent and relies on the known sub-key set
    plus a printable name — no dependence on the device-id prefix (which was
    coincidental). The caller (`coordinator._on_hts_device_kv`) only reaches
    this for rows absent from the gRPC device snapshot, so modeled devices and
    the hub are already excluded; this adds the user-row and marker guards.

    **The match is a superset, not an equality (#504).** Every key in
    `KEYFOB_SHAPE` must be present; keys beyond it are ignored. It was an
    equality until a hub firmware update (`2.42.120`) added a sixteenth
    sub-key, `0x17`, to the row — and every keyfob on that install stopped
    being recognised at once, taking its entity with it. Requiring the known
    keys keeps the discriminating power (no other observed row in a
    SETTINGS_BODY carries all of them) while a field the vendor adds costs
    nothing. Unknown keys are never read: each value below comes from a named
    sub-key.

    The rule is deliberately one-directional. A row that *drops* a known key is
    still rejected, because that is a different shape and worth seeing rather
    than absorbing — `looks_like_keyfob_candidate` catches it for the caller to
    report (coordinator warns once per row and records it in diagnostics).
    """
    if not frozenset(kv) >= KEYFOB_SHAPE:
        return None
    # A member row that happened to carry every keyfob key would otherwise pass
    # now that extra keys are tolerated; the user markers still exclude it.
    if frozenset(kv) >= _USER_ROW_MARKER_SUBKEYS:
        return None
    try:
        if int(device_id_hex, 16) < _MIN_DEVICE_ID:
            return None
    except ValueError:
        return None
    name = _str_val(kv[KEYFOB_SUBKEY_NAME])
    if not name or not name.isprintable():
        return None
    return Keyfob(
        id=device_id_hex,
        hub_id=hub_id,
        name=name,
        index=_int_be_val(kv.get(KEYFOB_SUBKEY_INDEX)),
        active=_bool_val(kv.get(KEYFOB_ACTIVE_SUBKEY, b"")),
        flags_hex=_flags_hex(kv),
    )


def looks_like_keyfob_candidate(device_id_hex: str, kv: dict[int, bytes]) -> bool:
    """Loose predicate for *whether to DEBUG-log* a row as a keyfob candidate.

    Deliberately broader than `parse_keyfob`: it matches a row that has a
    printable name (`0x02`) and an index (`0x0a`), is not a user/member row, and
    is not a low-id marker — even if its sub-key set differs from the strict
    shape. That way a CRA-deactivated keyfob whose bytes diverge from every
    observed (active) sample still surfaces in logs for diffing, instead of being
    silently dropped.
    """
    keys = frozenset(kv)
    if KEYFOB_SUBKEY_NAME not in keys or KEYFOB_SUBKEY_INDEX not in keys:
        return False
    if keys >= _USER_ROW_MARKER_SUBKEYS:
        return False
    try:
        if int(device_id_hex, 16) < _MIN_DEVICE_ID:
            return False
    except ValueError:
        return False
    name = _str_val(kv[KEYFOB_SUBKEY_NAME])
    return bool(name) and name.isprintable()
