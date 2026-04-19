#!/usr/bin/env python3
"""
tools/convert_opll_with_rhythm.py

Build an MS2 from an OPLL trace CSV plus its regs CSV so that:
 - rhythm instrument blobs are appended to the 2OP instrument chunk, and
 - rhythm "notes" (from parsed regs) are converted to _Segment objects and
   merged into the segments dict so they appear in MS2 patterns.

Usage:
  python3 tools/convert_opll_with_rhythm.py <trace_opll.csv> <regs_opll_regs.csv> <out.ms2>
"""
from __future__ import annotations
import argparse
import os
import sys

# Ensure repo root is importable as package 'py'
repo_root = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, repo_root)

from py import ms2_writer
from py import opll_mml

def parse_args():
    p = argparse.ArgumentParser(prog="convert_opll_with_rhythm.py")
    p.add_argument("trace_csv", help="Path to *_trace.opll.csv")
    p.add_argument("regs_csv", help="Path to *_trace.opll_regs.csv (rhythm regs)")
    p.add_argument("out_ms2", help="Output .MS2 path")
    p.add_argument("--base-ms2", help="Optional base MS2 to load instrument presets from", default=None)
    p.add_argument("--bpm", type=int, default=120, help="BPM for MS2 (default 120)")
    p.add_argument("--timer", type=int, choices=(0,1), default=0, help="Timer flag (0=VDP, 1=50Hz)")
    p.add_argument("--video", choices=("ntsc","pal"), default="ntsc", help="Video timing mode")
    p.add_argument("--debug", action="store_true", help="Enable debug prints")
    return p.parse_args()

def convert(trace_csv: str, regs_csv: str, out_ms2: str,
            base_ms2: str | None = None,
            bpm: int = 120, timer: int = 0, video: str = 'ntsc',
            debug: bool = False) -> int:
    # Build melody segments from trace CSV
    if not os.path.exists(trace_csv):
        print("Trace CSV not found:", trace_csv, file=sys.stderr)
        return 2
    print("Building segments from trace:", trace_csv)
    segments = opll_mml._build_segments(trace_csv)
    # segments is dict[int->list[_Segment]] for channels 0..NUM_CH-1

    # Parse regs CSV for rhythm info (instruments + channel events)
    rhythm_instruments = {}
    rhythm_channels = {}
    if regs_csv and os.path.exists(regs_csv):
        print("Parsing rhythm regs from:", regs_csv)
        regs_res = opll_mml.parse_opll_regs_for_rhythm(regs_csv)
        #rhythm_instruments = regs_res.get('instruments', {}) or {}
        #rhythm_channels = regs_res.get('channels', {}) or {}
        timeline = opll_mml.parse_opll_regs_for_rhythm(regs_csv)  # list[(tick, regval)]

        # timelineからチャンネルごとrhythm segmentを作る例
        from py.opll_mml import _Segment
        from collections import defaultdict

        rhythm_channels = defaultdict(list)
        for tick, regval in timeline:
            # ここでregvalから各リズムビットを判定し、chごとの_Segmentをちゃんと作る
            # BD例（詳細ロジックはMSX-OPLL仕様参照）
            bd  = (regval & 0x01)
            if bd:
                seg = _Segment(tick, 1, 0, 0, 0, 0, 0)
                seg.tick_end = tick + 3
                rhythm_channels[6].append(seg)

        # segmentsへ統合
        for ch_idx, seglist in rhythm_channels.items():
            segments.setdefault(ch_idx, []).extend(seglist)
        
        
        if debug:
            print("rhythm_instruments keys:", list(rhythm_instruments.keys()))
            for ch, segs in rhythm_channels.items():
                print(f"rhythm channel {ch}: {len(segs)} segments")
    else:
        print("Regs CSV not found (no rhythm):", regs_csv)

    # Convert rhythm channel entries into opll_mml._Segment objects and merge
    if rhythm_channels:
        print("Merging rhythm segments into segments dict ...")
        for ch_idx, seg_list in rhythm_channels.items():
            # Ensure we have a list for this channel index (may be >= NUM_CH)
            segs_here = segments.setdefault(ch_idx, [])
            for item in seg_list:
                # item expected keys: tick_start, tick_end, fnum, block, ms2_note, name
                ts = int(item.get('tick_start', 0))
                te = int(item.get('tick_end', ts + 1))
                fnum = int(item.get('fnum', 0))
                block = int(item.get('block', 0))
                # Choose inst=0 (user patch / will be overridden by starting_instruments),
                # vol=0 (max loudness)
                seg_obj = opll_mml._Segment(ts, 1, fnum, block, 0, 0)
                seg_obj.tick_end = te
                # optional: store name in at_token for debugging (not used for inst selection)
                seg_obj.at_token = ''  # leave empty; build_ms2_file will assign starting instrument
                segs_here.append(seg_obj)
        # Sort each channel's segment list by tick_start (important for _get_starting_instruments)
        for ch, segs in list(segments.items()):
            segs.sort(key=lambda s: (s.tick_start, s.tick_end))
        print("Merged rhythm segments; channels now:", sorted(segments.keys()))

    # Determine companion voice CSV (if present) to collect user patches
    voice_csv = trace_csv.replace('_trace.opll.csv', '_trace.opll_voice.csv')
    if not os.path.exists(voice_csv):
        voice_csv = None

    # Build MS2, passing rhythm_instruments so ms2_writer will append instrument records
    print("Building MS2 (BPM={}, timer={}, video={}) ...".format(bpm, timer, video))
    ms2_bytes = ms2_writer.build_ms2_file(
        segments=segments,
        voice_csv_path=voice_csv,
        stem=os.path.splitext(os.path.basename(out_ms2))[0],
        bpm=bpm,
        timer=timer,
        base_ms2=base_ms2,
        video=video,
        debug=debug,
        rhythm_instruments=rhythm_instruments or None,
    )

    with open(out_ms2, 'wb') as fh:
        fh.write(ms2_bytes)
    print("Wrote:", out_ms2)
    return 0

def main():
    args = parse_args()
    rc = convert(
        trace_csv=args.trace_csv,
        regs_csv=args.regs_csv,
        out_ms2=args.out_ms2,
        base_ms2=args.base_ms2,
        bpm=args.bpm,
        timer=args.timer,
        video=args.video,
        debug=args.debug,
    )
    sys.exit(rc)

if __name__ == "__main__":
    main()