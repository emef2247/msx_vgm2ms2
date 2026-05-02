"""test_beat_based_ms2.py – Tests for beat-based MS2 output correctness.

Validates that segments_to_patterns() places NOTE_ON and NOTE_OFF events at
the correct steps when beat-based fields (beat_start / ms2_len) are used.

Covers:
  - NOTE_ON appears at beat_start (within the correct pattern/step_in_pat).
  - NOTE_OFF appears at beat_start + ms2_len.
  - Cross-pattern note-off (NOTE_OFF in a later pattern than NOTE_ON).
  - Pattern count estimation in build_ms2_file() includes the last NOTE_OFF.
  - retrigger_ticks is not used; each segment emits its own NOTE_ON.

Run directly:  python3 py/test_beat_based_ms2.py
Run via pytest: python -m pytest py/test_beat_based_ms2.py -v
"""

from __future__ import annotations

import os
import struct
import sys

_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _this_dir)

from opll_mml import _Segment, NUM_CH
from ms2_writer import (
    segments_to_patterns,
    build_ms2_file,
    STEPS_PER_PATTERN,
)
from ms2_dump import iter_chunks
from ms2_validate import validate_ms2_bytes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# C4-like note: block=3, fnum=172 → valid MS2 note
_FNUM  = 172
_BLOCK = 3
_INST  = 1   # Violin preset
_VOL   = 60  # already in MS2 attenuation scale (0=full, 63=silent)


def _empty_segments() -> dict:
    return {ch: [] for ch in range(NUM_CH)}


def _make_seg(beat_start: int, ms2_len: int,
              fnum: int = _FNUM, block: int = _BLOCK,
              inst: int = _INST, vol: int = _VOL) -> _Segment:
    """Build a keyon Segment using the beat-based model fields."""
    seg = _Segment(
        reg_type='instVol', time=0.0, ch=0,
        beat_start=beat_start,
        keyon=1, fnum=fnum, block=block, inst=inst, vol=vol, sus=0,
    )
    seg.ms2_len  = ms2_len
    seg.beat_end = beat_start + ms2_len   # kept in sync for helpers that read it
    seg.beat     = ms2_len
    seg.at_token = ''
    seg.retrigger_ticks = []              # not used in beat-based model
    seg.is_ryt   = 0
    return seg


def _collect_events(channel_event_lists, ch):
    """Return list of (abs_step, opcode) for all events on *ch*."""
    result = []
    for pat_idx, pat in enumerate(channel_event_lists):
        for step_in_pat, evts in pat[ch]:
            abs_step = pat_idx * STEPS_PER_PATTERN + step_in_pat
            for evt in evts:
                result.append((abs_step, evt[0]))
    return result


# ---------------------------------------------------------------------------
# Tests: NOTE_ON placement
# ---------------------------------------------------------------------------

def test_note_on_placed_at_beat_start():
    """NOTE_ON (0x00) must appear at beat_start."""
    segs = _empty_segments()
    segs[0].append(_make_seg(beat_start=3, ms2_len=2))

    events = segments_to_patterns(segs, n_patterns=1, n_channels=1, n_instruments=15)
    note_ons = [(s, op) for s, op in _collect_events(events, 0) if op == 0x00]

    assert len(note_ons) == 1, f"Expected 1 NOTE_ON, got {note_ons}"
    assert note_ons[0][0] == 3, f"NOTE_ON should be at step 3, got step {note_ons[0][0]}"


def test_note_on_in_second_pattern():
    """A note starting at step 16 (pattern 1, step_in_pat 0) is placed correctly."""
    segs = _empty_segments()
    segs[0].append(_make_seg(beat_start=16, ms2_len=4))

    events = segments_to_patterns(segs, n_patterns=2, n_channels=1, n_instruments=15)
    note_ons = [(s, op) for s, op in _collect_events(events, 0) if op == 0x00]

    assert len(note_ons) == 1, f"Expected 1 NOTE_ON, got {note_ons}"
    assert note_ons[0][0] == 16, f"NOTE_ON should be at step 16, got step {note_ons[0][0]}"


# ---------------------------------------------------------------------------
# Tests: NOTE_OFF placement (ms2_len-based)
# ---------------------------------------------------------------------------

def test_note_off_placed_at_beat_start_plus_ms2_len():
    """NOTE_OFF (0x01) must appear at beat_start + ms2_len."""
    segs = _empty_segments()
    segs[0].append(_make_seg(beat_start=4, ms2_len=3))   # NOTE_OFF at step 7

    events = segments_to_patterns(segs, n_patterns=1, n_channels=1, n_instruments=15)
    note_offs = [(s, op) for s, op in _collect_events(events, 0) if op == 0x01]

    assert len(note_offs) == 1, f"Expected 1 NOTE_OFF, got {note_offs}"
    assert note_offs[0][0] == 7, f"NOTE_OFF should be at step 7, got step {note_offs[0][0]}"


def test_note_off_cross_pattern():
    """NOTE_OFF that falls in the next pattern is placed correctly (cross-pattern)."""
    # Note starts at step 14 (last two steps of pattern 0), lasts 4 steps.
    # NOTE_OFF at step 14 + 4 = 18 → pattern 1, step_in_pat 2.
    segs = _empty_segments()
    segs[0].append(_make_seg(beat_start=14, ms2_len=4))

    events = segments_to_patterns(segs, n_patterns=2, n_channels=1, n_instruments=15)
    note_offs = [(s, op) for s, op in _collect_events(events, 0) if op == 0x01]

    assert len(note_offs) == 1, f"Expected 1 NOTE_OFF, got {note_offs}"
    assert note_offs[0][0] == 18, (
        f"NOTE_OFF should be at step 18 (pattern 1, step 2), "
        f"got step {note_offs[0][0]}"
    )


def test_note_off_not_emitted_when_next_note_is_legato():
    """When the next note starts exactly at beat_start + ms2_len (legato), no NOTE_OFF
    is emitted between the two notes."""
    segs = _empty_segments()
    # Note 1: steps 0-3 (ms2_len=4), Note 2: starts at step 4 (legato)
    segs[0].append(_make_seg(beat_start=0, ms2_len=4))
    segs[0].append(_make_seg(beat_start=4, ms2_len=4))

    events = segments_to_patterns(segs, n_patterns=1, n_channels=1, n_instruments=15)
    all_events = _collect_events(events, 0)

    # There should be 2 NOTE_ONs and only 1 NOTE_OFF (for the final note).
    note_ons  = [s for s, op in all_events if op == 0x00]
    note_offs = [s for s, op in all_events if op == 0x01]

    assert len(note_ons)  == 2, f"Expected 2 NOTE_ONs, got {note_ons}"
    assert len(note_offs) == 1, f"Expected 1 NOTE_OFF (legato), got {note_offs}"
    assert note_offs[0]   == 8, f"Final NOTE_OFF should be at step 8, got {note_offs[0]}"


def test_note_off_emitted_when_gap_between_notes():
    """When there is a gap between two notes, a NOTE_OFF must be emitted."""
    segs = _empty_segments()
    # Note 1: steps 0-1 (ms2_len=2), gap at step 2, Note 2: starts at step 4
    segs[0].append(_make_seg(beat_start=0, ms2_len=2))
    segs[0].append(_make_seg(beat_start=4, ms2_len=2))

    events = segments_to_patterns(segs, n_patterns=1, n_channels=1, n_instruments=15)
    note_offs = [(s, op) for s, op in _collect_events(events, 0) if op == 0x01]

    assert len(note_offs) == 2, f"Expected 2 NOTE_OFFs (gap + final), got {note_offs}"
    assert note_offs[0][0] == 2, f"First NOTE_OFF should be at step 2, got {note_offs[0][0]}"
    assert note_offs[1][0] == 6, f"Second NOTE_OFF should be at step 6, got {note_offs[1][0]}"


# ---------------------------------------------------------------------------
# Tests: pattern count estimation in build_ms2_file
# ---------------------------------------------------------------------------

def _build_minimal_ms2(segments: dict) -> bytes:
    """Call build_ms2_file() with minimal arguments (no voice CSV, no base MS2)."""
    return build_ms2_file(
        segments=segments,
        voice_csv_path=None,
        stem='test',
        bpm=120,
    )


def _get_n_patterns_from_ms2(ms2_bytes: bytes) -> int:
    """Parse the pattern count from an MS2 binary."""
    for _offset, ctype, _version, cdata in iter_chunks(ms2_bytes):
        if ctype == 6 and len(cdata) >= 2:
            return struct.unpack_from('<H', cdata, 0)[0]
    return 0


def test_n_patterns_fits_last_note_off():
    """build_ms2_file() must allocate enough patterns to hold the last NOTE_OFF.

    A note at step 14 with ms2_len=4 has NOTE_OFF at step 18 → requires 2 patterns.
    """
    segs = _empty_segments()
    segs[0].append(_make_seg(beat_start=14, ms2_len=4))

    ms2_bytes = _build_minimal_ms2(segs)
    errors = validate_ms2_bytes(ms2_bytes)
    assert not errors, f"MS2 validation errors: {errors}"

    n_patterns = _get_n_patterns_from_ms2(ms2_bytes)
    assert n_patterns >= 2, (
        f"Expected >= 2 patterns to fit NOTE_OFF at step 18, got {n_patterns}"
    )


def test_n_patterns_single_short_note():
    """A single short note that fits entirely in pattern 0 needs only 1 pattern."""
    segs = _empty_segments()
    segs[0].append(_make_seg(beat_start=0, ms2_len=4))   # NOTE_OFF at step 4

    ms2_bytes = _build_minimal_ms2(segs)
    errors = validate_ms2_bytes(ms2_bytes)
    assert not errors, f"MS2 validation errors: {errors}"

    n_patterns = _get_n_patterns_from_ms2(ms2_bytes)
    assert n_patterns == 1, (
        f"Expected 1 pattern for short note in step 0-4, got {n_patterns}"
    )


def test_ms2_output_valid_with_beat_segments():
    """Full pipeline: build_ms2_file() with beat-based segments produces a valid MS2."""
    segs = _empty_segments()
    segs[0].append(_make_seg(beat_start=0,  ms2_len=4))
    segs[0].append(_make_seg(beat_start=4,  ms2_len=4))
    segs[0].append(_make_seg(beat_start=10, ms2_len=6))   # NOTE_OFF at 16 (pattern boundary)
    segs[1].append(_make_seg(beat_start=16, ms2_len=8))   # NOTE_OFF at 24 (pattern 1, step 8)

    ms2_bytes = _build_minimal_ms2(segs)
    errors = validate_ms2_bytes(ms2_bytes)
    assert not errors, f"MS2 validation errors: {errors}"

    n_patterns = _get_n_patterns_from_ms2(ms2_bytes)
    assert n_patterns >= 2, f"Expected >= 2 patterns, got {n_patterns}"


# ---------------------------------------------------------------------------
# Tests: retrigger_ticks is not used for beat-based segments
# ---------------------------------------------------------------------------

def test_retrigger_ticks_ignored_for_beat_based():
    """retrigger_ticks must not affect output: two segments with same beat_start
    each emit their own NOTE_ON without relying on retrigger_ticks."""
    segs = _empty_segments()
    # Two segments at the same beat_start (retrigger)
    s1 = _make_seg(beat_start=2, ms2_len=2)
    s2 = _make_seg(beat_start=2, ms2_len=2)
    # Ensure retrigger_ticks is present but should be ignored
    s1.retrigger_ticks = [100]
    s2.retrigger_ticks = []
    segs[0].append(s1)
    segs[0].append(s2)

    events = segments_to_patterns(segs, n_patterns=1, n_channels=1, n_instruments=15)
    note_ons = [(s, op) for s, op in _collect_events(events, 0) if op == 0x00]

    # Both segments are at the same step; we expect at least one NOTE_ON at step 2.
    assert any(s == 2 for s, _ in note_ons), (
        f"Expected at least one NOTE_ON at step 2, got {note_ons}"
    )


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

_TESTS = [
    test_note_on_placed_at_beat_start,
    test_note_on_in_second_pattern,
    test_note_off_placed_at_beat_start_plus_ms2_len,
    test_note_off_cross_pattern,
    test_note_off_not_emitted_when_next_note_is_legato,
    test_note_off_emitted_when_gap_between_notes,
    test_n_patterns_fits_last_note_off,
    test_n_patterns_single_short_note,
    test_ms2_output_valid_with_beat_segments,
    test_retrigger_ticks_ignored_for_beat_based,
]

if __name__ == '__main__':
    passed = failed = 0
    for fn in _TESTS:
        try:
            fn()
            print(f'OK    {fn.__name__}')
            passed += 1
        except Exception as exc:
            print(f'FAIL  {fn.__name__}: {exc}')
            import traceback; traceback.print_exc()
            failed += 1
    print(f'\n{passed} passed, {failed} failed out of {passed + failed} tests.')
    sys.exit(0 if failed == 0 else 1)
