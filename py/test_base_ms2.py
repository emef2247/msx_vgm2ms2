"""
test_base_ms2.py  –  Tests for --base-ms2 and instrument-library generation.

These tests do not require large binary files to be checked in; they build
temporary MS2 files in memory or in a tempdir.
"""

import os
import struct
import sys
import tempfile
import warnings

sys.path.insert(0, os.path.dirname(__file__))

from ms2_writer import (
    MS2_SIGNATURE,
    _load_library_from_ms2,
    _load_reference_library,
    build_instrument_library_ms2,
    _read_ms2_chunk_data,
    opll_patch_to_opl4,
)
from opll_presets import (
    PRESET_LIBRARY_MAP,
    YMFM_YM2413_VOICES,
    YMVOICE_YM2413_VOICES,
    EXPERIMENT_YM2413_PRESET,
    EXPERIMENT_VRC7_PRESET,
    EXPERIMENT_YMF281B_PRESET,
    EXPERIMENT_YM2423_PRESET,
    YMFM_VRC7_VOICES,
    YMFM_YMF281B_VOICES,
    YMFM_YM2423_VOICES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_instruments_in_ms2(ms2_bytes: bytes) -> int:
    """Return the number of 2OP instruments declared in chunk type 3."""
    data = _read_ms2_chunk_data(ms2_bytes, 3)
    if data and len(data) >= 2:
        return struct.unpack_from('<H', data, 0)[0]
    return 0


def _make_library_ms2(n_instruments: int) -> bytes:
    """Build a minimal instrument-only MS2 with *n_instruments* 2OP presets."""
    patches = [bytes(YMFM_YM2413_VOICES[i % 18]) for i in range(n_instruments)]
    return build_instrument_library_ms2(patches, 'Test Library', n_presets=n_instruments)


# ---------------------------------------------------------------------------
# opll_presets module tests
# ---------------------------------------------------------------------------

def test_preset_library_map_has_all_11_entries():
    """PRESET_LIBRARY_MAP should have exactly 11 entries."""
    assert len(PRESET_LIBRARY_MAP) == 11


def test_preset_library_map_filenames_8dot3():
    """All output filenames must fit the 8.3 convention."""
    for key, (table, filename) in PRESET_LIBRARY_MAP.items():
        name, ext = os.path.splitext(filename)
        assert len(name) <= 8, f"{key}: base name '{name}' exceeds 8 chars"
        assert len(ext) <= 4, f"{key}: extension '{ext}' exceeds 4 chars (incl. dot)"
        assert ext.upper() == '.MS2', f"{key}: extension is not .MS2"


def test_preset_tables_have_18_rows():
    """Each preset table must have 18 rows of 8 bytes."""
    for key, (table, _) in PRESET_LIBRARY_MAP.items():
        assert len(table) == 18, f"{key}: expected 18 rows, got {len(table)}"
        for i, row in enumerate(table):
            assert len(row) == 8, f"{key}[{i}]: expected 8 bytes, got {len(row)}"


def test_experiment_presets_differ_from_ymfm():
    """EXPERIMENT presets should not be byte-identical to their YMFM originals."""
    pairs = [
        (EXPERIMENT_YM2413_PRESET, YMFM_YM2413_VOICES),
        (EXPERIMENT_VRC7_PRESET,   YMFM_VRC7_VOICES),
        (EXPERIMENT_YMF281B_PRESET, YMFM_YMF281B_VOICES),
        (EXPERIMENT_YM2423_PRESET,  YMFM_YM2423_VOICES),
    ]
    for exp, ymfm in pairs:
        assert exp != ymfm, "EXPERIMENT preset is identical to YMFM source"


def test_experiment_ym2423_preset_has_nonzero_rows():
    """The EXPERIMENT_YM2423 voices (1..15) should not be all-zero."""
    for i in range(15):  # voices 1..15 (indices 0..14)
        assert any(b != 0 for b in EXPERIMENT_YM2423_PRESET[i]), \
            f"EXPERIMENT_YM2423 voice {i+1} is all zeros"


# ---------------------------------------------------------------------------
# build_instrument_library_ms2 tests
# ---------------------------------------------------------------------------

def test_instrument_library_has_correct_signature():
    """Instrument-only MS2 starts with the correct 8-byte signature."""
    ms2 = _make_library_ms2(16)
    assert ms2[:8] == MS2_SIGNATURE


def test_instrument_library_chunk3_count_equals_16():
    """Instrument-only MS2 with 16 presets reports 16 in the 2OP instruments chunk."""
    ms2 = _make_library_ms2(16)
    assert _count_instruments_in_ms2(ms2) == 16


def test_instrument_library_with_fewer_patches():
    """build_instrument_library_ms2 clamps to available patches if fewer than n_presets."""
    patches = [bytes(YMFM_YM2413_VOICES[i]) for i in range(8)]  # only 8 available
    ms2 = build_instrument_library_ms2(patches, 'Small', n_presets=16)
    # Should have min(16, 8) = 8 instruments
    assert _count_instruments_in_ms2(ms2) == 8


def test_instrument_library_all_preset_keys_buildable():
    """All 11 preset tables can be converted to a valid instrument-only MS2."""
    for key, (table, filename) in PRESET_LIBRARY_MAP.items():
        patches = [bytes(row) for row in table[:16]]
        ms2 = build_instrument_library_ms2(patches, key[:65], n_presets=16)
        assert ms2[:8] == MS2_SIGNATURE, f"{key}: bad signature"
        count = _count_instruments_in_ms2(ms2)
        assert count >= 16, f"{key}: expected >= 16 instruments, got {count}"


# ---------------------------------------------------------------------------
# _load_library_from_ms2 tests
# ---------------------------------------------------------------------------

def test_load_base_ms2_uses_first_16_instruments():
    """_load_library_from_ms2 keeps exactly the first 16 instruments when MS2 has more."""
    # Build a library MS2 with 18 instruments.
    ms2_18 = _make_library_ms2(18)
    with tempfile.NamedTemporaryFile(suffix='.MS2', delete=False) as f:
        f.write(ms2_18)
        tmp = f.name
    try:
        tunings, insts, n_tun, n_inst = _load_library_from_ms2(tmp, max_presets=16)
        assert n_inst == 16, f"Expected 16, got {n_inst}"
        assert struct.unpack_from('<H', insts, 0)[0] == 16
    finally:
        os.unlink(tmp)


def test_load_base_ms2_exact_16_instruments():
    """_load_library_from_ms2 returns 16 when the MS2 has exactly 16."""
    ms2_16 = _make_library_ms2(16)
    with tempfile.NamedTemporaryFile(suffix='.MS2', delete=False) as f:
        f.write(ms2_16)
        tmp = f.name
    try:
        _, insts, _, n_inst = _load_library_from_ms2(tmp, max_presets=16)
        assert n_inst == 16
        assert struct.unpack_from('<H', insts, 0)[0] == 16
    finally:
        os.unlink(tmp)


def test_load_base_ms2_fewer_than_16_warns():
    """_load_library_from_ms2 emits a warning when fewer than 16 instruments are present."""
    ms2_8 = _make_library_ms2(8)
    with tempfile.NamedTemporaryFile(suffix='.MS2', delete=False) as f:
        f.write(ms2_8)
        tmp = f.name
    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            _, insts, _, n_inst = _load_library_from_ms2(tmp, max_presets=16)
        assert n_inst == 8
        assert any('8' in str(warning.message) for warning in w), \
            "Expected a warning about instrument count, got: " + str([str(x.message) for x in w])
    finally:
        os.unlink(tmp)


def test_load_base_ms2_ignores_beyond_16():
    """Instruments at index 16+ from the base MS2 must NOT appear in the returned chunk."""
    # Build MS2 with 20 instruments (pad with extras).
    patches_20 = [bytes(YMFM_YM2413_VOICES[i % 18]) for i in range(20)]
    ms2_20 = build_instrument_library_ms2(patches_20, 'Test20', n_presets=20)
    with tempfile.NamedTemporaryFile(suffix='.MS2', delete=False) as f:
        f.write(ms2_20)
        tmp = f.name
    try:
        _, insts, _, n_inst = _load_library_from_ms2(tmp, max_presets=16)
        # Must have at most 16 instruments.
        assert n_inst == 16
        # The chunk data size must match 16 instrument records (30 bytes each) + 2-byte count.
        _INST_RECORD_SIZE = 30
        expected_size = 2 + 16 * _INST_RECORD_SIZE
        assert len(insts) == expected_size, \
            f"Expected {expected_size} bytes, got {len(insts)}"
    finally:
        os.unlink(tmp)


def test_load_base_ms2_missing_file_falls_back():
    """_load_library_from_ms2 falls back to the reference library if the file is missing."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter('always')
        tunings, insts, n_tun, n_inst = _load_library_from_ms2(
            '/nonexistent/path/to/nowhere.MS2', max_presets=16
        )
    ref_tunings, ref_insts, ref_n_tun, ref_n_inst = _load_reference_library()
    assert n_tun == ref_n_tun
    assert n_inst == ref_n_inst
    assert any('nonexistent' in str(warning.message).lower() or
               'Could not load' in str(warning.message) or
               'Falling back' in str(warning.message)
               for warning in w), \
        "Expected a fallback warning, got: " + str([str(x.message) for x in w])


# ---------------------------------------------------------------------------
# Integration: build_ms2_file with base_ms2
# ---------------------------------------------------------------------------

def test_build_ms2_file_uses_base_ms2_instruments():
    """build_ms2_file with base_ms2= uses the base library's instruments."""
    from ms2_writer import build_ms2_file
    from opll_mml import _build_segments, NUM_CH

    # Build a library with distinct instruments (using YMVOICE, not default YMFM).
    ymvoice_patches = [bytes(row) for row in YMVOICE_YM2413_VOICES[:16]]
    ms2_lib = build_instrument_library_ms2(ymvoice_patches, 'YMVOICE Library')
    with tempfile.NamedTemporaryFile(suffix='.MS2', delete=False) as f:
        f.write(ms2_lib)
        tmp_lib = f.name

    # Use empty segments (no notes) – just verify the instrument count.
    try:
        segments: dict = {ch: [] for ch in range(NUM_CH)}
        ms2_song = build_ms2_file(
            segments=segments,
            voice_csv_path=None,
            stem='TestSong',
            bpm=120,
            timer=0,
            base_ms2=tmp_lib,
        )
        # The chunk 3 in the song should have the YMVOICE library instruments (16).
        n_inst = _count_instruments_in_ms2(ms2_song)
        assert n_inst == 16, f"Expected 16 preset instruments from base_ms2, got {n_inst}"
    finally:
        os.unlink(tmp_lib)


# ---------------------------------------------------------------------------
# Structural integrity of generated MS2
# ---------------------------------------------------------------------------

def test_instrument_library_ms2_has_required_chunks():
    """Instrument-only MS2 must contain chunk types 1, 2, 3, 4, 5, 6."""
    ms2 = _make_library_ms2(16)
    required = {1, 2, 3, 4, 5, 6}
    found = set()
    pos = 8  # skip signature
    while pos + 4 <= len(ms2):
        chunk_type, _, chunk_size = struct.unpack_from('<BBH', ms2, pos)
        if chunk_type == 0:
            break
        found.add(chunk_type)
        pos += 4 + chunk_size
    assert required.issubset(found), f"Missing chunks: {required - found}"


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
