#!/usr/bin/env python3
"""
vgm2mml_grid.py - Top-level CLI: VGM binary → MGSDRV MML text (OPLL)
"""

import sys
import os
import argparse

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_SCRIPT_DIR, 'py'))

from vgm_reader import parse_vgm
from opll_mml import _build_segments
from ms2_writer import build_mgsdrvmml_file


def main():
    parser = argparse.ArgumentParser(
        description='Convert VGM (OPLL/YM2413) to MGSDRV MML text using MS2 step-grid mapping'
    )
    parser.add_argument('vgm_file', nargs='?', default=None, help='Input VGM file')
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
        help='0 = VDP timer (default), 1 = 50 Hz timer (kept for compatibility)'
    )
    parser.add_argument(
        '--base-ms2', default=None, metavar='PATH',
        help=(
            'Path to an MS2 file to use as the base preset instrument library. '
            'The first 15 instruments map to OPLL presets 1..15.'
        ),
    )
    parser.add_argument(
        '--video', default='ntsc', choices=['ntsc', 'pal'],
        help='Video system: ntsc (default) or pal',
    )
    parser.add_argument(
        '--ticks-per-step', type=int, default=None, metavar='N',
        help=(
            'Override ticks_per_step used for MS2 step grid quantization. '
            'Minimum value: 2. Must be a positive integer >= 2.'
        ),
    )
    parser.add_argument(
        '--debug', action='store_true',
        help='Print extra information'
    )
    args = parser.parse_args()
    if args.ticks_per_step is not None and args.ticks_per_step < 2:
        parser.error('--ticks-per-step must be >= 2')

    if args.vgm_file is None:
        parser.error('vgm_file is required')

    vgm_path = args.vgm_file
    if not os.path.isfile(vgm_path):
        print(f"ERROR: VGM file not found: {vgm_path}", file=sys.stderr)
        sys.exit(1)

    if args.base_ms2 and not os.path.isfile(args.base_ms2):
        print(f"ERROR: --base-ms2 file not found: {args.base_ms2}", file=sys.stderr)
        sys.exit(1)

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

    print(f"Parsing VGM: {vgm_path}")
    (
        psg_log_csv, scc_log_csv,
        psg_trace_csv, scc_trace_csv,
        opll_log_csv, opll_trace_csv,
        opll_voice_csv, opll_regs_csv,
    ) = parse_vgm(vgm_path, output_dir)

    print(f"  OPLL trace CSV: {opll_trace_csv}")
    print(f"  OPLL voice CSV: {opll_voice_csv}")

    print("Building OPLL segments …")
    num_ch = 18
    segments, bpm_detected = _build_segments(
        opll_trace_csv,
        include_rhythm=True,
        ticks_per_step_override=args.ticks_per_step,
        debug=args.debug,
    )
    n_segs = sum(len(v) for v in segments.values())
    print(f"  {n_segs} segments across {num_ch} channels")

    print(f"Generating MGSDRV MML (BPM={bpm_detected}, timer={args.timer}) …")
    if args.base_ms2:
        print(f"  Using base instrument library: {args.base_ms2}")
    mml_text = build_mgsdrvmml_file(
        segments=segments,
        voice_csv_path=opll_voice_csv,
        stem=base_name,
        bpm=bpm_detected,
        base_ms2=args.base_ms2,
        video=args.video,
        debug=args.debug,
        ticks_per_step_override=args.ticks_per_step,
    )

    out_name = base_name + '.opll.mml'
    out_path = os.path.join(output_dir, out_name)
    with open(out_path, 'w', newline='\n') as f:
        f.write(mml_text)

    print(f"Written: {out_path}  ({len(mml_text)} bytes)")

    if not args.debug:
        for _csv in [psg_log_csv, scc_log_csv, psg_trace_csv, scc_trace_csv,
                     opll_log_csv, opll_trace_csv, opll_voice_csv, opll_regs_csv]:
            try:
                if _csv and os.path.isfile(_csv):
                    os.remove(_csv)
            except OSError:
                pass


if __name__ == '__main__':
    main()
