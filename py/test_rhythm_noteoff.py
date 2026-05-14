from __future__ import annotations

import os
import sys
from types import SimpleNamespace

_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _this_dir)

from ms2_writer import NUM_CH, STEPS_PER_PATTERN, _build_step_grid, segments_to_patterns

NOTE_ON_OPCODE = 0x00
NOTE_OFF_OPCODE = 0x01


def _empty_segments() -> dict:
    return {ch: [] for ch in range(NUM_CH)}


def _make_rhythm_segment(tick_start: int, tick_end: int, keyon: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        ev_type='rhythm',
        keyon=keyon,
        tick_start=tick_start,
        tick_end=tick_end,
        fnum=0,
        block=0,
        inst=16,
        vol=1,
        at_token='',
        is_legato=0,
    )


def _make_note_segment(tick_start: int, tick_end: int, keyon: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        ev_type='note',
        keyon=keyon,
        tick_start=tick_start,
        tick_end=tick_end,
        fnum=172,
        block=3,
        inst=1,
        vol=1,
        at_token='',
        is_legato=0,
    )


def _collect_note_steps(channel_event_lists: list[list], ch: int) -> tuple[list[int], list[int]]:
    note_on_steps: list[int] = []
    note_off_steps: list[int] = []
    for pat_idx, pat in enumerate(channel_event_lists):
        for step_in_pat, evts in pat[ch]:
            abs_step = pat_idx * STEPS_PER_PATTERN + step_in_pat
            for evt in evts:
                if evt[0] == NOTE_ON_OPCODE:
                    note_on_steps.append(abs_step)
                elif evt[0] == NOTE_OFF_OPCODE:
                    note_off_steps.append(abs_step)
    return note_on_steps, note_off_steps


def _collect_step_events(channel_event_lists: list[list], ch: int) -> dict[int, list[int]]:
    step_events: dict[int, list[int]] = {}
    for pat_idx, pat in enumerate(channel_event_lists):
        for step_in_pat, evts in pat[ch]:
            abs_step = pat_idx * STEPS_PER_PATTERN + step_in_pat
            step_events.setdefault(abs_step, []).extend(evt[0] for evt in evts)
    return step_events


def _drop_initial_note_off(note_off_steps: list[int]) -> list[int]:
    return sorted(step for step in note_off_steps if step != 0)


def test_note_off_inserted_before_note_on_when_prev_step_empty():
    segs = _empty_segments()
    segs[9].append(_make_rhythm_segment(4, 8))

    grid = _build_step_grid(segs, ticks_per_step=1, total_steps=16)
    assert grid[(9, 4)][0] == 1
    assert grid[(9, 8)][0] == 0

    channel_event_lists = segments_to_patterns(
        segs,
        ticks_per_step=1,
        n_patterns=1,
        n_channels=NUM_CH,
        n_instruments=20,
    )
    note_ons, note_offs = _collect_note_steps(channel_event_lists, 9)
    assert note_ons == [4]
    assert _drop_initial_note_off(note_offs) == [3, 8]


def test_note_off_not_inserted_when_prev_step_occupied():
    segs = _empty_segments()
    segs[9].append(_make_rhythm_segment(3, 4))
    segs[9].append(_make_rhythm_segment(4, 7))

    channel_event_lists = segments_to_patterns(
        segs,
        ticks_per_step=1,
        n_patterns=1,
        n_channels=NUM_CH,
        n_instruments=20,
    )
    note_ons, note_offs = _collect_note_steps(channel_event_lists, 9)
    step_events = _collect_step_events(channel_event_lists, 9)
    assert note_ons == [3, 4]
    assert step_events[3].count(NOTE_OFF_OPCODE) == 0
    assert _drop_initial_note_off(note_offs) == [2, 7]


def test_non_adjacent_rhythm_segments_place_note_off_between_hits():
    segs = _empty_segments()
    segs[9].append(_make_rhythm_segment(2, 5))
    segs[9].append(_make_rhythm_segment(7, 9))

    channel_event_lists = segments_to_patterns(
        segs,
        ticks_per_step=1,
        n_patterns=1,
        n_channels=NUM_CH,
        n_instruments=20,
    )
    note_ons, note_offs = _collect_note_steps(channel_event_lists, 9)
    assert note_ons == [2, 7]
    assert _drop_initial_note_off(note_offs) == [1, 5, 6, 9]


def test_note_off_not_inserted_at_step_0():
    segs = _empty_segments()
    segs[9].append(_make_rhythm_segment(0, 2))

    channel_event_lists = segments_to_patterns(
        segs,
        ticks_per_step=1,
        n_patterns=1,
        n_channels=NUM_CH,
        n_instruments=20,
    )
    note_ons, note_offs = _collect_note_steps(channel_event_lists, 9)
    assert note_ons == [0]
    assert note_offs == [2]


def test_note_off_inserted_cross_pattern_boundary():
    segs = _empty_segments()
    segs[9].append(_make_rhythm_segment(16, 18))

    channel_event_lists = segments_to_patterns(
        segs,
        ticks_per_step=1,
        n_patterns=2,
        n_channels=NUM_CH,
        n_instruments=20,
    )
    note_ons, note_offs = _collect_note_steps(channel_event_lists, 9)
    assert note_ons == [16]
    assert 15 in note_offs
    assert 18 in note_offs


def test_note_off_not_inserted_for_non_rhythm_channel():
    segs = _empty_segments()
    segs[0].append(_make_note_segment(4, 8))

    channel_event_lists = segments_to_patterns(
        segs,
        ticks_per_step=1,
        n_patterns=1,
        n_channels=NUM_CH,
        n_instruments=20,
    )
    note_ons, note_offs = _collect_note_steps(channel_event_lists, 0)
    step_events = _collect_step_events(channel_event_lists, 0)
    assert note_ons == [4]
    assert 3 not in note_offs
    assert step_events.get(3, []).count(NOTE_OFF_OPCODE) == 0


if __name__ == '__main__':
    test_note_off_inserted_before_note_on_when_prev_step_empty()
    test_note_off_not_inserted_when_prev_step_occupied()
    test_non_adjacent_rhythm_segments_place_note_off_between_hits()
    test_note_off_not_inserted_at_step_0()
    test_note_off_inserted_cross_pattern_boundary()
    test_note_off_not_inserted_for_non_rhythm_channel()
    print('rhythm note-off tests passed')
