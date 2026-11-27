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
# text. One letter is required so a phone number never passes for an id.
_HEX_ID = re.compile(rb"(?=[0-9]*[A-Fa-f])[0-9A-Fa-f]{8,}")


def _mask_run(chunk: bytes, keep_ids: bool) -> str:
    """Mask one text run, leaving the hex ids inside it readable if asked."""
    if len(chunk) < MIN_TEXT_RUN:
        return chunk.hex()
    if not keep_ids:
        return f"<text:{len(chunk)}b>"
    out: list[str] = []
    last = 0
    for match in _HEX_ID.finditer(chunk):
        if match.start() > last:
            out.append(_mask_run(chunk[last : match.start()], False))
        out.append(f"<id:{match.group().decode()}>")
        last = match.end()
    if last < len(chunk):
        out.append(_mask_run(chunk[last:], False))
    return "".join(out)


def mask_text_runs(data: bytes, *, keep_ids: bool = False) -> str:
    """Hex-encode `data`, replacing each text run with `<text:Nb>`.

    With `keep_ids`, device, hub and user ids stay readable as `<id:...>`:
    they say which device a payload is about, and they are not names.
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
            out.append(_mask_run(data[i - run : i], keep_ids))
        run = 0
        out.append(f"{data[i]:02x}")
        i += 1
    if run:
        out.append(_mask_run(data[i - run : i], keep_ids))
    return "".join(out)
