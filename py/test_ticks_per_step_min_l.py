from __future__ import annotations

import struct
from types import SimpleNamespace

from ms2_dump import iter_chunks
from ms2_writer import NUM_CH, _FREQ_NTSC, build_ms2_file


def _empty_segments() -> dict:
    return {ch: [] for ch in range(NUM_CH)}


def _make_note_seg(tick_start: int, tick_end: int, min_l=None):
    seg = SimpleNamespace(
        keyon=1,
        tick_start=tick_start,
        tick_end=tick_end,
        fnum=172,
        block=3,
        inst=1,
        vol=60,
        at_token='',
        is_legato=0,
        ev_type='instVol',
    )
    if min_l is not None:
        seg.min_l = min_l
    return seg


def _build_ms2(segments: dict, bpm: int = 120, debug: bool = False) -> bytes:
    return build_ms2_file(
        segments=segments,
        voice_csv_path=None,
        stem='test',
        bpm=bpm,
        debug=debug,
    )


def _legacy_ticks_per_step(bpm: int) -> int:
    # Legacy grid: 1 step = 1/16 note => freq_hz * 15 / bpm ticks per 1/16-note step
    # (15 = 240/16, where 240 is quarter-note ticks in the original timer model).
    return max(1, round(_FREQ_NTSC * 15 / bpm))


def _legacy_timer_divider(bpm: int) -> int:
    # Legacy timer formula: freq_hz * 3840 / bpm (3840 = 256 * 15).
    return int(_FREQ_NTSC * 3840 // bpm)


def _read_song_settings_timer_divider(ms2_bytes: bytes) -> int:
    for _offset, ctype, _version, cdata in iter_chunks(ms2_bytes):
        if ctype == 1:
            return struct.unpack_from('<H', cdata, 68)[0]
    raise AssertionError('song settings chunk not found')


def _read_pattern_count(ms2_bytes: bytes) -> int:
    for _offset, ctype, _version, cdata in iter_chunks(ms2_bytes):
        if ctype == 6 and len(cdata) >= 2:
            return struct.unpack_from('<H', cdata, 0)[0]
    raise AssertionError('patterns chunk not found')


def test_min_l_5_point_4_uses_min_l_based_ticks_and_timer():
    segs = _empty_segments()
    segs[0].append(_make_note_seg(0, 50, min_l=5.4))

    ms2_bytes = _build_ms2(segs, bpm=120)
    timer_divider = _read_song_settings_timer_divider(ms2_bytes)

    assert timer_divider == 1280  # 256 * int(5.4)


def test_min_l_none_falls_back_to_legacy_values():
    segs = _empty_segments()
    segs[0].append(_make_note_seg(0, 50, min_l=None))

    ms2_bytes = _build_ms2(segs, bpm=120)
    timer_divider = _read_song_settings_timer_divider(ms2_bytes)

    assert timer_divider == _legacy_timer_divider(120)


def test_min_l_is_clipped_by_legacy_16th_grid():
    segs = _empty_segments()
    segs[0].append(_make_note_seg(0, 50, min_l=20.0))

    ms2_bytes = _build_ms2(segs, bpm=120)
    timer_divider = _read_song_settings_timer_divider(ms2_bytes)

    assert timer_divider == 256 * _legacy_ticks_per_step(120)


def test_n_patterns_stays_small_for_short_song_with_smaller_ticks_per_step():
    segs = _empty_segments()
    segs[0].append(_make_note_seg(0, 50, min_l=5.4))

    ms2_bytes = _build_ms2(segs, bpm=120)
    n_patterns = _read_pattern_count(ms2_bytes)

    assert n_patterns == 1


def test_debug_prints_min_l_vs_legacy_comparison(capsys):
    segs = _empty_segments()
    segs[0].append(_make_note_seg(0, 50, min_l=5.4))

    _build_ms2(segs, bpm=120, debug=True)
    out = capsys.readouterr().out

    assert '[ticks_per_step]' in out
    assert 'min_l=5.4' in out
    assert f'legacy={_legacy_ticks_per_step(120)}' in out
    assert 'ticks_per_step=5' in out
    assert 'timer_divider=1280' in out
