"""test_beat_based_ms2.py – Unit tests for the beat-based MS2 note placement.

Validates that after _assign_beat_fields() sets beat_start / ms2_len on every
segment, _build_step_grid() and segments_to_patterns() place NOTE_ON and
NOTE_OFF events at the correct pattern / step positions.

The key requirement:
  abs_step = seg.beat_start
  pat      = abs_step // 16
  step     = abs_step % 16

  NOTE_OFF appears at abs_step = beat_start + ms2_len (across pattern boundary
  when necessary).

Run directly:  python3 py/test_beat_based_ms2.py
Run via pytest: python -m pytest py/test_beat_based_ms2.py -v
"""

from __future__ import annotations

import os
import sys

_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _this_dir)

from opll_mml import Segment, NUM_CH
from ms2_writer import (
    _assign_beat_fields,
    _build_step_grid,
    segments_to_patterns,
    build_ms2_file,
    STEPS_PER_PATTERN,
    TICKS_PER_STEP_75BPM,
)
from ms2_validate import validate_ms2_bytes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A fnum/block pair that yields a valid MS2 note (approx C3).
_FNUM  = 172
_BLOCK = 3
_INST  = 1
_VOL   = 0


def _make_seg(tick_start: int, tick_end: int, keyon: int,
              inst: int = _INST, vol: int = _VOL,
              fnum: int = _FNUM, block: int = _BLOCK) -> Segment:
    seg = Segment(tick_start, tick_end, 0, keyon=keyon,
                  fnum=fnum, block=block, inst=inst, vol=vol, sus=0)
    seg.at_token = f'@{inst}'
    return seg


def _empty_segs() -> dict:
    return {ch: [] for ch in range(NUM_CH)}


def _collect_note_ons(channel_event_lists: list, ch: int) -> list[int]:
    """Return absolute step numbers of all NOTE_ON (opcode 0x00) events for ch."""
    result = []
    for pat_idx, pat in enumerate(channel_event_lists):
        for step_in_pat, evts in pat[ch]:
            for evt in evts:
                if evt[0] == 0x00:
                    result.append(pat_idx * STEPS_PER_PATTERN + step_in_pat)
    return result


def _collect_note_offs(channel_event_lists: list, ch: int) -> list[int]:
    """Return absolute step numbers of all NOTE_OFF (opcode 0x01) events for ch."""
    result = []
    for pat_idx, pat in enumerate(channel_event_lists):
        for step_in_pat, evts in pat[ch]:
            for evt in evts:
                if evt[0] == 0x01:
                    result.append(pat_idx * STEPS_PER_PATTERN + step_in_pat)
    return result


# ---------------------------------------------------------------------------
# Tests for _assign_beat_fields
# ---------------------------------------------------------------------------

def test_assign_beat_fields_basic():
    """beat_start and ms2_len are computed correctly from tick fields."""
    tps = TICKS_PER_STEP_75BPM  # 12

    segs = _empty_segs()
    segs[0].append(_make_seg(tick_start=0,  tick_end=12, keyon=1))   # step 0, len 1
    segs[0].append(_make_seg(tick_start=24, tick_end=48, keyon=1))   # step 2, len 2

    _assign_beat_fields(segs, tps)

    s0, s1 = segs[0]
    assert s0.beat_start == 0, f"Expected beat_start=0, got {s0.beat_start}"
    assert s0.ms2_len    == 1, f"Expected ms2_len=1, got {s0.ms2_len}"
    assert s1.beat_start == 2, f"Expected beat_start=2, got {s1.beat_start}"
    assert s1.ms2_len    == 2, f"Expected ms2_len=2, got {s1.ms2_len}"


def test_assign_beat_fields_min_length_one():
    """Zero-length segments get ms2_len=1 (minimum)."""
    tps = TICKS_PER_STEP_75BPM

    segs = _empty_segs()
    segs[0].append(_make_seg(tick_start=12, tick_end=12, keyon=0))  # zero-length keyoff

    _assign_beat_fields(segs, tps)

    assert segs[0][0].ms2_len >= 1, "ms2_len must be at least 1"


# ---------------------------------------------------------------------------
# Tests for _build_step_grid (beat_start used directly)
# ---------------------------------------------------------------------------

def test_build_step_grid_uses_beat_start():
    """When beat_start is set, _build_step_grid places the note at that step."""
    tps = TICKS_PER_STEP_75BPM
    total_steps = 2 * STEPS_PER_PATTERN

    segs = _empty_segs()
    # Place a note at step 5 (beat_start=5) via tick 60 (= 5 * 12)
    segs[0].append(_make_seg(tick_start=60, tick_end=72, keyon=1))

    _assign_beat_fields(segs, tps)

    grid = _build_step_grid(segs, tps, total_steps)

    # Step 5 should be keyon
    state = grid.get((0, 5))
    assert state is not None
    keyon = state[0]
    assert keyon == 1, f"Expected keyon=1 at step 5, got {keyon}"

    # Step 4 should not be keyon
    state4 = grid.get((0, 4))
    assert state4 is not None
    assert state4[0] == 0, f"Expected keyon=0 at step 4, got {state4[0]}"


def test_build_step_grid_note_at_beat_start_cross_pattern():
    """Note placed in pattern 1 via beat_start >= STEPS_PER_PATTERN."""
    tps = TICKS_PER_STEP_75BPM
    total_steps = 3 * STEPS_PER_PATTERN

    segs = _empty_segs()
    # Note at absolute step 17 = pattern 1, step 1
    segs[0].append(_make_seg(tick_start=17 * tps, tick_end=(17 + 2) * tps, keyon=1))

    _assign_beat_fields(segs, tps)

    grid = _build_step_grid(segs, tps, total_steps)

    state = grid.get((0, 17))
    assert state is not None and state[0] == 1, \
        f"Expected keyon=1 at absolute step 17, got {state}"

    state16 = grid.get((0, 16))
    assert state16 is not None and state16[0] == 0, \
        f"Expected keyon=0 at step 16 (before note), got {state16}"


# ---------------------------------------------------------------------------
# Tests for segments_to_patterns (NOTE_ON / NOTE_OFF positions)
# ---------------------------------------------------------------------------

def test_note_on_at_correct_step():
    """NOTE_ON event appears at the beat_start step of the keyon segment."""
    tps = TICKS_PER_STEP_75BPM

    segs = _empty_segs()
    segs[0].append(_make_seg(tick_start=0, tick_end=12, keyon=1))

    _assign_beat_fields(segs, tps)

    events = segments_to_patterns(segs, tps, n_patterns=1,
                                  n_channels=1, n_instruments=15)
    note_ons = _collect_note_ons(events, ch=0)
    assert 0 in note_ons, f"Expected NOTE_ON at step 0, got {note_ons}"


def test_two_sequential_notes_both_get_note_on():
    """Two notes on different steps each produce a NOTE_ON event."""
    tps = TICKS_PER_STEP_75BPM

    segs = _empty_segs()
    segs[0].append(_make_seg(tick_start=0,      tick_end=12,     keyon=1))
    segs[0].append(_make_seg(tick_start=12,     tick_end=12,     keyon=0))  # keyoff
    segs[0].append(_make_seg(tick_start=24,     tick_end=36,     keyon=1))

    _assign_beat_fields(segs, tps)

    events = segments_to_patterns(segs, tps, n_patterns=1,
                                  n_channels=1, n_instruments=15)
    note_ons = _collect_note_ons(events, ch=0)

    assert 0 in note_ons, f"Expected NOTE_ON at step 0, got {note_ons}"
    assert 2 in note_ons, f"Expected NOTE_ON at step 2, got {note_ons}"


def test_note_off_after_note_on():
    """A NOTE_OFF event must appear after the NOTE_ON."""
    tps = TICKS_PER_STEP_75BPM

    segs = _empty_segs()
    segs[0].append(_make_seg(tick_start=0,  tick_end=12, keyon=1))
    segs[0].append(_make_seg(tick_start=12, tick_end=12, keyon=0))  # keyoff

    _assign_beat_fields(segs, tps)

    events = segments_to_patterns(segs, tps, n_patterns=1,
                                  n_channels=1, n_instruments=15)
    note_ons  = _collect_note_ons(events,  ch=0)
    note_offs = _collect_note_offs(events, ch=0)

    assert note_ons,  "Expected at least one NOTE_ON"
    assert note_offs, "Expected at least one NOTE_OFF"
    assert min(note_ons) < min(note_offs), \
        f"NOTE_ON ({note_ons}) should precede NOTE_OFF ({note_offs})"


def test_note_on_at_correct_step_pattern1():
    """NOTE_ON event in pattern 1 is placed at the right step within that pattern."""
    tps = TICKS_PER_STEP_75BPM

    # Note starts at absolute step 17 = pattern 1, step 1
    segs = _empty_segs()
    segs[0].append(_make_seg(tick_start=17 * tps, tick_end=(17 + 4) * tps, keyon=1))

    _assign_beat_fields(segs, tps)

    events = segments_to_patterns(segs, tps, n_patterns=2,
                                  n_channels=1, n_instruments=15)
    note_ons = _collect_note_ons(events, ch=0)

    assert 17 in note_ons, (
        f"Expected NOTE_ON at absolute step 17 (pat 1, step 1), got {note_ons}"
    )
    assert 0 not in note_ons, (
        f"Step 0 should have no NOTE_ON when note starts at step 17, got {note_ons}"
    )


def test_retrigger_produces_two_note_ons():
    """Same-tick keyoff→keyon (retrigger) must produce two NOTE_ON events."""
    tps = TICKS_PER_STEP_75BPM

    segs = _empty_segs()
    segs[0].append(_make_seg(tick_start=0,  tick_end=12, keyon=1))
    segs[0].append(_make_seg(tick_start=12, tick_end=12, keyon=0))  # zero-length keyoff
    segs[0].append(_make_seg(tick_start=12, tick_end=24, keyon=1))  # retrigger at step 1

    _assign_beat_fields(segs, tps)

    events = segments_to_patterns(segs, tps, n_patterns=1,
                                  n_channels=1, n_instruments=15)
    note_ons = _collect_note_ons(events, ch=0)

    assert 0 in note_ons, f"Expected NOTE_ON at step 0, got {note_ons}"
    assert 1 in note_ons, f"Expected NOTE_ON at step 1 (retrigger), got {note_ons}"


# ---------------------------------------------------------------------------
# End-to-end: build_ms2_file validates note presence
# ---------------------------------------------------------------------------

def _write_trace(rows: list[tuple], *, with_sus: bool = True) -> str:
    import tempfile
    fh = tempfile.NamedTemporaryFile(
        mode='w', suffix='.csv', delete=False, newline='\n')
    fh.write('#type,time,ch,ticks,keyon,fnum,block,inst,vol,sus\n')
    for tick, ch, keyon, fnum, block, inst, vol in rows:
        sus = 0
        fh.write(f'N,0,{ch},{tick},{keyon},{fnum},{block},{inst},{vol},{sus}\n')
    fh.close()
    return fh.name


def test_build_ms2_file_notes_visible():
    """build_ms2_file must produce a valid MS2 with NOTE_ON events for a
    simple two-note sequence, demonstrating that beat-based placement works."""
    from opll_mml import _build_segments

    rows = [
        (0,  0, 1, _FNUM, _BLOCK, _INST, _VOL),   # tick 0: keyon ch0
        (12, 0, 0, _FNUM, _BLOCK, _INST, _VOL),   # tick 12: keyoff
        (24, 0, 1, _FNUM, _BLOCK, _INST, _VOL),   # tick 24: second keyon
    ]
    trace_path = _write_trace(rows)
    try:
        segs = _build_segments(trace_path)
    finally:
        os.unlink(trace_path)

    ms2_bytes = build_ms2_file(segs, voice_csv_path=None, stem='test', bpm=75)

    # Structural validity
    errors = validate_ms2_bytes(ms2_bytes)
    assert not errors, f"MS2 validation errors: {errors}"

    # Must contain at least one NOTE_ON opcode (0x04 is INST, 0x00 is NOTE_ON)
    note_on_count = 0
    i = 0
    while i < len(ms2_bytes):
        if ms2_bytes[i] == 0x00 and i + 1 < len(ms2_bytes):
            note_on_count += 1
        i += 1
    assert note_on_count >= 2, (
        f"Expected at least 2 NOTE_ON (0x00) opcodes in MS2 bytes, "
        f"found {note_on_count}"
    )


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

_TESTS = [
    test_assign_beat_fields_basic,
    test_assign_beat_fields_min_length_one,
    test_build_step_grid_uses_beat_start,
    test_build_step_grid_note_at_beat_start_cross_pattern,
    test_note_on_at_correct_step,
    test_two_sequential_notes_both_get_note_on,
    test_note_off_after_note_on,
    test_note_on_at_correct_step_pattern1,
    test_retrigger_produces_two_note_ons,
    test_build_ms2_file_notes_visible,
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
    import sys as _sys
    _sys.exit(0 if failed == 0 else 1)
