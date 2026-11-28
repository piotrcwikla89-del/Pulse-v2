"""Normalizacja numerów LUB przy dopasowaniu farb do zleceń."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import lub_match_variants, normalize_lub_key


def test_normalize_lub_key_variants():
    assert normalize_lub_key("LUB4475") == "4475"
    assert normalize_lub_key("4475") == "4475"
    assert normalize_lub_key("LUB 4475") == "4475"
    assert normalize_lub_key("lub-4475") == "4475"
    assert normalize_lub_key("") == ""
    assert normalize_lub_key(None) == ""


def test_lub_match_variants_include_common_forms():
    variants = lub_match_variants("LUB4475")
    assert "4475" in variants
    assert "LUB4475" in variants
    assert "LUB 4475" in variants
    assert "LUB-4475" in variants

    hyphenated = lub_match_variants("88")
    assert "LUB-88" in hyphenated
    assert "LUB88" in hyphenated
