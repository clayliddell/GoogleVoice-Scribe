from __future__ import annotations

from install_common import compare_versions, quote_if_needed, unquote


def test_compare_versions_handles_equal_newer_and_older():
    assert compare_versions("0.2.0", "0.2") == 0
    assert compare_versions("0.2.1", "0.2.0") == 1
    assert compare_versions("v0.1.9", "0.2.0") == -1


def test_config_quote_round_trip_for_paths_with_spaces():
    value = r"C:\Users\Pew Pew Control\Documents\Google Voice Transcripts"

    assert unquote(quote_if_needed(value)) == value
