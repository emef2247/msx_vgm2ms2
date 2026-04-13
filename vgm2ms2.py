#!/usr/bin/env python3
"""
vgm2ms2.py - Top-level CLI: VGM binary → MS2 (MoonSound Music Studio) binary

Produces an OPLL-only .MS2 file for playback in MS² (MoonSound Music Studio).
Only the YM2413 (OPLL) FM channels are exported; PSG and SCC are ignored.

Usage:
    python vgm2ms2.py <vgm_file> [--outdir <dir>] [--bpm <bpm>] [--debug]

Arguments:
    vgm_file          Path to the input VGM file.
    --outdir <dir>    Output directory (default: outputs/<song_stem>/).
    --bpm <bpm>       Song tempo in BPM (default: 120).
    --timer <0|1>     0 = VDP timer (default), 1 = 50 Hz timer.
    --video <ntsc|pal> Video system for VDP timer (default: ntsc).
                      Ignored when --timer 1.
    --debug           Print extra info and dump the generated MS2 structure.
    --compare         Also run ms2_dump.py --compare against ms2_example/TEST.MS2.

Example:
    python vgm2ms2.py "inputs/ym2413/Ys-SMS-FM/Ys (FM) - 01 - Feena.vgm" --debug
"""

import sys
import os
import argparse

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_SCRIPT_DIR, 'py'))

from vgm_reader import parse_vgm
from opll_mml import _build_segments, _assign_voice_ids, NUM_CH
from ms2_writer import build_ms2_file
from ms2_dump import dump_ms2, compare_signatures
from ms2_validate import validate_ms2_bytes


def main():
    parser = argparse.ArgumentParser(
        description='Convert VGM (OPLL/YM2413) to MS2 binary for MoonSound Music Studio'
    )
    parser.add_argument('vgm_file', help='Input VGM file')
    parser.add_argument(
        '--outdir', default=None,
        help='Output directory (default: outputs/<song_stem>/)'
    )
    parser.add_argument(
        '--bpm', type=int, default=120,
        help='Song tempo in BPM (default: 120)'
    )
    parser.add_argument(
        '--timer', type=int, default=0, choices=[0, 1],
        help='0 = VDP timer (default), 1 = 50 Hz timer'
    )
    parser.add_argument(
        '--video', default='ntsc', choices=['ntsc', 'pal'],
        help='Video system: ntsc (default) or pal; only used when --timer 0'
    )
    parser.add_argument(
        '--debug', action='store_true',
        help='Print extra information and dump the generated MS2 structure'
    )
    parser.add_argument(
        '--compare', action='store_true',
        help='Compare generated file against ms2_example/TEST.MS2'
    )
    parser.add_argument(
        '--no-validate', dest='validate', action='store_false', default=True,
        help='Skip post-build validation of Patterns chunk (validation is on by default)'
    )
    args = parser.parse_args()

    vgm_path = args.vgm_file
    if not os.path.isfile(vgm_path):
        print(f"ERROR: VGM file not found: {vgm_path}", file=sys.stderr)
        sys.exit(1)

    # ── Derive output directory and stem ─────────────────────────────────
    base_name = os.path.splitext(os.path.basename(vgm_path))[0]

    if args.outdir:
        output_dir = args.outdir
    else:
        output_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'outputs',
            base_name,
        )
    os.makedirs(output_dir, exist_ok=True)

    # ── Parse VGM → trace CSVs ───────────────────────────────────────────
    print(f"Parsing VGM: {vgm_path}")
    (
        _psg_log, _scc_log,
        _psg_trace, _scc_trace,
        _opll_log, opll_trace_csv,
        opll_voice_csv, _opll_regs_csv,
    ) = parse_vgm(vgm_path, output_dir)

    print(f"  OPLL trace CSV: {opll_trace_csv}")
    print(f"  OPLL voice CSV: {opll_voice_csv}")

    # ── Build OPLL segments ───────────────────────────────────────────────
    print("Building OPLL segments …")
    segments = _build_segments(opll_trace_csv)
    n_segs = sum(len(v) for v in segments.values())
    print(f"  {n_segs} segments across {NUM_CH} channels")

    # ── Generate MS2 binary ───────────────────────────────────────────────
    video_label = '' if args.timer == 1 else f', video={args.video}'
    print(f"Generating MS2 (BPM={args.bpm}, timer={args.timer}{video_label}) …")
    ms2_bytes = build_ms2_file(
        segments       = segments,
        voice_csv_path = opll_voice_csv,
        stem           = base_name,
        bpm            = args.bpm,
        timer          = args.timer,
        video          = args.video,
        debug          = args.debug,
    )

    # ── Write output file ─────────────────────────────────────────────────
    out_name = base_name + '.MS2'
    out_path = os.path.join(output_dir, out_name)
    with open(out_path, 'wb') as f:
        f.write(ms2_bytes)

    print(f"Written: {out_path}  ({len(ms2_bytes)} bytes)")

    # ── Optional: validate Patterns chunk structure ───────────────────────
    if args.validate:
        errors = validate_ms2_bytes(ms2_bytes)
        if errors:
            print(f"\nVALIDATION FAILED ({len(errors)} error(s)):")
            for e in errors:
                print(f"  {e}")
            print("Aborting: generated file is structurally invalid for MS².")
            # Remove the invalid file so the user doesn't accidentally load it
            try:
                os.remove(out_path)
            except OSError:
                pass
            sys.exit(1)
        else:
            print("Validation OK: Patterns chunk is structurally valid.")

    # ── Optional: dump structure ──────────────────────────────────────────
    if args.debug:
        print()
        print('=' * 60)
        print('MS2 structure dump:')
        print('=' * 60)
        dump_ms2(out_path, show_pattern_bytes=True, max_patterns=2)

    # ── Optional: compare with TEST.MS2 ──────────────────────────────────
    if args.compare:
        test_ms2 = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'ms2_example', 'TEST.MS2'
        )
        if os.path.isfile(test_ms2):
            print()
            print('Comparing with TEST.MS2:')
            compare_signatures(out_path, test_ms2)
        else:
            print(f"WARNING: TEST.MS2 not found at {test_ms2}", file=sys.stderr)


if __name__ == '__main__':
    main()
