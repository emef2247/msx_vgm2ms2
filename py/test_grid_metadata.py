from __future__ import annotations

from types import SimpleNamespace

from ms2_writer import _build_step_grid
from segment_utils import attach_grid_info
from vgm2mml_grid import NUM_CH, build_mgsdrvmml_file


def _empty_segments() -> dict:
    return {ch: [] for ch in range(NUM_CH)}


def _seg(*, ch: int, tick_start: int, tick_end: int, keyon: int = 1, ev_type: str = 'note'):
    return SimpleNamespace(
        ch=ch,
        keyon=keyon,
        tick_start=tick_start,
        tick_end=tick_end,
        fnum=172,
        block=3,
        inst=1,
        vol=0,
        at_token='',
        is_legato=0,
        ev_type=ev_type,
        min_l=4,
    )


def test_attach_grid_info_populates_segments_for_all_channels():
    melody = _seg(ch=0, tick_start=5, tick_end=9)
    rhythm = _seg(ch=NUM_CH + 3, tick_start=8, tick_end=12, ev_type='rhythm')
    segments = {0: [melody], NUM_CH + 3: [rhythm]}

    attach_grid_info(segments, 4)

    assert melody.grid_index == 1
    assert melody.grid_start_tick == 4
    assert melody.grid_end_tick == 8
    assert melody.grid_offset_start == 1
    assert melody.grid_offset_end == 5
    assert melody.ticks_per_grid == 4

    assert rhythm.grid_index == 2
    assert rhythm.grid_start_tick == 8
    assert rhythm.grid_end_tick == 12


def test_build_step_grid_prefers_grid_index_and_keeps_fallback():
    preferred = _seg(ch=0, tick_start=4, tick_end=8)
    preferred.grid_index = 2
    fallback = _seg(ch=1, tick_start=4, tick_end=8)
    segments = {0: [preferred], 1: [fallback]}

    grid = _build_step_grid(segments, 4, 8)

    assert grid[(0, 2)][0] == 1
    assert grid[(0, 1)][0] == 0
    assert grid[(1, 1)][0] == 1


def test_build_mgsdrvmml_file_attaches_grid_metadata():
    segs = _empty_segments()
    segs[0].append(_seg(ch=0, tick_start=0, tick_end=32))
    segs[0].append(_seg(ch=0, tick_start=32, tick_end=40, keyon=0))

    build_mgsdrvmml_file(
        segments=segs,
        voice_csv_path=None,
        stem='unit',
        bpm=120,
    )

    assert segs[0][0].grid_index == 0
    assert segs[0][0].grid_start_tick == 0
    assert segs[0][0].grid_end_tick > segs[0][0].grid_start_tick
    assert segs[0][1].grid_index == 8
    assert segs[0][1].ticks_per_grid == 4
