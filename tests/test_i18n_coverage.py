"""Test that all data-i18n keys in index.html exist in both ru and en dictionaries."""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _used_keys():
    html = (ROOT / "index.html").read_text()
    return set(re.findall(r'data-i18n="([^"]+)"', html))


def _lang_keys():
    js = (ROOT / "static" / "i18n.js").read_text()
    # Split on the top-level language keys (2-space indent)
    ru_start = js.index("  ru:")
    en_start = js.index("  en:")
    ru_block = js[ru_start:en_start]
    en_block = js[en_start:]
    key_re = re.compile(r'^\s{4}(\w+)\s*:', re.MULTILINE)
    return set(key_re.findall(ru_block)), set(key_re.findall(en_block))


def test_all_used_keys_in_ru():
    used = _used_keys()
    ru_keys, _ = _lang_keys()
    missing = used - ru_keys
    assert not missing, f"Keys used in index.html but missing from ru: {sorted(missing)}"


def test_all_used_keys_in_en():
    used = _used_keys()
    _, en_keys = _lang_keys()
    missing = used - en_keys
    assert not missing, f"Keys used in index.html but missing from en: {sorted(missing)}"


def test_ru_en_symmetry():
    ru_keys, en_keys = _lang_keys()
    only_ru = ru_keys - en_keys
    only_en = en_keys - ru_keys
    assert not only_ru and not only_en, (
        f"Asymmetry: only in ru={sorted(only_ru)}, only in en={sorted(only_en)}"
    )
