#!/usr/bin/env python3
"""Convert VGM (YM2413/OPLL) traces to Standard MIDI File (SMF format 0)."""

from __future__ import annotations

import sys
import os
import argparse
import struct
from dataclasses import dataclass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_SCRIPT_DIR, 'py'))

from vgm_reader import parse_vgm
from opll_mml import (
    NUM_CH,
    RHYTHM_CH_MAP,
    _assign_voice_ids,
    _build_segments,
    _opll_note,
    parse_opll_regs_for_rhythm,
)

DEFAULT_PPQ = 480

_FREQ_NTSC: float = 59.988527908187

OPLL_TO_GM_PROGRAM = {
    0: 81,
    1: 40,
    2: 25,
    3: 0,
    4: 73,
    5: 71,
    6: 68,
    7: 56,
    8: 19,
    9: 60,
    10: 81,
    11: 6,
    12: 11,
    13: 38,
    14: 32,
    15: 27,
}

RHYTHM_TO_GM_NOTE = {
    "bd": 35,
    "sd": 38,
    "tom": 41,
    "tc": 49,
    "hh": 42,
}

SCALE_TO_SEMITONE = {
    "c": 0,
    "c+": 1,
    "d": 2,
    "d+": 3,
    "e": 4,
    "f": 5,
    "f+": 6,
    "g": 7,
    "g+": 8,
    "a": 9,
    "a+": 10,
    "b": 11,
}


@dataclass(frozen=True)
class MidiEvent:
    tick: int
    order: int
    data: bytes


def _clamp_midi_note(note: int) -> int:
    return max(0, min(127, int(note)))


def _clamp_velocity(vel: int) -> int:
    return max(0, min(127, int(vel)))


def _opll_vol_to_velocity(vol_opll: int) -> int:
    return _clamp_velocity(int(round((15 - max(0, min(15, int(vol_opll)))) * 127 / 15)))


def _vgm_tick_to_midi_tick(vgm_tick: int, bpm: int, ppq: int) -> int:
    return int(round(int(vgm_tick) * int(bpm) * int(ppq) / 3600.0))


def _encode_vlq(value: int) -> bytes:
    value = int(value)
    if value < 0:
        raise ValueError("VLQ value must be non-negative")
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.reverse()
    return bytes(out)


def _tempo_meta_event(bpm: int) -> bytes:
    us_per_qn = int(round(60_000_000 / max(1, int(bpm))))
    return b"\xFF\x51\x03" + struct.pack(">I", us_per_qn)[1:]


def _track_name_meta_event(name: str) -> bytes:
    name_bytes = (name or "vgm2midi").encode("utf-8", errors="replace")
    if len(name_bytes) > 127:
        name_bytes = name_bytes[:127]
    return b"\xFF\x03" + bytes([len(name_bytes)]) + name_bytes


class MidiBuilder:
    def __init__(self, ppq: int = DEFAULT_PPQ):
        self.ppq = int(ppq)
        self._events: list[MidiEvent] = []

    def add_event(self, tick: int, data: bytes, order: int = 100) -> None:
        self._events.append(MidiEvent(max(0, int(tick)), int(order), bytes(data)))

    def build(self) -> bytes:
        self._events.sort(key=lambda e: (e.tick, e.order))
        track_data = bytearray()
        prev_tick = 0
        for ev in self._events:
            delta = ev.tick - prev_tick
            track_data.extend(_encode_vlq(delta))
            track_data.extend(ev.data)
            prev_tick = ev.tick
        track_data.extend(b"\x00\xFF\x2F\x00")

        mthd = b"MThd" + struct.pack(">IHHH", 6, 0, 1, self.ppq)
        mtrk = b"MTrk" + struct.pack(">I", len(track_data)) + bytes(track_data)
        return mthd + mtrk


def _segment_to_midi_note(seg) -> int | None:
    octave, scale = _opll_note(getattr(seg, "fnum", 0), getattr(seg, "block", 0))
    if scale == "r":
        return None
    semitone = SCALE_TO_SEMITONE.get(scale)
    if semitone is None:
        return None
    return _clamp_midi_note((int(octave) + 1) * 12 + semitone)


def convert_opll_segments_to_midi(
    segments_by_ch: dict,
    bpm: int,
    ppq: int = DEFAULT_PPQ,
    track_name: str = "vgm2midi",
) -> MidiBuilder:
    builder = MidiBuilder(ppq=ppq)
    builder.add_event(0, _tempo_meta_event(bpm), order=0)
    builder.add_event(0, _track_name_meta_event(track_name), order=1)

    last_program_by_ch: dict[int, int] = {}
    rhythm_channels = set(RHYTHM_CH_MAP.values())

    for ch in range(NUM_CH):
        for seg in segments_by_ch.get(ch, []):
            if int(getattr(seg, "keyon", 0)) != 1:
                continue

            start_tick = _vgm_tick_to_midi_tick(getattr(seg, "tick_start", 0), bpm, ppq)
            end_tick = _vgm_tick_to_midi_tick(getattr(seg, "tick_end", 0), bpm, ppq)
            if end_tick <= start_tick:
                continue

            if ch in rhythm_channels:
                drum_defs = (
                    ("bd", getattr(seg, "bd", 0)),
                    ("sd", getattr(seg, "sd", 0)),
                    ("tom", getattr(seg, "tom", 0)),
                    ("tc", getattr(seg, "tc", 0)),
                    ("hh", getattr(seg, "hh", 0)),
                )
                for drum_name, is_on in drum_defs:
                    if int(is_on) != 1:
                        continue
                    drum_note = RHYTHM_TO_GM_NOTE[drum_name]
                    vel = _opll_vol_to_velocity(getattr(seg, "vol", 15))
                    builder.add_event(start_tick, bytes([0x99, drum_note, vel]), order=30)
                    builder.add_event(end_tick, bytes([0x89, drum_note, 0]), order=40)
                continue

            if 0 <= ch <= 8:
                midi_ch = ch
                program = OPLL_TO_GM_PROGRAM.get(int(getattr(seg, "inst", 0)), 81)
                if last_program_by_ch.get(midi_ch) != program:
                    builder.add_event(start_tick, bytes([0xC0 | midi_ch, program]), order=10)
                    last_program_by_ch[midi_ch] = program

                midi_note = _segment_to_midi_note(seg)
                if midi_note is None:
                    continue
                vel = _opll_vol_to_velocity(getattr(seg, "vol", 15))
                builder.add_event(start_tick, bytes([0x90 | midi_ch, midi_note, vel]), order=20)
                builder.add_event(end_tick, bytes([0x80 | midi_ch, midi_note, 0]), order=40)

    return builder


def process_vgm_to_midi(
    vgm_path: str,
    output_dir: str | None = None,
    ticks_per_step_override: int | None = None,
    ppq: int = DEFAULT_PPQ,
    debug: bool = False,
) -> str:
    base_name = os.path.splitext(os.path.basename(vgm_path))[0]
    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(vgm_path))
    os.makedirs(output_dir, exist_ok=True)

    (
        psg_log_csv,
        scc_log_csv,
        psg_trace_csv,
        scc_trace_csv,
        opll_log_csv,
        opll_trace_csv,
        opll_voice_csv,
        opll_regs_csv,
    ) = parse_vgm(vgm_path, output_dir)

    rhythm_timeline = []
    if opll_regs_csv and os.path.exists(opll_regs_csv):
        rhythm_timeline = parse_opll_regs_for_rhythm(opll_regs_csv)

    segments_by_ch, bpm_detected = _build_segments(
        opll_trace_csv,
        include_rhythm=True,
        rhythm_reg_timeline=rhythm_timeline,
        ticks_per_step_override=ticks_per_step_override,
    )

    # Determine ticks_per_step using the same logic as _build_segments internally
    if ticks_per_step_override is not None:
        ticks_per_step = max(2, int(ticks_per_step_override))
    else:
        # Back-calculate mode_ioi from bpm_detected and apply the Nyquist condition
        mode_ioi = 3600.0 / bpm_detected if bpm_detected > 0 else 60.0
        ticks_per_step = max(2, int(mode_ioi // 2))

    # Derive accurate MIDI BPM from ticks_per_step using exact NTSC frequency
    midi_bpm = max(1, round(_FREQ_NTSC * 15 / ticks_per_step))

    if debug:
        print(f"[vgm2midi] bpm_detected={bpm_detected}, ticks_per_step={ticks_per_step}, midi_bpm={midi_bpm}")

    _assign_voice_ids(segments_by_ch, opll_voice_csv)

    builder = convert_opll_segments_to_midi(
        segments_by_ch,
        bpm=midi_bpm,
        ppq=ppq,
        track_name=base_name,
    )
    midi_bytes = builder.build()

    out_path = os.path.join(output_dir, f"{base_name}.mid")
    with open(out_path, "wb") as fh:
        fh.write(midi_bytes)

    if not debug:
        for _csv in [psg_log_csv, scc_log_csv, psg_trace_csv, scc_trace_csv,
                     opll_log_csv, opll_trace_csv, opll_voice_csv, opll_regs_csv]:
            try:
                if _csv and os.path.isfile(_csv):
                    os.remove(_csv)
            except OSError:
                pass

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert VGM (YM2413) to MIDI (SMF format 0)")
    parser.add_argument("vgm_file", help="Input VGM file path")
    parser.add_argument("output_dir", nargs="?", default=None, help="Output directory")
    parser.add_argument(
        "--ticks-per-step",
        type=int,
        default=None,
        metavar="N",
        help="Override ticks-per-step for segment building",
    )
    parser.add_argument(
        "--ppq",
        type=int,
        default=DEFAULT_PPQ,
        metavar="N",
        help=f"MIDI ticks per quarter note (default: {DEFAULT_PPQ})",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print extra information and keep intermediate CSV files",
    )
    args = parser.parse_args()

    if args.ppq <= 0:
        parser.error("--ppq must be >= 1")
    if args.ticks_per_step is not None and args.ticks_per_step < 2:
        parser.error("--ticks-per-step must be >= 2")

    if not os.path.isfile(args.vgm_file):
        parser.error(f"vgm_file not found: {args.vgm_file}")

    out_path = process_vgm_to_midi(
        args.vgm_file,
        output_dir=args.output_dir,
        ticks_per_step_override=args.ticks_per_step,
        ppq=args.ppq,
        debug=args.debug,
    )
    print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
