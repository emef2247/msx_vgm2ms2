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


def test_convert_segments_adds_portamento_ccs_for_marked_melody_segment(monkeypatch):
    monkeypatch.setattr(
        vgm2midi,
        "_opll_note",
        lambda fnum, _block: (4, "c") if fnum == 1 else (4, "d"),
    )

    segs = {ch: [] for ch in range(vgm2midi.NUM_CH)}
    segs[0].append(_make_seg(ch=0, tick_start=0, tick_end=60, inst=1, vol=0, fnum=1))
    segs[0].append(
        _make_seg(
            ch=0,
            tick_start=60,
            tick_end=120,
            inst=1,
            vol=0,
            fnum=2,
            is_portamento=1,
            l=4,
            mode_ioi=8,
        )
    )

    builder = vgm2midi.convert_opll_segments_to_midi(segs, bpm=120, ppq=480, track_name="demo")
    events = sorted(builder._events, key=lambda e: (e.tick, e.order))
    portamento_events = [(ev.tick, ev.order, ev.data) for ev in events if ev.data[:2] == b"\xB0T" or ev.data[:2] == b"\xB0A" or ev.data[:2] == b"\xB0\x05"]

    assert portamento_events == [
        (960, 15, bytes([0xB0, 84, 60])),
        (960, 16, bytes([0xB0, 65, 127])),
        (960, 17, bytes([0xB0, 5, 32])),
        (1920, 35, bytes([0xB0, 65, 0])),
    ]


def test_convert_segments_does_not_add_portamento_ccs_for_rhythm(monkeypatch):
    monkeypatch.setattr(vgm2midi, "_opll_note", lambda _f, _b: (4, "c"))

    segs = {ch: [] for ch in range(vgm2midi.NUM_CH)}
    segs[vgm2midi.RHYTHM_CH_MAP["bd"]].append(
        _make_seg(
            ch=vgm2midi.RHYTHM_CH_MAP["bd"],
            tick_start=0,
            tick_end=60,
            vol=4,
            bd=1,
            is_portamento=1,
        )
    )

    builder = vgm2midi.convert_opll_segments_to_midi(segs, bpm=120, ppq=480, track_name="demo")
    events = sorted(builder._events, key=lambda e: (e.tick, e.order))

    assert not any(ev.data and (ev.data[0] & 0xF0) == 0xB0 for ev in events)


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
    """midi_bpm は _build_segments() が返す NTSC ベースの bpm をそのまま使うこと。

    ticks_per_step_override=2 のとき _build_segments() が 450 を返せば、
    convert_opll_segments_to_midi にも 450 が渡されること。
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
        # _build_segments() now returns the NTSC-based BPM directly.
        # With ticks_per_step_override=2: round(59.988... * 15 / 2) = round(449.9) = 450
        tps_override = kwargs.get("ticks_per_step_override")
        if tps_override is not None:
            import math as _math
            _FREQ_NTSC = 59.988527908187
            tps = max(2, int(tps_override))
            bpm = max(1, round(_FREQ_NTSC * 15 / tps))
        else:
            bpm = 450
        return ({ch: [] for ch in range(vgm2midi.NUM_CH)}, bpm)

    def fake_convert_to_midi(segs, bpm, ppq=480, track_name=""):
        bpm_used["bpm"] = bpm
        builder = vgm2midi.MidiBuilder(ppq=ppq)
        return builder

    monkeypatch.setattr(vgm2midi, "parse_vgm", fake_parse_vgm)
    monkeypatch.setattr(vgm2midi, "parse_opll_regs_for_rhythm", lambda _p: [(0, 0x20)])
    monkeypatch.setattr(vgm2midi, "_build_segments", fake_build_segments)
    monkeypatch.setattr(vgm2midi, "_assign_voice_ids", lambda _s, _v: ({}, {}, []))
    monkeypatch.setattr(vgm2midi, "convert_opll_segments_to_midi", fake_convert_to_midi)

    # With ticks_per_step_override=2, _build_segments returns 450, which must be passed through
    vgm2midi.process_vgm_to_midi(str(vgm_path), str(tmp_path), ticks_per_step_override=2, ppq=480)
    assert bpm_used["bpm"] == 450, (
        f"Expected midi_bpm=450 (from ticks_per_step=2), got {bpm_used['bpm']}"
    )
