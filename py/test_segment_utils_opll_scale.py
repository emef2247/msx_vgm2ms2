from segment_utils import pass2_compute_onsets_and_ioi, pass3_merge_pitch_volume_wobble


def test_pass2_compute_onsets_and_ioi_uses_opll_scale_from_fnum_and_block():
    events = [
        {"#type": "instVol", "ch": 0, "ticks": 0, "l": 1, "keyon": 1, "vol": 8, "fnum": 172, "block": 4},
        {"#type": "instVol", "ch": 0, "ticks": 1, "l": 1, "keyon": 1, "vol": 8, "fnum": 181, "block": 4},
    ]

    out = pass2_compute_onsets_and_ioi(events)

    assert out[0]["scale"] == "c"
    assert out[1]["scale"] == "c+"


def test_pass3_merge_pitch_volume_wobble_compares_opll_scales():
    events = [
        {"ch": 0, "ticks": 0, "l": 1, "keyon": 1, "vol": 8, "block": 4, "fnum": 11, "inst": 1, "sus": 0},
        {"ch": 0, "ticks": 1, "l": 1, "keyon": 1, "vol": 8, "block": 4, "fnum": 12, "inst": 1, "sus": 0},
    ]

    out = pass3_merge_pitch_volume_wobble(events)

    assert len(out) == 2
