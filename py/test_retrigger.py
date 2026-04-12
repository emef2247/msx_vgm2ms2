"""test_retrigger.py - Unit tests for keyoff->keyon retrigger detection.

Tests that when a keyoff and keyon occur within the same VGM tick (tick group),
the exporter emits a NOTE_ON retrigger instead of leaving the step blank (which
would be interpreted as a tie/continue in MS²).

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

from opll_mml import _build_segments, _Segment, NUM_CH
from ms2_writer import (
    _build_step_grid,
    segments_to_patterns,
    fnum_block_to_ms2_note,
    TICKS_PER_STEP_75BPM,
    STEPS_PER_PATTERN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_trace(rows: list[tuple]) -> str:
    """Write a minimal trace CSV and return the file path.

    Each row is (tick, ch, keyon, fnum, block, inst, vol).
    Columns match the format expected by _build_segments():
      col 0: ignored (type label)
      col 1: ignored (time)
      col 2: ch
      col 3: ticks
      col 4: keyon
      col 5: fnum
      col 6: block
      col 7: inst
      col 8: vol
    """
    fh = tempfile.NamedTemporaryFile(
        mode='w', suffix='.csv', delete=False, newline='\n'
    )
    fh.write('#type,time,ch,ticks,keyon,fnum,block,inst,vol\n')
    for tick, ch, keyon, fnum, block, inst, vol in rows:
        fh.write(f'N,0,{ch},{tick},{keyon},{fnum},{block},{inst},{vol}\n')
    fh.close()
    return fh.name


# A fnum/block combination that maps to a valid MS2 note (C3 ≈ block=3, fnum=172).
_FNUM  = 172
_BLOCK = 3
_INST  = 1
_VOL   = 0


# ---------------------------------------------------------------------------
# Tests for _build_segments()
# ---------------------------------------------------------------------------

def test_build_segments_no_retrigger_normal_sequence():
    """Normal keyoff→keyon across different ticks: standard two segments."""
    rows = [
        (0,  0, 1, _FNUM, _BLOCK, _INST, _VOL),   # tick 0: keyon
        (12, 0, 0, _FNUM, _BLOCK, _INST, _VOL),   # tick 12: keyoff
        (24, 0, 1, _FNUM, _BLOCK, _INST, _VOL),   # tick 24: keyon (new note)
    ]
    path = _write_trace(rows)
    try:
        segs = _build_segments(path)
    finally:
        os.unlink(path)

    ch_segs = segs[0]
    # Expect: ON segment [0,12), OFF segment [12,24), ON segment [24,...)
    on_segs  = [s for s in ch_segs if s.keyon]
    off_segs = [s for s in ch_segs if not s.keyon]
    assert len(on_segs) == 2,  f"Expected 2 ON segments, got {len(on_segs)}"
    assert len(off_segs) == 1, f"Expected 1 OFF segment, got {len(off_segs)}"


def test_build_segments_hidden_retrigger_same_tick():
    """Keyoff then keyon at the exact same tick must produce an explicit
    zero-length keyoff segment so downstream can detect the retrigger."""
    rows = [
        (0,  0, 1, _FNUM, _BLOCK, _INST, _VOL),   # tick 0: keyon
        (12, 0, 0, _FNUM, _BLOCK, _INST, _VOL),   # tick 12: keyoff  } same tick
        (12, 0, 1, _FNUM, _BLOCK, _INST, _VOL),   # tick 12: keyon   }
    ]
    path = _write_trace(rows)
    try:
        segs = _build_segments(path)
    finally:
        os.unlink(path)

    ch_segs = segs[0]

    # Must have at least one OFF segment so the retrigger is visible.
    off_segs = [s for s in ch_segs if not s.keyon]
    assert off_segs, (
        "Expected at least one keyoff segment for same-tick keyoff→keyon, "
        f"got segments: {[(s.tick_start, s.tick_end, s.keyon) for s in ch_segs]}"
    )

    # The OFF segment is a zero-length boundary marker (tick_start == tick_end).
    off = off_segs[0]
    assert off.tick_start == 12, f"Keyoff segment should start at tick 12, got {off.tick_start}"
    assert off.tick_end == 12,   f"Zero-length keyoff marker: tick_end should equal tick_start"


def test_build_segments_inst_vol_same_tick_not_affected():
    """INST/VOL writes at the same tick as KeyOn must still use final-state
    evaluation and must NOT produce a spurious keyoff segment."""
    # Simulate: tick 0 keyon with one inst, tick 12 same keyon+inst+vol update
    rows = [
        (0,  0, 1, _FNUM, _BLOCK, _INST,   _VOL),
        (12, 0, 1, _FNUM, _BLOCK, _INST+1, _VOL),   # inst change while still ON
    ]
    path = _write_trace(rows)
    try:
        segs = _build_segments(path)
    finally:
        os.unlink(path)

    ch_segs = segs[0]
    off_segs = [s for s in ch_segs if not s.keyon]
    assert not off_segs, (
        "No keyoff segment expected for pure inst-change while keyon=1, "
        f"got: {[(s.tick_start, s.tick_end, s.keyon) for s in ch_segs]}"
    )


# ---------------------------------------------------------------------------
# Tests for _build_step_grid() retrigger flag
# ---------------------------------------------------------------------------

def test_step_grid_retrigger_set_for_same_step_keyoff_keyon():
    """After _build_segments() inserts a zero-length keyoff, _build_step_grid()
    must set retrigger=True at that step."""
    rows = [
        (0,  0, 1, _FNUM, _BLOCK, _INST, _VOL),
        (12, 0, 0, _FNUM, _BLOCK, _INST, _VOL),   # keyoff at tick 12
        (12, 0, 1, _FNUM, _BLOCK, _INST, _VOL),   # keyon  at tick 12
    ]
    path = _write_trace(rows)
    try:
        segs = _build_segments(path)
    finally:
        os.unlink(path)

    ticks_per_step = TICKS_PER_STEP_75BPM   # 12
    total_steps = 2 * STEPS_PER_PATTERN
    grid = _build_step_grid(segs, ticks_per_step, total_steps)

    # Tick 12 / ticks_per_step=12 → step 1
    step = 12 // ticks_per_step
    state = grid.get((0, step))
    assert state is not None, f"No grid state at step {step}"
    keyon, ms2_note, inst, vol, retrigger = state[:5]
    assert keyon == 1,     f"Expected keyon=1 at retrigger step, got {keyon}"
    assert retrigger,      "Expected retrigger=True at same-tick keyoff+keyon step"


def test_step_grid_no_retrigger_normal_separate_ticks():
    """When keyoff and keyon are on different steps, retrigger must be False."""
    rows = [
        (0,  0, 1, _FNUM, _BLOCK, _INST, _VOL),
        (12, 0, 0, _FNUM, _BLOCK, _INST, _VOL),   # keyoff at step 1
        (24, 0, 1, _FNUM, _BLOCK, _INST, _VOL),   # keyon  at step 2
    ]
    path = _write_trace(rows)
    try:
        segs = _build_segments(path)
    finally:
        os.unlink(path)

    ticks_per_step = TICKS_PER_STEP_75BPM
    total_steps = 2 * STEPS_PER_PATTERN
    grid = _build_step_grid(segs, ticks_per_step, total_steps)

    step = 24 // ticks_per_step   # step 2
    state = grid.get((0, step))
    assert state is not None
    keyon, ms2_note, inst, vol, retrigger = state[:5]
    assert keyon == 1
    assert not retrigger, "No retrigger expected when keyoff and keyon are on different steps"


# ---------------------------------------------------------------------------
# End-to-end test: segments_to_patterns() must emit NOTE_ON for retrigger
# ---------------------------------------------------------------------------

def test_segments_to_patterns_retrigger_emits_note_on():
    """Full pipeline: same-tick keyoff→keyon must produce a NOTE_ON event at
    the retrigger step, not a blank (which would be a tie in MS²)."""
    rows = [
        (0,  0, 1, _FNUM, _BLOCK, _INST, _VOL),
        (12, 0, 0, _FNUM, _BLOCK, _INST, _VOL),
        (12, 0, 1, _FNUM, _BLOCK, _INST, _VOL),
    ]
    path = _write_trace(rows)
    try:
        segs = _build_segments(path)
    finally:
        os.unlink(path)

    ticks_per_step = TICKS_PER_STEP_75BPM
    n_patterns    = 2
    n_channels    = 1
    n_instruments = 15

    channel_event_lists = segments_to_patterns(
        segs, ticks_per_step, n_patterns, n_channels, n_instruments
    )

    # Collect all NOTE_ON (0x00 ...) events across all patterns for channel 0.
    note_on_steps: list[int] = []
    for pat_idx, pat in enumerate(channel_event_lists):
        for step_in_pat, evts in pat[0]:    # ch=0
            for evt in evts:
                if evt[0] == 0x00:          # NOTE_ON opcode
                    note_on_steps.append(pat_idx * STEPS_PER_PATTERN + step_in_pat)

    # We expect two NOTE_ON events: one at step 0 (first note) and one at step 1
    # (retrigger from same-tick keyoff→keyon).
    assert len(note_on_steps) >= 2, (
        f"Expected at least 2 NOTE_ON events (initial + retrigger), "
        f"got {len(note_on_steps)} at steps {note_on_steps}"
    )
    assert 0 in note_on_steps, "Expected NOTE_ON at step 0 (initial note)"
    assert 1 in note_on_steps, (
        f"Expected NOTE_ON at step 1 (retrigger), got NOTE_ONs at steps {note_on_steps}"
    )


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

_TESTS = [
    test_build_segments_no_retrigger_normal_sequence,
    test_build_segments_hidden_retrigger_same_tick,
    test_build_segments_inst_vol_same_tick_not_affected,
    test_step_grid_retrigger_set_for_same_step_keyoff_keyon,
    test_step_grid_no_retrigger_normal_separate_ticks,
    test_segments_to_patterns_retrigger_emits_note_on,
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
            failed += 1
    print(f'\n{passed} passed, {failed} failed out of {passed + failed} tests.')
    sys.exit(0 if failed == 0 else 1)
