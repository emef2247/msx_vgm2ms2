import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import opll as opll_mml


def test_process_opll_csv_uses_new_build_segments_api_and_global_num_ch(tmp_path, monkeypatch):
    called = {}

    def fake_build_segments(trace_path, include_rhythm=True, rhythm_reg_timeline=None, ticks_per_step_override=None):
        called["trace_path"] = trace_path
        called["include_rhythm"] = include_rhythm
        called["rhythm_reg_timeline"] = rhythm_reg_timeline
        seg = SimpleNamespace(
            beat_start=0,
            beat_end=1,
            keyon=1,
            fnum=440,
            block=4,
            inst=1,
            vol=8,
            voice_id=0,
            at_token='@0',
        )
        return {2: [seg]}, 1.0

    monkeypatch.setattr(opll_mml, "_build_segments", fake_build_segments)
    monkeypatch.setattr(opll_mml, "_assign_voice_ids", lambda *_args, **_kwargs: ({}, {}, []))
    monkeypatch.setattr(opll_mml, "_generate_mml_mgs_pct", lambda *_args, **_kwargs: "x\n")
    monkeypatch.setattr(opll_mml, "compress_mml_text", lambda s: s)

    original_num_ch = opll_mml.NUM_CH
    monkeypatch.setattr(opll_mml, "NUM_CH", 3)
    try:
        trace_path = os.path.join(str(tmp_path), "dummy_trace.opll.csv")
        output_dir = os.path.join(str(tmp_path), "out")
        timeline = [(0, 32)]
        out_path = opll_mml.process_opll_csv(
            trace_path,
            output_dir,
            stem="dummy",
            dump_passes=True,
            debug=False,
            rhythm_reg_timeline=timeline,
        )
    finally:
        monkeypatch.setattr(opll_mml, "NUM_CH", original_num_ch)

    assert called["trace_path"] == trace_path
    assert called["include_rhythm"] is True
    assert called["rhythm_reg_timeline"] == timeline
    assert out_path.endswith("dummy.opll.pass3.compress.MGS_pct.mml")

    pass0_path = os.path.join(output_dir, "dummy.opll.pass0.csv")
    dumped = open(pass0_path, "r", encoding="utf-8").read()
    assert "grid_index,grid_start_tick,grid_end_tick,grid_offset_start,grid_offset_end,ticks_per_grid" in dumped
    assert "2,0,1,1,440,4,1,8,0,@0" in dumped
