"""`strings.json` is the source of truth for every user-facing string.

Home Assistant reads `translations/<lang>.json` at runtime and never
`strings.json`, so the two drifting apart is invisible until something
regenerates the translations from the source — at which point every key the
source has lost is silently deleted from all fourteen languages. That is what
these tests exist to prevent: they compare key sets, in both directions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parents[2] / "custom_components" / "aegis_ajax"
STRINGS = COMPONENT / "strings.json"
TRANSLATIONS = COMPONENT / "translations"
LOCALES = sorted(p.stem for p in TRANSLATIONS.glob("*.json"))


def _keys(data: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in data.items():
        keys.add(prefix + key)
        if isinstance(value, dict):
            keys |= _keys(value, prefix + key + ".")
    return keys


def _load(path: Path) -> set[str]:
    return _keys(json.loads(path.read_text(encoding="utf-8")))


def test_the_expected_locales_are_present() -> None:
    # 14 + Danish (#488). The count is asserted rather than derived so that a
    # locale file vanishing from the directory fails loudly instead of the
    # parametrised tests below quietly having one fewer case to run.
    assert len(LOCALES) == 15, LOCALES


@pytest.mark.parametrize("locale", LOCALES)
def test_locale_has_every_key_of_strings_json(locale: str) -> None:
    """A key in the source but not in a language never reaches that user."""
    missing = sorted(_load(STRINGS) - _load(TRANSLATIONS / f"{locale}.json"))
    assert not missing, f"{locale}.json is missing {len(missing)} key(s): {missing}"


@pytest.mark.parametrize("locale", LOCALES)
def test_locale_has_no_key_absent_from_strings_json(locale: str) -> None:
    """A key in a language but not in the source is one regeneration from death."""
    orphans = sorted(_load(TRANSLATIONS / f"{locale}.json") - _load(STRINGS))
    assert not orphans, (
        f"{locale}.json carries {len(orphans)} key(s) absent from strings.json: {orphans}"
    )
