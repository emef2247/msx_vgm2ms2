#!/usr/bin/env python3
"""Convert VGM (YM2413/OPLL) traces to Standard MIDI File (SMF format 0).
Usage:
    python vgm2midi.py <vgm_file> [--outdir <dir>] [--debug]

Default output (no --debug):
    Generate <stem>.mid and <stem>.user_voice.json (if user patches exist)

With --debug:
    The <stem>.mid file and the raw log/trace CSV files.

Example:
    python vgm2midi.py test.vgm
"""

from __future__ import annotations

import sys
import os
import json
import argparse
import struct
from dataclasses import dataclass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_SCRIPT_DIR, 'py'))

from vgm_reader import parse_vgm
from opll import (
    NUM_CH,
    RHYTHM_CH_MAP,
    _assign_voice_ids,
    _build_segments,
    _opll_note,
    _ym2413_patch_to_mgsdrv,
    parse_opll_regs_for_rhythm,
)

DEFAULT_PPQ = 480

# User patches are assigned GM programs starting from this value (0-indexed).
# Programs 0-14 correspond to OPLL presets 1-15 via OPLL_TO_GM_PROGRAM.
# Programs 15-19 are reserved for rhythm channels (bd/sd/tom/tc/hh) in MS2
# convention (INST 16-20).  User patches therefore start at program 20
# (= GM program 21 in 1-indexed / musician notation).
_USER_VOICE_FIRST_GM_PROGRAM = 20

# All-zero patch sentinel: inst=0 with no register writes.
# Fixed to GM 127 (Gunshot) to make it clearly audible / obviously wrong.
_ZERO_PATCH = bytes(8)
_GM_PROGRAM_UNUSED = 127   # GM 127 = Gunshot (0-indexed)

OPLL_TO_GM_PROGRAM = {
    0: 81,   # fallback for inst=0 without user-patch info
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


# ---------------------------------------------------------------------------
# User voice map helpers
# ---------------------------------------------------------------------------

def _user_voice_map_path(output_dir: str, stem: str) -> str:
    """Return the path for <stem>.user_voice.json beside the .mid output."""
    return os.path.join(output_dir, f"{stem}.user_voice.json")


def _is_zero_patch(patch_bytes: bytes) -> bool:
    """Return True if the patch is all-zero (no register writes occurred)."""
    return patch_bytes == _ZERO_PATCH


def _format_user_patch_lines(at_v_num: int, patch_bytes: bytes, gm_prog: int) -> list[str]:
    """Return human-readable description lines for a single user patch.

    All-zero patches (no register written) are flagged as unused with a
    single summary line instead of the full register decode.

    Normal format::

        User patch Inst 20 (1-indexed:21) -> GM program 20
        Inst 20: 'User patch v15' regs = 21 1B C2 F0 01 14 74 11
            TL=27 FB=4
            MO: AR= 2 DR= 1 SL=12 RR= 2 KL= 0 MT= 1 AM= 0 VB= 0 EG= 1 KR= 0 DT= 0
            CA: AR=15 DR= 4 SL= 0 RR= 5 KL= 0 MT= 1 AM= 0 VB= 1 EG= 0 KR= 1 DT= 0

    All-zero format::

        User patch Inst 127 (1-indexed:128) -> GM program 127  (unused - no register written)
        Inst 127: 'User patch v15' regs = 00 00 00 00 00 00 00 00  [all zero]
    """
    regs_str = ' '.join(f'{b:02X}' for b in patch_bytes)
    if _is_zero_patch(patch_bytes):
        return [
            f"User patch Inst {gm_prog} (1-indexed:{gm_prog + 1}) -> GM program {gm_prog}"
            f"  (unused - no register written)",
            f"Inst {gm_prog}: 'User patch v{at_v_num}' regs = {regs_str}  [all zero]",
        ]
    d = _ym2413_patch_to_mgsdrv(patch_bytes)
    tl, fb = d['tl'], d['fb']
    m = d['mod']   # (ar, dr, sl, rr, kl, mt, am, vb, eg, kr, dt)
    c = d['car']
    return [
        f"User patch Inst {gm_prog} (1-indexed:{gm_prog + 1}) -> GM program {gm_prog}",
        f"Inst {gm_prog}: 'User patch v{at_v_num}' regs = {regs_str}",
        f"\tTL={tl:2d} FB={fb}",
        f"\tMO: AR={m[0]:2d} DR={m[1]:2d} SL={m[2]:2d} RR={m[3]:2d} "
        f"KL={m[4]} MT={m[5]:2d} AM={m[6]} VB={m[7]} EG={m[8]} KR={m[9]} DT={m[10]}",
        f"\tCA: AR={c[0]:2d} DR={c[1]:2d} SL={c[2]:2d} RR={c[3]:2d} "
        f"KL={c[4]} MT={c[5]:2d} AM={c[6]} VB={c[7]} EG={c[8]} KR={c[9]} DT={c[10]}",
    ]


def _load_user_voice_map(path: str) -> dict[str, int]:
    """Load patch_hex -> GM program (0-indexed) mapping from JSON.

    Returns an empty dict if the file does not exist or cannot be parsed.
    Keys are lower-case 16-character hex strings.
    Values are 0-indexed GM program numbers (0-127).
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        result: dict[str, int] = {}
        for k, v in data.items():
            if k.startswith("_"):          # skip comment/info keys
                continue
            if isinstance(k, str) and isinstance(v, int):
                result[k.lower()] = max(0, min(127, v))
        return result
    except Exception:
        return {}


def _save_user_voice_map(
    path: str,
    mapping: dict[str, int],
    user_patches: dict[int, bytes],
    at_v_to_gm: dict[int, int],
) -> None:
    """Save patch_hex -> GM program (0-indexed) mapping to JSON.

    Adds:
    - ``_comment``: format explanation for the user.
    - ``_voice_info``: dict of patch_hex -> list[str] with decoded OPLL register
      details for each user patch (at the end of the file for reference).
    """
    out: dict = {
        "_comment": (
            "OPLL user-patch to GM program mapping for vgm2midi.py. "
            "Keys are 8-byte OPLL patch data in hex (16 chars). "
            "Values are 0-indexed GM program numbers "
            "(0=Acoustic Grand Piano ... 127=Gunshot; "
            "i.e. subtract 1 from the 1-indexed program number shown in most DAWs). "
            "Edit values to remap user patches to any GM instrument. "
            "New patches discovered on the next run are appended automatically. "
            "All-zero patches (no register written) are fixed to GM 127 (Gunshot) "
            "and marked as unused."
        ),
    }
    out.update(mapping)

    # Build _voice_info: patch_hex -> decoded register lines (for reference / editing)
    # Keyed by patch_hex so it matches the mapping entries above.
    voice_info: dict[str, list[str]] = {}
    for at_v_num, patch_bytes in sorted(user_patches.items()):
        patch_hex = patch_bytes.hex()
        gm_prog = at_v_to_gm.get(at_v_num, mapping.get(patch_hex, _USER_VOICE_FIRST_GM_PROGRAM))
        voice_info[patch_hex] = _format_user_patch_lines(at_v_num, patch_bytes, gm_prog)
    if voice_info:
        out["_voice_info"] = voice_info

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")


def _build_user_voice_gm_map(
    user_patches: dict[int, bytes],
    existing_map: dict[str, int],
) -> tuple[dict[int, int], dict[str, int]]:
    """Build at_v_num -> GM program (0-indexed) for all user patches.

    Special cases:
    - All-zero patch (no register writes): always mapped to GM 127 (Gunshot),
      regardless of ``existing_map``.  This is a sentinel for "unused" patches.

    For other patches already present in *existing_map* the stored program is used.
    New non-zero patches are assigned the next free program >= ``_USER_VOICE_FIRST_GM_PROGRAM``
    in encounter order.

    Returns:
        at_v_to_gm   : dict mapping at_v_num (int) -> GM program (0-indexed int)
        updated_map  : updated patch_hex -> GM program dict (superset of existing_map)
    """
    at_v_to_gm: dict[int, int] = {}
    updated = dict(existing_map)

    used_programs: set[int] = set(existing_map.values())
    # Reserve GM 127 for zero patches; exclude from free-assignment pool
    used_programs.add(_GM_PROGRAM_UNUSED)

    def _next_free() -> int:
        p = _USER_VOICE_FIRST_GM_PROGRAM
        while p in used_programs:
            p += 1
        used_programs.add(p)
        return p

    for at_v_num, patch_bytes in user_patches.items():
        patch_hex = patch_bytes.hex()
        # All-zero patch is always GM 127 (unused sentinel), not editable
        if _is_zero_patch(patch_bytes):
            at_v_to_gm[at_v_num] = _GM_PROGRAM_UNUSED
            updated[patch_hex] = _GM_PROGRAM_UNUSED
            continue
        if patch_hex in updated:
            at_v_to_gm[at_v_num] = updated[patch_hex]
        else:
            gm_prog = _next_free()
            updated[patch_hex] = gm_prog
            at_v_to_gm[at_v_num] = gm_prog

    return at_v_to_gm, updated


# ---------------------------------------------------------------------------
# MIDI helpers
# ---------------------------------------------------------------------------

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


def _at_token_to_user_v_num(at_token: str) -> int | None:
    """Parse ``'@v15'`` -> ``15``.  Returns None for preset tokens like ``'@5'``."""
    if at_token and at_token.startswith("@v"):
        try:
            return int(at_token[2:])
        except ValueError:
            pass
    return None


def convert_opll_segments_to_midi(
    segments_by_ch: dict,
    bpm: int,
    ppq: int = DEFAULT_PPQ,
    track_name: str = "vgm2midi",
    user_voice_gm_map: dict[int, int] | None = None,
) -> MidiBuilder:
    """Convert OPLL segments to a MidiBuilder.

    Args:
        segments_by_ch   : channel -> list[_Segment]
        bpm              : detected BPM
        ppq              : MIDI ticks per quarter note
        track_name       : MIDI track name metadata
        user_voice_gm_map: at_v_num -> GM program (0-indexed).
                           When provided, inst=0 segments with ``@vN`` tokens
                           are mapped to the corresponding GM program instead of
                           the generic OPLL_TO_GM_PROGRAM[0] fallback.
    """
    builder = MidiBuilder(ppq=ppq)
    builder.add_event(0, _tempo_meta_event(bpm), order=0)
    builder.add_event(0, _track_name_meta_event(track_name), order=1)

    last_program_by_ch: dict[int, int] = {}
    last_note_by_ch: dict[int, int] = {}
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
                inst = int(getattr(seg, "inst", 0))

                # ── Resolve GM program ──────────────────────────────────────
                if inst == 0 and user_voice_gm_map:
                    at_token = getattr(seg, "at_token", "") or ""
                    at_v_num = _at_token_to_user_v_num(at_token)
                    if at_v_num is not None and at_v_num in user_voice_gm_map:
                        program = user_voice_gm_map[at_v_num]
                    else:
                        program = OPLL_TO_GM_PROGRAM.get(0, 81)
                else:
                    program = OPLL_TO_GM_PROGRAM.get(inst, 81)

                if last_program_by_ch.get(midi_ch) != program:
                    builder.add_event(start_tick, bytes([0xC0 | midi_ch, program]), order=10)
                    last_program_by_ch[midi_ch] = program

                midi_note = _segment_to_midi_note(seg)
                if midi_note is None:
                    continue
                vel = _opll_vol_to_velocity(getattr(seg, "vol", 15))
                is_portamento = int(getattr(seg, "is_portamento", 0))
                if is_portamento == 1 and midi_ch in last_note_by_ch:
                    prev_note = last_note_by_ch[midi_ch]
                    try:
                        mode_ioi = int(getattr(seg, "mode_ioi", 2))
                    except (TypeError, ValueError):
                        mode_ioi = 2
                    try:
                        seg_l = int(getattr(seg, "l", 2))
                    except (TypeError, ValueError):
                        seg_l = 2
                    portamento_time = min(127, max(1, seg_l * 64 // max(1, mode_ioi)))

                    builder.add_event(start_tick, bytes([0xB0 | midi_ch, 84, prev_note]), order=15)
                    builder.add_event(start_tick, bytes([0xB0 | midi_ch, 65, 127]), order=16)
                    builder.add_event(start_tick, bytes([0xB0 | midi_ch, 5, portamento_time]), order=17)
                    builder.add_event(end_tick, bytes([0xB0 | midi_ch, 65, 0]), order=35)
                builder.add_event(start_tick, bytes([0x90 | midi_ch, midi_note, vel]), order=20)
                builder.add_event(end_tick, bytes([0x80 | midi_ch, midi_note, 0]), order=40)
                last_note_by_ch[midi_ch] = midi_note

    return builder


def process_vgm_to_midi(
    vgm_path: str,
    output_dir: str | None = None,
    ppq: int = DEFAULT_PPQ,
    debug: bool = False,
) -> str:
    base_name = os.path.splitext(os.path.basename(vgm_path))[0]
    if output_dir is None:
        output_dir = os.path.join(
            os.path.dirname(os.path.abspath(vgm_path)),
        )
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
        debug=debug,
    )

    if debug:
        print(f"[vgm2midi] bpm={bpm_detected}")

    # ── Assign voice IDs (captures user patches) ──────────────────────────
    _voice_table, user_patches, _warnings = _assign_voice_ids(segments_by_ch, opll_voice_csv)

    # ── Resolve user-patch GM program mapping ─────────────────────────────
    user_voice_gm_map: dict[int, int] | None = None
    if user_patches:
        # <stem>.user_voice.json lives beside <stem>.mid
        uv_path = _user_voice_map_path(output_dir, base_name)
        existing_map = _load_user_voice_map(uv_path)
        user_voice_gm_map, updated_map = _build_user_voice_gm_map(user_patches, existing_map)
        _save_user_voice_map(uv_path, updated_map, user_patches, user_voice_gm_map)

        # Print user patch info to stdout.
        # All-zero patches are shown as a brief unused notice; real patches get full decode.
        real_patches = {k: v for k, v in user_patches.items() if not _is_zero_patch(v)}
        zero_patches = {k: v for k, v in user_patches.items() if _is_zero_patch(v)}
        total = len(user_patches)
        print(f"User patches ({total}) -> {uv_path}")
        for at_v_num in sorted(real_patches):
            patch_bytes = real_patches[at_v_num]
            gm_prog = user_voice_gm_map[at_v_num]
            for line in _format_user_patch_lines(at_v_num, patch_bytes, gm_prog):
                print(line)
        for at_v_num in sorted(zero_patches):
            gm_prog = user_voice_gm_map[at_v_num]
            print(
                f"User patch v{at_v_num}: all-zero (no register written) -> "
                f"GM program {gm_prog} (1-indexed:{gm_prog + 1}, unused)"
            )

    builder = convert_opll_segments_to_midi(
        segments_by_ch,
        bpm=bpm_detected,
        ppq=ppq,
        track_name=base_name,
        user_voice_gm_map=user_voice_gm_map,
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
    parser.add_argument(
        "--outdir", default=None,
        help="Output directory (default: same directory as VGM file)",
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

    if not os.path.isfile(args.vgm_file):
        parser.error(f"vgm_file not found: {args.vgm_file}")

    out_path = process_vgm_to_midi(
        args.vgm_file,
        output_dir=args.outdir,
        ppq=args.ppq,
        debug=args.debug,
    )
    print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
