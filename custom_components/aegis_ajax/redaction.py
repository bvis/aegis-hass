"""Text masking for debug dumps that users paste into public issues.

Device and user names travel as UTF-8 inside otherwise-binary payloads. A
name is not always ASCII ("João", "Salón"), so text here means printable
ASCII *or* a well-formed multi-byte UTF-8 character.
"""

from __future__ import annotations

import re
import unicodedata

# A run this long is treated as text (a name or label) and masked. Shorter
# runs stay as hex: too short to carry a name, and often coincidental.
MIN_TEXT_RUN = 3

# Beyond ASCII only control, unassigned and private-use code points are left
# out: a name may hold a non-breaking space or a zero-width joiner.
_NOT_TEXT = frozenset({"Cc", "Cn", "Co"})
_UTF8_LEADS = ((2, 0xC2, 0xDF), (3, 0xE0, 0xEF), (4, 0xF0, 0xF4))


def _text_char_len(data: bytes, i: int) -> int:
    """Byte length of the printable character starting at `data[i]`, else 0."""
    byte = data[i]
    if 0x20 <= byte <= 0x7E:
        return 1
    for size, low, high in _UTF8_LEADS:
        if low <= byte <= high:
            try:
                char = data[i : i + size].decode("utf-8")
            except UnicodeDecodeError:
                return 0
            return size if unicodedata.category(char) not in _NOT_TEXT else 0
    return 0


def is_text(value: bytes) -> bool:
    """True when the whole value reads as text of at least `MIN_TEXT_RUN` bytes."""
    if len(value) < MIN_TEXT_RUN:
        return False
    i = 0
    while i < len(value):
        size = _text_char_len(value, i)
        if not size:
            return False
        i += size
    return True


# A hardware or account id as Ajax writes it in a payload: hex digits as
# text. One letter is required so a number never passes for an id.
_HEX_ID = re.compile(rb"(?=[0-9]*[A-Fa-f])[0-9A-Fa-f]+")
_MIN_ID_LEN = 8


def _mask_run(data: bytes, start: int, end: int, keep_ids: bool) -> str:
    """Mask the text run `data[start:end]`, leaving a leading hex id readable.

    Hex digits are only an id when they are a string field that opens the run:
    the byte right before the run is their length. That byte is never text
    itself (it would be part of the run), so a digit in a phone number cannot
    pass for a length. Going by the look of the text alone would show a phone
    number that sits next to a printable field tag. Ids of 32 characters or
    more have a printable length byte and stay masked.
    """
    if end - start < MIN_TEXT_RUN:
        return data[start:end].hex()
    if keep_ids and start:
        size = data[start - 1]
        value = data[start : start + size]
        if _MIN_ID_LEN <= size <= end - start and _HEX_ID.fullmatch(value):
            rest = end - start - size
            return f"<id:{value.decode()}>" + (f"<text:{rest}b>" if rest else "")
    return f"<text:{end - start}b>"


def mask_text_runs(data: bytes, *, keep_ids: bool = False) -> str:
    """Hex-encode `data`, replacing each text run with `<text:Nb>`.

    With `keep_ids`, a string field that is a hex id (device, hub, user) stays
    readable as `<id:...>`: it says which device a payload is about, and it is
    not a name. Anything less certain than that is masked.
    """
    out: list[str] = []
    run = 0  # bytes of text collected so far, ending at `i`
    i = 0

    while i < len(data):
        size = _text_char_len(data, i)
        if size:
            run += size
            i += size
            continue
        if run:
            out.append(_mask_run(data, i - run, i, keep_ids))
        run = 0
        out.append(f"{data[i]:02x}")
        i += 1
    if run:
        out.append(_mask_run(data, i - run, i, keep_ids))
    return "".join(out)
