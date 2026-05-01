"""test_retrigger.py - Unit tests for keyoff->keyon retrigger detection.

In the beat-based segment model, each keyon=1 event is an independent Segment
with beat_start / beat_end positions. Retriggers are detected when two segments
share the same beat_start; _build_step_grid() marks these with retrigger=True,
and segments_to_patterns() emits a NOTE_ON for each.

Run directly:  python3 py/test_retrigger.py
Run via pytest: python -m pytest py/test_retrigger.py -v
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _this_dir)

from opll_mml import _Segment, NUM_CH
from ms2_writer import (
    _build_step_grid,
    segments_to_patterns,
    fnum_block_to_ms2_note,
    STEPS_PER_PATTERN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A fnum/block combination that maps to a valid MS2 note (C4 = block=3, fnum=172).
_FNUM  = 172
_BLOCK = 3
_INST  = 1
_VOL   = 60  # MS2 vol (0-63); already converted


def _empty_segments() -> dict:
    return {ch: [] for ch in range(NUM_CH)}


def _make_segment(beat_start: int, beat_end: int, keyon: int = 1,
                  fnum: int = _FNUM, block: int = _BLOCK,
                  inst: int = _INST, vol: int = _VOL) -> _Segment:
    """Build a _Segment for testing with the new beat-based model."""
    seg = _Segment(
        reg_type='instVol', time=0.0, ch=0,
        beat_start=beat_start,
        keyon=keyon, fnum=fnum, block=block, inst=inst, vol=vol, sus=0,
    )
    seg.beat_end = beat_end
    seg.beat = beat_end - beat_start
    seg.at_token = ''
    seg.retrigger_ticks = []
    seg.is_ryt = 0
    return seg


# ---------------------------------------------------------------------------
# Tests for segment structure in beat-based model
# ---------------------------------------------------------------------------

def test_beat_segments_normal_sequence():
    """Two keyon segments at different beat positions: verify beat_start values."""
    segs = _empty_segments()
    segs[0].append(_make_segment(beat_start=0, beat_end=1))   # note at step 0
    segs[0].append(_make_segment(beat_start=2, beat_end=3))   # note at step 2

    ch_segs = segs[0]
    assert len(ch_segs) == 2, f"Expected 2 segments, got {len(ch_segs)}"
    assert ch_segs[0].beat_start == 0, f"First segment beat_start should be 0"
    assert ch_segs[1].beat_start == 2, f"Second segment beat_start should be 2"


def test_beat_segments_retrigger_same_step():
    """Two keyon segments at the same beat_start represents a retrigger."""
    segs = _empty_segments()
    segs[0].append(_make_segment(beat_start=1, beat_end=2))
    segs[0].append(_make_segment(beat_start=1, beat_end=2))   # retrigger

    ch_segs = segs[0]
    assert len(ch_segs) == 2, "Expected 2 independent segments for retrigger"
    assert ch_segs[0].beat_start == ch_segs[1].beat_start == 1


def test_beat_segments_keyon_flag():
    """All segments from _make_segment have keyon=1."""
    segs = _empty_segments()
    segs[0].append(_make_segment(0, 1))
    segs[0].append(_make_segment(2, 3))
    assert all(s.keyon == 1 for s in segs[0])


# ---------------------------------------------------------------------------
# Tests for _build_step_grid() retrigger flag (beat-based)
# ---------------------------------------------------------------------------

def test_step_grid_retrigger_set_for_same_beat_start():
    """When two segments share the same beat_start, _build_step_grid() must
    set retrigger=True at that step for the second one."""
    segs = _empty_segments()
    segs[0].append(_make_segment(beat_start=1, beat_end=2))
    segs[0].append(_make_segment(beat_start=1, beat_end=2))   # retrigger

    total_steps = 2 * STEPS_PER_PATTERN
    grid = _build_step_grid(segs, total_steps)

    state = grid.get((0, 1))
    assert state is not None, "No grid state at step 1"
    keyon, ms2_note, inst, vol, retrigger, at_token = state
    assert keyon == 1,    f"Expected keyon=1 at retrigger step, got {keyon}"
    assert retrigger,     "Expected retrigger=True for same-beat_start segments"


def test_step_grid_no_retrigger_different_beat_starts():
    """When segments have different beat_start values, retrigger must be False."""
    segs = _empty_segments()
    segs[0].append(_make_segment(beat_start=0, beat_end=1))
    segs[0].append(_make_segment(beat_start=2, beat_end=3))

    total_steps = 2 * STEPS_PER_PATTERN
    grid = _build_step_grid(segs, total_steps)

    state0 = grid.get((0, 0))
    state2 = grid.get((0, 2))
    assert state0 is not None
    assert state2 is not None
    assert not state0[4], "No retrigger expected at step 0"
    assert not state2[4], "No retrigger expected at step 2"


# ---------------------------------------------------------------------------
# End-to-end test: segments_to_patterns() must emit NOTE_ON for each segment
# ---------------------------------------------------------------------------

def test_segments_to_patterns_emits_note_on_per_segment():
    """Full pipeline: each keyon segment must produce a NOTE_ON event."""
    segs = _empty_segments()
    segs[0].append(_make_segment(beat_start=0, beat_end=1))
    segs[0].append(_make_segment(beat_start=1, beat_end=2))   # retrigger

    n_patterns    = 2
    n_channels    = 1
    n_instruments = 15

    channel_event_lists = segments_to_patterns(
        segs, n_patterns, n_channels, n_instruments
    )

    # Collect all NOTE_ON (0x00 ...) events across all patterns for channel 0.
    note_on_steps: list[int] = []
    for pat_idx, pat in enumerate(channel_event_lists):
        for step_in_pat, evts in pat[0]:    # ch=0
            for evt in evts:
                if evt[0] == 0x00:          # NOTE_ON opcode
                    note_on_steps.append(pat_idx * STEPS_PER_PATTERN + step_in_pat)

    assert len(note_on_steps) >= 2, (
        f"Expected at least 2 NOTE_ON events, "
        f"got {len(note_on_steps)} at steps {note_on_steps}"
    )
    assert 0 in note_on_steps, "Expected NOTE_ON at step 0 (first note)"
    assert 1 in note_on_steps, (
        f"Expected NOTE_ON at step 1 (second note), got NOTE_ONs at steps {note_on_steps}"
    )


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

_TESTS = [
    test_beat_segments_normal_sequence,
    test_beat_segments_retrigger_same_step,
    test_beat_segments_keyon_flag,
    test_step_grid_retrigger_set_for_same_beat_start,
    test_step_grid_no_retrigger_different_beat_starts,
    test_segments_to_patterns_emits_note_on_per_segment,
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
