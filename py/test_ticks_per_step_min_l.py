from __future__ import annotations

import struct
from types import SimpleNamespace

from ms2_dump import iter_chunks
from ms2_writer import NUM_CH, STEPS_PER_PATTERN, _FREQ_NTSC, build_ms2_file, segments_to_patterns


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


def _make_keyoff_seg(tick_start: int, tick_end: int, min_l=None):
    seg = SimpleNamespace(
        keyon=0,
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


def _build_ms2(
    segments: dict,
    bpm: int = 120,
    debug: bool = False,
    ticks_per_step_override: int | None = None,
) -> bytes:
    return build_ms2_file(
        segments=segments,
        voice_csv_path=None,
        stem='test',
        bpm=bpm,
        debug=debug,
        ticks_per_step_override=ticks_per_step_override,
    )


def _legacy_ticks_per_step(bpm: int) -> int:
    # Legacy grid: 1 step = 1/16 note => freq_hz * 15 / bpm ticks per 1/16-note step
    # (15 = 240/16, where 240 is quarter-note ticks in the original timer model).
    return max(1, round(_FREQ_NTSC * 15 / bpm))


def _legacy_timer_divider(bpm: int) -> int:
    # Legacy timer formula: freq_hz * 3840 / bpm (3840 = 256 * 15).
    return int(_FREQ_NTSC * 3840 / bpm)


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


def _collect_ch_opcodes(channel_event_lists: list[list], ch: int) -> list[tuple[int, int]]:
    opcodes: list[tuple[int, int]] = []
    for pat_idx, pat in enumerate(channel_event_lists):
        for step_in_pat, evts in pat[ch]:
            abs_step = pat_idx * STEPS_PER_PATTERN + step_in_pat
            for evt in evts:
                opcodes.append((abs_step, evt[0]))
    return opcodes


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


def test_same_note_retrigger_emits_note_on_for_each_is_legato_zero_event():
    segs = _empty_segments()
    for tick in [2, 5, 7, 10, 12, 15, 17, 20]:
        segs[0].append(_make_note_seg(tick, tick + 1, min_l=2.4))
    segs[0].append(_make_keyoff_seg(22, 23, min_l=2.4))

    channel_event_lists = segments_to_patterns(
        segs,
        ticks_per_step=2,
        n_patterns=2,
        n_channels=NUM_CH,
        n_instruments=20,
    )
    note_ons = [step for step, opcode in _collect_ch_opcodes(channel_event_lists, 0) if opcode == 0x00]
    assert len(note_ons) == 8


def test_is_legato_one_event_emits_legato_instead_of_note_on():
    segs = _empty_segments()
    segs[0].append(_make_note_seg(2, 3, min_l=2.4))
    legato_seg = _make_note_seg(4, 5, min_l=2.4)
    legato_seg.block = 4
    legato_seg.is_legato = 1
    segs[0].append(legato_seg)
    segs[0].append(_make_keyoff_seg(6, 7, min_l=2.4))

    channel_event_lists = segments_to_patterns(
        segs,
        ticks_per_step=2,
        n_patterns=1,
        n_channels=NUM_CH,
        n_instruments=20,
    )
    ch0 = _collect_ch_opcodes(channel_event_lists, 0)
    note_ons = [step for step, opcode in ch0 if opcode == 0x00]
    legatos = [step for step, opcode in ch0 if opcode == 0x02]
    assert note_ons == [1]
    assert legatos == [2]


def test_ticks_per_step_override_uses_override_value_for_timer_divider():
    segs = _empty_segments()
    segs[0].append(_make_note_seg(0, 50, min_l=1.0))

    ms2_bytes = _build_ms2(segs, bpm=120, ticks_per_step_override=3)
    timer_divider = _read_song_settings_timer_divider(ms2_bytes)

    assert timer_divider == 768  # 256 * 3


def test_ticks_per_step_override_is_clamped_to_minimum_two():
    segs = _empty_segments()
    segs[0].append(_make_note_seg(0, 50, min_l=10.0))

    ms2_bytes = _build_ms2(segs, bpm=120, ticks_per_step_override=1)
    timer_divider = _read_song_settings_timer_divider(ms2_bytes)

    assert timer_divider == 512  # 256 * 2
