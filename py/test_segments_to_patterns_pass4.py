from __future__ import annotations

from types import SimpleNamespace

from ms2_writer import segments_to_patterns


def _seg(**kwargs):
    base = {
        'tick_start': 0,
        'keyon': 1,
        'fnum': 172,
        'block': 3,
        'inst': 1,
        'vol': 2,
        'ioi': 1,
        'l': 1,
        'fl': 1,
        'vl': 1,
        'is_legate': 0,
        'ev_type': 'keyBlk',
        'at_token': '',
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _events_by_step(events, ch=0):
    out = {}
    for pat_idx, pat in enumerate(events):
        for step_in_pat, evts in pat[ch]:
            abs_step = pat_idx * 16 + step_in_pat
            out.setdefault(abs_step, [])
            out[abs_step].extend(evts)
    return out


def test_ioi1_emits_note_inst_vol_in_order():
    segs = {0: [_seg(tick_start=0, ioi=1, l=10, fl=10, vl=10)], 1: []}
    events = segments_to_patterns(segs, ticks_per_step=1, n_patterns=1, n_channels=1, n_instruments=15)
    by_step = _events_by_step(events)
    assert [e[0] for e in by_step[0]] == [0x00, 0x04, 0x05]


def test_legato_emits_legato_opcode():
    segs = {0: [_seg(tick_start=0, ioi=1, is_legate=1)], 1: []}
    events = segments_to_patterns(segs, ticks_per_step=1, n_patterns=1, n_channels=1, n_instruments=15)
    by_step = _events_by_step(events)
    assert by_step[0][0][0] == 0x02


def test_keyon_with_silent_volume_emits_note_off():
    segs = {0: [_seg(tick_start=0, keyon=1, ms2_vol=63, vol=0)], 1: []}
    events = segments_to_patterns(segs, ticks_per_step=1, n_patterns=1, n_channels=1, n_instruments=15)
    by_step = _events_by_step(events)
    assert [e[0] for e in by_step[0]] == [0x01]


def test_explicit_keyoff_emits_note_off():
    segs = {
        0: [
            _seg(tick_start=0, keyon=1, ioi=1),
            _seg(tick_start=4, keyon=0, ioi=0, l=4, fl=4, vl=4),
        ],
    }
    events = segments_to_patterns(segs, ticks_per_step=1, n_patterns=1, n_channels=1, n_instruments=15)
    by_step = _events_by_step(events)
    assert 4 in by_step
    assert any(e[0] == 0x01 for e in by_step[4])


def test_ioi0_l_equals_fl_emits_note():
    segs = {
        0: [
            _seg(tick_start=0, keyon=1, ioi=1, l=4, fl=4, vl=4),
            _seg(tick_start=2, keyon=1, ioi=0, l=5, fl=5, vl=7),
        ],
    }
    events = segments_to_patterns(segs, ticks_per_step=1, n_patterns=1, n_channels=1, n_instruments=15)
    by_step = _events_by_step(events)
    assert any(e[0] in (0x00, 0x02) for e in by_step[2])


def test_ioi0_l_equals_vl_emits_vol_only():
    segs = {
        0: [
            _seg(tick_start=0, keyon=1, ioi=1, l=4, fl=4, vl=4),
            _seg(tick_start=2, keyon=1, ioi=0, l=6, fl=7, vl=6, vol=4),
        ],
    }
    events = segments_to_patterns(segs, ticks_per_step=1, n_patterns=1, n_channels=1, n_instruments=15)
    by_step = _events_by_step(events)
    opcodes = [e[0] for e in by_step[2]]
    assert 0x05 in opcodes
    assert 0x00 not in opcodes and 0x02 not in opcodes


def test_rhythy_segment_is_treated_as_rhythm():
    segs = {0: [_seg(tick_start=0, ev_type='rhythy', fnum=0, block=0)], 1: []}
    events = segments_to_patterns(segs, ticks_per_step=1, n_patterns=1, n_channels=1, n_instruments=15)
    by_step = _events_by_step(events)
    assert any(e[0] == 0x00 for e in by_step[0])
