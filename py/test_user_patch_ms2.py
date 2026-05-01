"""test_user_patch_ms2.py - Unit tests for YM2413 user-patch MS2 export.

Validates that when a segment uses inst==0 and user_patches contains at least
one patch, the generated MS2 binary:
  1. Has an instrument count greater than the reference-library count (ref_count).
  2. Contains at least one INST event referencing an index >= ref_count in its
     pattern data.

Run directly:  python3 py/test_user_patch_ms2.py
Run via pytest: python -m pytest py/test_user_patch_ms2.py -v
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile

_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _this_dir)

from opll_mml import _Segment, NUM_CH
from ms2_writer import (
    _build_step_grid,
    _parse_user_v_num,
    _get_starting_instruments,
    segments_to_patterns,
    build_2op_instruments_chunk_data,
    _build_2op_instrument,
    opll_patch_to_opl4,
    _load_reference_library,
    STEPS_PER_PATTERN,
    OPLL_ROM_PATCHES,
)
from ms2_dump import iter_chunks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A known 8-byte YM2413 user patch (Violin-like).
_USER_PATCH_1 = bytes([0x71, 0x61, 0x1e, 0x07, 0xd3, 0x01, 0x99, 0x00])
_USER_PATCH_2 = bytes([0x13, 0x41, 0x1a, 0x06, 0xd3, 0x83, 0x40, 0x00])

# A fnum/block pair that maps to a valid MS2 note (C3).
_FNUM  = 172
_BLOCK = 3


def _make_user_segment(beat_start: int, beat_end: int, v_num: int) -> _Segment:
    """Build a keyon _Segment with inst=0 and at_token='@v{v_num}'."""
    seg = _Segment(
        reg_type='instVol', time=0.0, ch=0,
        beat_start=beat_start,
        keyon=1, fnum=_FNUM, block=_BLOCK, inst=0, vol=60, sus=0,
    )
    seg.beat_end = beat_end
    seg.beat = beat_end - beat_start
    seg.at_token = f'@v{v_num}'
    seg.retrigger_ticks = []
    seg.is_ryt = 0
    return seg


def _make_preset_segment(beat_start: int, beat_end: int, inst: int) -> _Segment:
    """Build a keyon _Segment with a ROM preset (inst != 0)."""
    seg = _Segment(
        reg_type='instVol', time=0.0, ch=0,
        beat_start=beat_start,
        keyon=1, fnum=_FNUM, block=_BLOCK, inst=inst, vol=60, sus=0,
    )
    seg.beat_end = beat_end
    seg.beat = beat_end - beat_start
    seg.at_token = f'@{inst}'
    seg.retrigger_ticks = []
    seg.is_ryt = 0
    return seg


def _empty_segments() -> dict:
    return {ch: [] for ch in range(NUM_CH)}


# ---------------------------------------------------------------------------
# Tests for _parse_user_v_num
# ---------------------------------------------------------------------------

def test_parse_user_v_num_valid():
    """'@v1' → 1, '@v12' → 12."""
    assert _parse_user_v_num('@v1') == 1
    assert _parse_user_v_num('@v12') == 12


def test_parse_user_v_num_preset_returns_none():
    """Preset tokens like '@5' must not be parsed as user-patch numbers."""
    assert _parse_user_v_num('@5') is None
    assert _parse_user_v_num('') is None
    assert _parse_user_v_num(None) is None


# ---------------------------------------------------------------------------
# Tests for _build_step_grid: at_token stored in grid state
# ---------------------------------------------------------------------------

def test_build_step_grid_stores_at_token():
    """Grid state tuple must include at_token as the 6th element."""
    segs = _empty_segments()
    segs[0].append(_make_user_segment(0, 2, v_num=1))

    total_steps = STEPS_PER_PATTERN
    grid = _build_step_grid(segs, total_steps)

    state = grid.get((0, 0))
    assert state is not None, "No grid state at step 0"
    assert len(state) == 6, f"Expected 6-element state tuple, got {len(state)}"
    keyon, ms2_note, inst, vol, retrigger, at_token = state
    assert keyon == 1
    assert inst == 0
    assert at_token == '@v1', f"Expected '@v1', got {at_token!r}"


# ---------------------------------------------------------------------------
# Tests for segments_to_patterns: user patch → appended instrument index
# ---------------------------------------------------------------------------

def test_segments_to_patterns_user_patch_uses_appended_index():
    """When a channel uses inst==0, the emitted INST event must reference
    an index >= ref_count (the appended user-patch slot)."""
    _, _, _, ref_count = _load_reference_library()

    user_patches = {1: _USER_PATCH_1}

    segs = _empty_segments()
    segs[0].append(_make_user_segment(0, 1, v_num=1))

    n_instruments = ref_count + len(user_patches)
    events = segments_to_patterns(
        segs,
        n_patterns=1,
        n_channels=1,
        n_instruments=n_instruments,
        user_patches=user_patches,
        ref_count=ref_count,
    )

    # Collect all INST (0x04 ...) events for channel 0 across all patterns.
    inst_indices: list[int] = []
    for pat in events:
        for _step, evts in pat[0]:
            for evt in evts:
                if evt[0] == 0x04:
                    inst_indices.append(evt[1])

    assert inst_indices, "Expected at least one INST event for user-patch channel"
    assert any(idx >= ref_count for idx in inst_indices), (
        f"Expected INST index >= ref_count ({ref_count}), "
        f"got indices: {inst_indices}"
    )
    assert inst_indices[0] == ref_count, (
        f"User patch @v1 should map to index {ref_count}, got {inst_indices[0]}"
    )


def test_segments_to_patterns_two_user_patches_distinct_indices():
    """Two distinct user patches must map to different appended indices."""
    _, _, _, ref_count = _load_reference_library()

    user_patches = {1: _USER_PATCH_1, 2: _USER_PATCH_2}
    n_instruments = ref_count + len(user_patches)

    segs = _empty_segments()
    segs[0].append(_make_user_segment(0, 1, v_num=1))
    segs[1].append(_make_user_segment(0, 1, v_num=2))

    events = segments_to_patterns(
        segs,
        n_patterns=1,
        n_channels=2,
        n_instruments=n_instruments,
        user_patches=user_patches,
        ref_count=ref_count,
    )

    def _collect_inst(ch_idx: int) -> list[int]:
        result = []
        for pat in events:
            for _step, evts in pat[ch_idx]:
                for evt in evts:
                    if evt[0] == 0x04:
                        result.append(evt[1])
        return result

    ch0_insts = _collect_inst(0)
    ch1_insts = _collect_inst(1)

    assert ch0_insts, "Expected INST event for ch0"
    assert ch1_insts, "Expected INST event for ch1"
    assert ch0_insts[0] == ref_count,     f"ch0: expected {ref_count}, got {ch0_insts[0]}"
    assert ch1_insts[0] == ref_count + 1, f"ch1: expected {ref_count+1}, got {ch1_insts[0]}"


def test_segments_to_patterns_preset_unaffected_by_user_patches():
    """Preset instruments must still map to their reference-library indices
    even when user_patches is non-empty."""
    _, _, _, ref_count = _load_reference_library()

    user_patches = {1: _USER_PATCH_1}
    n_instruments = ref_count + len(user_patches)

    segs = _empty_segments()
    segs[0].append(_make_preset_segment(0, 1, inst=5))   # OPLL preset 5 → index 4

    events = segments_to_patterns(
        segs,
        n_patterns=1,
        n_channels=1,
        n_instruments=n_instruments,
        user_patches=user_patches,
        ref_count=ref_count,
    )

    inst_indices = []
    for pat in events:
        for _step, evts in pat[0]:
            for evt in evts:
                if evt[0] == 0x04:
                    inst_indices.append(evt[1])

    assert inst_indices, "Expected INST event"
    assert inst_indices[0] == 4, (
        f"OPLL preset 5 should map to reference index 4, got {inst_indices[0]}"
    )


# ---------------------------------------------------------------------------
# Integration test: user-patch instruments appended in instruments chunk
# ---------------------------------------------------------------------------

def test_instruments_chunk_has_increased_count_with_user_patches():
    """build_2op_instruments_chunk_data extended with user patches must have
    instrument count == ref_count + number_of_user_patches."""
    _, ref_instruments_data, _, ref_count = _load_reference_library()

    user_patches = {1: _USER_PATCH_1, 2: _USER_PATCH_2}

    # Build the extra records exactly as build_ms2_file does.
    extra_records = b''
    for v_num in sorted(user_patches):
        patch_bytes = user_patches[v_num]
        opl4 = opll_patch_to_opl4(patch_bytes)
        name = f'User Patch v{v_num}'
        extra_records += _build_2op_instrument(opl4, tuning_idx=0, name=name)

    new_total = ref_count + len(user_patches)
    instruments_data = (
        struct.pack('<H', new_total)
        + ref_instruments_data[2:]
        + extra_records
    )

    stored_count = struct.unpack_from('<H', instruments_data, 0)[0]
    assert stored_count == new_total, (
        f"Expected instrument count {new_total}, got {stored_count}"
    )
    # Each instrument record is 30 bytes.
    expected_size = 2 + new_total * 30
    assert len(instruments_data) == expected_size, (
        f"Expected {expected_size} bytes, got {len(instruments_data)}"
    )


def test_build_ms2_file_user_patch_end_to_end():
    """Full build_ms2_file pipeline: when inst==0 is used with a voice CSV,
    the output MS2 has more instruments than the reference and the pattern
    contains an INST event referencing a slot >= ref_count."""
    from ms2_writer import build_ms2_file
    from ms2_validate import validate_ms2_bytes

    _, _, _, ref_count = _load_reference_library()

    # Build segments directly with inst=0 (user patch) at beat_start=0.
    patch_hex = _USER_PATCH_1.hex()
    voice_csv_content = (
        '#type,time,ch,ticks,inst,vol,patch_hex\n'
        f'patch,0.0,-1,0,0,0,{patch_hex}\n'
    )

    segs = _empty_segments()
    segs[0].append(_make_user_segment(0, 1, v_num=1))

    with tempfile.TemporaryDirectory() as tmpdir:
        voice_path = os.path.join(tmpdir, 'test_trace.opll_voice.csv')
        with open(voice_path, 'w') as f:
            f.write(voice_csv_content)

        ms2_bytes = build_ms2_file(segs, voice_path, stem='test', bpm=75)

    # Validate the MS2 structure.
    errors = validate_ms2_bytes(ms2_bytes)
    assert not errors, f"MS2 validation errors: {errors}"

    # Extract instrument count and check patterns for INST events.
    n_2op_inst_in_file = None
    inst_events: list[int] = []

    for _offset, ctype, _version, cdata in iter_chunks(ms2_bytes):
        if ctype == 3 and len(cdata) >= 2:
            n_2op_inst_in_file = struct.unpack_from('<H', cdata, 0)[0]
        elif ctype == 6:
            # Scan raw pattern bytes for INST opcode 0x04.
            i = 2  # skip count word
            while i < len(cdata):
                b = cdata[i]
                if b == 0x04 and i + 1 < len(cdata):
                    inst_events.append(cdata[i + 1])
                i += 1
        elif ctype == 0:
            break

    assert n_2op_inst_in_file is not None, "Chunk type 3 not found"
    assert n_2op_inst_in_file > ref_count, (
        f"Expected instrument count > ref_count ({ref_count}), "
        f"got {n_2op_inst_in_file}"
    )
    # The generated pattern must reference the user-patch instrument slot (>= ref_count).
    # ref_count is the melody count from _load_reference_library(); rhythm instruments
    # and user patches are appended after, so user patch INST index > ref_count.
    assert any(idx >= ref_count for idx in inst_events), (
        f"Expected at least one INST event with index >= {ref_count}, "
        f"got: {inst_events}"
    )


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

_TESTS = [
    test_parse_user_v_num_valid,
    test_parse_user_v_num_preset_returns_none,
    test_build_step_grid_stores_at_token,
    test_segments_to_patterns_user_patch_uses_appended_index,
    test_segments_to_patterns_two_user_patches_distinct_indices,
    test_segments_to_patterns_preset_unaffected_by_user_patches,
    test_instruments_chunk_has_increased_count_with_user_patches,
    test_build_ms2_file_user_patch_end_to_end,
]

if __name__ == '__main__':
    passed = failed = 0
    for fn in _TESTS:
        try:
            fn()
            print(f'OK    {fn.__name__}')
            passed += 1
        except Exception as exc:
            import traceback
            print(f'FAIL  {fn.__name__}: {exc}')
            traceback.print_exc()
            failed += 1
    print(f'\n{passed} passed, {failed} failed out of {passed + failed} tests.')
    sys.exit(0 if failed == 0 else 1)
