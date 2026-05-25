from __future__ import annotations

from types import SimpleNamespace

from vgm2mml_grid import NUM_CH, build_mgsdrvmml_file


def _empty_segments() -> dict:
    return {ch: [] for ch in range(NUM_CH)}


def _seg(
    *,
    ch: int,
    keyon: int,
    tick_start: int,
    tick_end: int,
    inst: int = 1,
    vol: int = 0,
    fnum: int = 172,
    block: int = 3,
    min_l: int = 4,
) -> SimpleNamespace:
    return SimpleNamespace(
        ch=ch,
        keyon=keyon,
        tick_start=tick_start,
        tick_end=tick_end,
        fnum=fnum,
        block=block,
        inst=inst,
        vol=vol,
        at_token='',
        is_legato=0,
        ev_type='instVol',
        min_l=min_l,
    )


def test_build_mgsdrvmml_file_uses_segments_to_patterns_timing_and_pattern_output():
    segs = _empty_segments()
    segs[0].append(_seg(ch=0, keyon=1, tick_start=0, tick_end=32, inst=1, min_l=4))
    segs[0].append(_seg(ch=0, keyon=0, tick_start=32, tick_end=40, inst=1, min_l=4))

    mml = build_mgsdrvmml_file(
        segments=segs,
        voice_csv_path=None,
        stem='unit',
        bpm=120,
    )

    assert '; === Pattern 0 ===' in mml
    assert '\n9 ' in mml
    assert 'c2 r2' in mml
    assert '@1' in mml
    assert 'v15' in mml
    assert '\na ' in mml and 'r1' in mml
    assert '\nf r1\n' in mml


def test_build_mgsdrvmml_file_emits_user_patch_and_rhythm_merge():
    segs = _empty_segments()
    segs[0].append(_seg(ch=0, keyon=1, tick_start=0, tick_end=4, inst=0, min_l=4))
    segs[0].append(_seg(ch=0, keyon=0, tick_start=4, tick_end=8, inst=0, min_l=4))
    segs[9].append(_seg(ch=9, keyon=1, tick_start=0, tick_end=4, inst=15, min_l=4))
    segs[9].append(_seg(ch=9, keyon=0, tick_start=4, tick_end=8, inst=15, min_l=4))
    segs[10].append(_seg(ch=10, keyon=1, tick_start=0, tick_end=4, inst=16, min_l=4))
    segs[10].append(_seg(ch=10, keyon=0, tick_start=4, tick_end=8, inst=16, min_l=4))

    mml = build_mgsdrvmml_file(
        segments=segs,
        voice_csv_path=None,
        stem='unit',
        bpm=120,
    )

    assert '@v15 = {' in mml
    assert '\n9 ' in mml
    assert 'c16' in mml
    assert '\nf bs16' in mml


def test_build_mgsdrvmml_file_tempo_derived_from_ticks_per_step():
    """#tempo は bpm_detected ではなく ticks_per_step から逆算した実効BPMになること"""
    segs = _empty_segments()
    segs[0].append(_seg(ch=0, keyon=1, tick_start=0, tick_end=2, inst=1, min_l=2))
    segs[0].append(_seg(ch=0, keyon=0, tick_start=2, tick_end=4, inst=1, min_l=2))

    mml = build_mgsdrvmml_file(
        segments=segs,
        voice_csv_path=None,
        stem='unit',
        bpm=499,
    )

    assert '#tempo 450' in mml
    assert '#tempo 499' not in mml


def test_build_mgsdrvmml_file_keeps_direct_at_token_mapping_per_channel():
    segs = _empty_segments()
    segs[0].append(_seg(ch=0, keyon=1, tick_start=0, tick_end=16, inst=1, min_l=4))
    segs[0].append(_seg(ch=0, keyon=0, tick_start=16, tick_end=20, inst=1, min_l=4))
    segs[1].append(_seg(ch=1, keyon=1, tick_start=0, tick_end=16, inst=2, min_l=4))
    segs[1].append(_seg(ch=1, keyon=0, tick_start=16, tick_end=20, inst=2, min_l=4))

    mml = build_mgsdrvmml_file(
        segments=segs,
        voice_csv_path=None,
        stem='unit',
        bpm=120,
    )

    line9 = next(line for line in mml.splitlines() if line.startswith('9 '))
    linea = next(line for line in mml.splitlines() if line.startswith('a '))

    assert '@1' in line9
    assert '@2' in linea
