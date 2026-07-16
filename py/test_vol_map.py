"""
test_vol_map.py – Unit tests for the OPLL→MS2 volume mapping modes.

Tests cover:
- 'legacy' mode reproduces old mapping exactly for all OPLL values 0..15
- 'db' mode produces values in range 0..63 for all OPLL values 0..15
- 'db' mode is monotonic (non-decreasing)
- Direction consistency: both modes map 0→0 and have the same ordering
- CLI parsing: --vol-map accepts 'legacy' and 'db'; unknown values are rejected
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ms2_writer import opll_vol_to_ms2_vol


# ---------------------------------------------------------------------------
# Expected legacy values (round(v * 8/3) for v in 0..15, capped at 63)
# ---------------------------------------------------------------------------

_LEGACY_EXPECTED = [
    0,   # 0  → round(0  * 8/3) = 0
    3,   # 1  → round(1  * 8/3) = round(2.666) = 3
    5,   # 2  → round(2  * 8/3) = round(5.333) = 5
    8,   # 3  → round(3  * 8/3) = round(8.0)   = 8
    11,  # 4  → round(4  * 8/3) = round(10.666) = 11
    13,  # 5  → round(5  * 8/3) = round(13.333) = 13
    16,  # 6  → round(6  * 8/3) = round(16.0)   = 16
    19,  # 7  → round(7  * 8/3) = round(18.666) = 19
    21,  # 8  → round(8  * 8/3) = round(21.333) = 21
    24,  # 9  → round(9  * 8/3) = round(24.0)   = 24
    27,  # 10 → round(10 * 8/3) = round(26.666) = 27
    29,  # 11 → round(11 * 8/3) = round(29.333) = 29
    32,  # 12 → round(12 * 8/3) = round(32.0)   = 32
    35,  # 13 → round(13 * 8/3) = round(34.666) = 35
    37,  # 14 → round(14 * 8/3) = round(37.333) = 37
    40,  # 15 → round(15 * 8/3) = round(40.0)   = 40
]


# ---------------------------------------------------------------------------
# Tests: legacy mode
# ---------------------------------------------------------------------------

def test_legacy_exact_values():
    """'legacy' mode must reproduce the original mapping exactly for all 0..15."""
    for opll_vol, expected in enumerate(_LEGACY_EXPECTED):
        result = opll_vol_to_ms2_vol(opll_vol, mode='legacy')
        assert result == expected, (
            f"legacy mode: opll_vol={opll_vol} → expected {expected}, got {result}"
        )


def test_legacy_default_mode():
    """Calling opll_vol_to_ms2_vol without mode= should behave as 'legacy'."""
    for opll_vol, expected in enumerate(_LEGACY_EXPECTED):
        result = opll_vol_to_ms2_vol(opll_vol)
        assert result == expected, (
            f"default mode: opll_vol={opll_vol} → expected {expected}, got {result}"
        )


def test_legacy_clamp_below_zero():
    """Negative inputs should clamp to 0."""
    assert opll_vol_to_ms2_vol(-5, mode='legacy') == opll_vol_to_ms2_vol(0, mode='legacy')


def test_legacy_clamp_above_fifteen():
    """Inputs above 15 should clamp to 15."""
    assert opll_vol_to_ms2_vol(99, mode='legacy') == opll_vol_to_ms2_vol(15, mode='legacy')


# ---------------------------------------------------------------------------
# Tests: db mode
# ---------------------------------------------------------------------------

def test_db_in_range():
    """'db' mode must produce values in 0..63 for all OPLL inputs 0..15."""
    for opll_vol in range(16):
        result = opll_vol_to_ms2_vol(opll_vol, mode='db')
        assert 0 <= result <= 63, (
            f"db mode: opll_vol={opll_vol} → {result} is out of range 0..63"
        )


def test_db_is_int():
    """'db' mode must return an int."""
    for opll_vol in range(16):
        result = opll_vol_to_ms2_vol(opll_vol, mode='db')
        assert isinstance(result, int), (
            f"db mode: opll_vol={opll_vol} → returned {type(result)}, expected int"
        )


def test_db_monotonic():
    """'db' mode must be non-decreasing (monotonic) for OPLL 0..15."""
    values = [opll_vol_to_ms2_vol(v, mode='db') for v in range(16)]
    for i in range(1, len(values)):
        assert values[i] >= values[i - 1], (
            f"db mode not monotonic at index {i}: {values[i - 1]} → {values[i]}"
        )


def test_db_zero_maps_to_zero():
    """'db' mode: OPLL 0 (silence) must map to MS2 0."""
    assert opll_vol_to_ms2_vol(0, mode='db') == 0


def test_db_clamp_below_zero():
    """Negative inputs should clamp to 0 in db mode."""
    assert opll_vol_to_ms2_vol(-1, mode='db') == opll_vol_to_ms2_vol(0, mode='db')


def test_db_clamp_above_fifteen():
    """Inputs above 15 should clamp to 15 in db mode."""
    assert opll_vol_to_ms2_vol(16, mode='db') == opll_vol_to_ms2_vol(15, mode='db')


# ---------------------------------------------------------------------------
# Tests: direction consistency between legacy and db
# ---------------------------------------------------------------------------

def test_direction_consistency():
    """Both modes must have the same direction: 0 is minimum, 15 is maximum."""
    legacy_0 = opll_vol_to_ms2_vol(0, mode='legacy')
    legacy_15 = opll_vol_to_ms2_vol(15, mode='legacy')
    db_0 = opll_vol_to_ms2_vol(0, mode='db')
    db_15 = opll_vol_to_ms2_vol(15, mode='db')

    assert legacy_0 < legacy_15, "legacy: 0 should map to less than 15"
    assert db_0 < db_15, "db: 0 should map to less than 15"


def test_no_direction_inversion():
    """Neither mode should invert the direction relative to the other."""
    for opll_vol in range(1, 16):
        legacy_prev = opll_vol_to_ms2_vol(opll_vol - 1, mode='legacy')
        legacy_cur  = opll_vol_to_ms2_vol(opll_vol,     mode='legacy')
        db_prev     = opll_vol_to_ms2_vol(opll_vol - 1, mode='db')
        db_cur      = opll_vol_to_ms2_vol(opll_vol,     mode='db')
        # Both should be non-decreasing (same direction)
        assert legacy_cur >= legacy_prev, (
            f"legacy not non-decreasing at {opll_vol}: {legacy_prev} → {legacy_cur}"
        )
        assert db_cur >= db_prev, (
            f"db not non-decreasing at {opll_vol}: {db_prev} → {db_cur}"
        )


# ---------------------------------------------------------------------------
# Tests: CLI parsing
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VGM2MS2 = os.path.join(_REPO_ROOT, 'vgm2ms2.py')


def _run_cli(*extra_args: str) -> subprocess.CompletedProcess:
    """Run vgm2ms2.py with help flag to test argument parsing without a VGM file."""
    return subprocess.run(
        [sys.executable, _VGM2MS2, '--help'] + list(extra_args),
        capture_output=True,
        text=True,
    )


def test_cli_help_shows_vol_map():
    """--help output should mention --vol-map."""
    result = subprocess.run(
        [sys.executable, _VGM2MS2, '--help'],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert '--vol-map' in result.stdout, (
        f"--vol-map not found in --help output:\n{result.stdout}"
    )


def test_cli_vol_map_legacy_accepted():
    """--vol-map legacy should be accepted (no error from argument parsing)."""
    # Pass an invalid vgm_file so we get a file-not-found error, not an arg-parse error
    result = subprocess.run(
        [sys.executable, _VGM2MS2, 'nonexistent.vgm', '--vol-map', 'legacy'],
        capture_output=True,
        text=True,
    )
    # Should fail with file-not-found, not an argparse error about vol-map
    assert '--vol-map' not in result.stderr or 'invalid choice' not in result.stderr, (
        f"Unexpected argparse error:\n{result.stderr}"
    )


def test_cli_vol_map_db_accepted():
    """--vol-map db should be accepted (no argparse error)."""
    result = subprocess.run(
        [sys.executable, _VGM2MS2, 'nonexistent.vgm', '--vol-map', 'db'],
        capture_output=True,
        text=True,
    )
    assert 'invalid choice' not in result.stderr, (
        f"Unexpected argparse error for --vol-map db:\n{result.stderr}"
    )


def test_cli_vol_map_invalid_rejected():
    """Unknown --vol-map values should be rejected by argparse."""
    result = subprocess.run(
        [sys.executable, _VGM2MS2, 'nonexistent.vgm', '--vol-map', 'bogus'],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "Expected non-zero exit for invalid --vol-map"
    assert 'invalid choice' in result.stderr or 'bogus' in result.stderr, (
        f"Expected 'invalid choice' in stderr:\n{result.stderr}"
    )
