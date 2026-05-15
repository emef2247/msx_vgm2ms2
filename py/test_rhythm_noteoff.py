from __future__ import annotations

import os
import sys
from types import SimpleNamespace

_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _this_dir)

from ms2_writer import NUM_CH, STEPS_PER_PATTERN, _build_step_grid, segments_to_patterns


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


def _collect_note_steps(channel_event_lists: list[list], ch: int) -> tuple[list[int], list[int]]:
    note_on_steps: list[int] = []
    note_off_steps: list[int] = []
    for pat_idx, pat in enumerate(channel_event_lists):
        for step_in_pat, evts in pat[ch]:
            abs_step = pat_idx * STEPS_PER_PATTERN + step_in_pat
            for evt in evts:
                if evt[0] == 0x00:
                    note_on_steps.append(abs_step)
                elif evt[0] == 0x01:
                    note_off_steps.append(abs_step)
    return note_on_steps, note_off_steps


def _drop_initial_note_off(note_off_steps: list[int]) -> list[int]:
    return [step for step in note_off_steps if step != 0]


def test_single_rhythm_segment_places_note_on_and_note_off():
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
    assert _drop_initial_note_off(note_offs) == [8]


def test_consecutive_rhythm_segments_retrigger_without_intermediate_note_off():
    segs = _empty_segments()
    segs[9].append(_make_rhythm_segment(2, 5))
    segs[9].append(_make_rhythm_segment(5, 7))

    channel_event_lists = segments_to_patterns(
        segs,
        ticks_per_step=1,
        n_patterns=1,
        n_channels=NUM_CH,
        n_instruments=20,
    )
    note_ons, note_offs = _collect_note_steps(channel_event_lists, 9)
    assert note_ons == [2, 5]
    assert _drop_initial_note_off(note_offs) == [7]


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
    assert _drop_initial_note_off(note_offs) == [5, 9]


if __name__ == '__main__':
    test_single_rhythm_segment_places_note_on_and_note_off()
    test_consecutive_rhythm_segments_retrigger_without_intermediate_note_off()
    test_non_adjacent_rhythm_segments_place_note_off_between_hits()
    print('rhythm note-off tests passed')
