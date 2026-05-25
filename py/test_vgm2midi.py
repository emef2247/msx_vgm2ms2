from types import SimpleNamespace

import vgm2midi


def _make_seg(**kwargs):
    data = {
        "ch": 0,
        "tick_start": 0,
        "tick_end": 60,
        "keyon": 1,
        "inst": 1,
        "vol": 0,
        "fnum": 1,
        "block": 4,
        "bd": 0,
        "sd": 0,
        "tom": 0,
        "tc": 0,
        "hh": 0,
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


def _extract_track_data(smf_bytes: bytes) -> bytes:
    assert smf_bytes[:4] == b"MThd"
    assert smf_bytes[14:18] == b"MTrk"
    size = int.from_bytes(smf_bytes[18:22], "big")
    return smf_bytes[22 : 22 + size]


def test_encode_vlq():
    assert vgm2midi._encode_vlq(0) == b"\x00"
    assert vgm2midi._encode_vlq(127) == b"\x7f"
    assert vgm2midi._encode_vlq(128) == b"\x81\x00"
    assert vgm2midi._encode_vlq(16383) == b"\xff\x7f"


def test_convert_segments_program_change_and_rhythm(monkeypatch):
    monkeypatch.setattr(vgm2midi, "_opll_note", lambda _f, _b: (4, "c"))

    segs = {ch: [] for ch in range(vgm2midi.NUM_CH)}
    segs[0].append(_make_seg(ch=0, tick_start=0, tick_end=60, inst=1, vol=0))
    segs[0].append(_make_seg(ch=0, tick_start=60, tick_end=120, inst=2, vol=8))
    segs[vgm2midi.RHYTHM_CH_MAP["bd"]].append(
        _make_seg(
            ch=vgm2midi.RHYTHM_CH_MAP["bd"],
            tick_start=0,
            tick_end=60,
            vol=4,
            bd=1,
        )
    )

    builder = vgm2midi.convert_opll_segments_to_midi(segs, bpm=120, ppq=480, track_name="demo")
    track_data = _extract_track_data(builder.build())

    assert b"\x00\xc0\x28" in track_data  # ch0 program change inst1->GM40
    assert b"\xc0\x19" in track_data  # ch0 program change inst2->GM25
    assert b"\x00\x99\x23" in track_data  # drum ch9 note 35 (BD)
    assert track_data.endswith(b"\x00\xff\x2f\x00")


def test_process_vgm_to_midi_pipeline(tmp_path, monkeypatch):
    vgm_path = tmp_path / "song.vgm"
    vgm_path.write_bytes(b"Vgm ")

    trace = tmp_path / "song_trace.opll.csv"
    voice = tmp_path / "song_trace.opll_voice.csv"
    regs = tmp_path / "song_trace.opll_regs.csv"
    trace.write_text("", encoding="utf-8")
    voice.write_text("", encoding="utf-8")
    regs.write_text("", encoding="utf-8")

    called = {}

    def fake_parse_vgm(_vgm, _out):
        return ("a", "b", "c", "d", "e", str(trace), str(voice), str(regs))

    def fake_build_segments(_trace_csv, **kwargs):
        called["ticks_per_step_override"] = kwargs.get("ticks_per_step_override")
        return ({ch: [] for ch in range(vgm2midi.NUM_CH)}, 120)

    monkeypatch.setattr(vgm2midi, "parse_vgm", fake_parse_vgm)
    monkeypatch.setattr(vgm2midi, "parse_opll_regs_for_rhythm", lambda _p: [(0, 0x20)])
    monkeypatch.setattr(vgm2midi, "_build_segments", fake_build_segments)
    monkeypatch.setattr(vgm2midi, "_assign_voice_ids", lambda _s, _v: ({}, {}, []))

    out_path = vgm2midi.process_vgm_to_midi(str(vgm_path), str(tmp_path), ticks_per_step_override=8, ppq=480)

    assert called["ticks_per_step_override"] == 8
    assert out_path.endswith("song.mid")
    assert (tmp_path / "song.mid").exists()


def test_midi_bpm_derived_from_ticks_per_step(tmp_path, monkeypatch):
    """midi_bpm は bpm_detected ではなく ticks_per_step から逆算した値になること。

    ticks_per_step=2 のとき: round(59.988... * 15 / 2) = round(449.9) = 450
    bpm_detected=499 が返されても midi_bpm=450 になること。
    """
    vgm_path = tmp_path / "song.vgm"
    vgm_path.write_bytes(b"Vgm ")

    trace = tmp_path / "song_trace.opll.csv"
    voice = tmp_path / "song_trace.opll_voice.csv"
    regs = tmp_path / "song_trace.opll_regs.csv"
    trace.write_text("", encoding="utf-8")
    voice.write_text("", encoding="utf-8")
    regs.write_text("", encoding="utf-8")

    bpm_used = {}

    def fake_parse_vgm(_vgm, _out):
        return ("a", "b", "c", "d", "e", str(trace), str(voice), str(regs))

    def fake_build_segments(_trace_csv, **kwargs):
        # Returns a wrongly estimated bpm_detected=499 instead of the true 450
        return ({ch: [] for ch in range(vgm2midi.NUM_CH)}, 499)

    def fake_convert_to_midi(segs, bpm, ppq=480, track_name=""):
        bpm_used["bpm"] = bpm
        builder = vgm2midi.MidiBuilder(ppq=ppq)
        return builder

    monkeypatch.setattr(vgm2midi, "parse_vgm", fake_parse_vgm)
    monkeypatch.setattr(vgm2midi, "parse_opll_regs_for_rhythm", lambda _p: [(0, 0x20)])
    monkeypatch.setattr(vgm2midi, "_build_segments", fake_build_segments)
    monkeypatch.setattr(vgm2midi, "_assign_voice_ids", lambda _s, _v: ({}, {}, []))
    monkeypatch.setattr(vgm2midi, "convert_opll_segments_to_midi", fake_convert_to_midi)

    # With ticks_per_step_override=2, midi_bpm must be 450 regardless of bpm_detected
    vgm2midi.process_vgm_to_midi(str(vgm_path), str(tmp_path), ticks_per_step_override=2, ppq=480)
    assert bpm_used["bpm"] == 450, (
        f"Expected midi_bpm=450 (from ticks_per_step=2), got {bpm_used['bpm']}"
    )
