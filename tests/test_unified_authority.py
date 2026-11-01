from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polat.agent import new_position_authorized


def test_legacy_challenger_cannot_open_new_positions_when_disabled():
    assert not new_position_authorized({"yetki": {"yeni_pozisyon": False}})


def test_authority_flag_is_explicit_and_fail_closed_by_default():
    # Polat/legacy ajanlar yetki yapılandırması eksikse yeni pozisyon açamaz.
    assert not new_position_authorized({})
    assert new_position_authorized({"yetki": {"yeni_pozisyon": True}})
