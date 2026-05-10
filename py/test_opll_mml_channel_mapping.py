from __future__ import annotations

from types import SimpleNamespace

from opll_mml import _generate_mml_impl, _generate_mml_mgs_pct


class _Seg(SimpleNamespace):
    def mml_vol(self):
        return self.mml_volume


def _melody_seg(*, ticks: int = 0, beat_start: int = 0, beat_end: int = 4) -> _Seg:
    return _Seg(
        ticks=ticks,
        beat_start=beat_start,
        beat_end=beat_end,
        keyon=1,
        fnum=256,
        block=4,
        at_token='@1',
        mml_volume=10,
        is_ryt=0,
        bd=0, sd=0, tom=0, tc=0, hh=0,
    )


def _rhythm_seg(kind: str, *, ticks: int, beat_start: int, beat_end: int) -> _Seg:
    return _Seg(
        ticks=ticks,
        beat_start=beat_start,
        beat_end=beat_end,
        keyon=1,
        fnum=0,
        block=0,
        at_token='@16',
        mml_volume=15,
        is_ryt=1,
        bd=1 if kind == 'bd' else 0,
        sd=1 if kind == 'sd' else 0,
        tom=1 if kind == 'tom' else 0,
        tc=1 if kind == 'tc' else 0,
        hh=1 if kind == 'hh' else 0,
    )


def test_generate_mml_mgs_pct_uses_only_9_to_e_tracks_and_merges_rhythm():
    segments = {
        0: [_melody_seg()],
        9: [_rhythm_seg('bd', ticks=20, beat_start=4, beat_end=6)],
        10: [_rhythm_seg('sd', ticks=10, beat_start=2, beat_end=4)],
        17: [_melody_seg()],  # must be ignored
    }

    mml = _generate_mml_mgs_pct(segments, 'unit-test')

    assert '#alloc 9=' in mml
    assert '#alloc e=' in mml
    assert '#alloc f=' in mml
    assert '#alloc g=' not in mml
    assert 'f s%2 b%2' in mml
    assert mml.find(' s%2') < mml.find(' b%2')  # sorted by ticks (sd before bd)


def test_generate_mml_impl_includes_rhythm_track_f():
    segments = {
        0: [_melody_seg()],
        13: [_rhythm_seg('hh', ticks=0, beat_start=0, beat_end=3)],
    }

    mml = _generate_mml_impl(segments, 'unit-test', raw_ticks=False)

    assert '#alloc f=' in mml
    assert 'f h%3' in mml
