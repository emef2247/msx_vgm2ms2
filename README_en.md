# vgm2ms2

A script that converts VGM files (YM2413 / OPLL) into .MS2 binaries for playback in MoonSound Music Studio (MS²).

## Overview

- Exports YM2413 OPLL FM channels only (PSG and SCC are ignored)
- PAL/NTSC-aware tempo and quantization calculations
- Uses the OPL4 reference instrument library (MoonBlaster v1.4)

## Usage

```bash
python vgm2ms2.py --outdir <output directory> <input vgm>
```

### Example

```bash
python vgm2ms2.py --outdir out/ mysong.vgm
```

Output: `out/mysong.MS2`

## Options

| Option | Description |
|--------|-------------|
| `--outdir OUTDIR` | Output directory (default: `outputs/<stem>/`) |
| `--bpm BPM` | Song tempo in BPM (default: 120) |
| `--timer 0\|1` | Timer source: `0` = VDP timer (default), `1` = 50 Hz fixed |
| `--video ntsc\|pal` | Video system: `ntsc` (default) or `pal`. Only used when `--timer 0` |
| `--vol-map legacy\|db` | Volume mapping mode for VGM→MS2 export (default: `legacy`). `legacy` preserves original linear scaling (range 0–40). `db` uses a physically-accurate dB mapping for better loudness preservation (range 0–60). |
| `--force_stereo` | Pan active channels alternately Left/Right to create pseudo-stereo output |
| `--debug` | Print timing diagnostics (freq_hz, timer divider, quantization, implied BPM) |
| `--no-validate` | Skip post-build validation of the Patterns chunk |

### `--force_stereo`

When specified, channels that contain notes are sorted by channel number and
assigned Left (−7) and Right (+7) stereo pan values alternately.  Channels
without any notes remain at center (0).  Without this flag all channels use
the default center panning (backward compatible).

```bash
python vgm2ms2.py mysong.vgm --force_stereo
```

Examples:
- Active channels ch2, ch5 → ch2 = L, ch5 = R
- Active channels ch0, ch3, ch7 → ch0 = L, ch3 = R, ch7 = L

MS² FM channel pan values: −7 to −3 = Left, −2 to +2 = Center, +3 to +7 = Right.

### `--vol-map`

Selects the OPLL volume (0–15) → MS2 TL (0–63) mapping used during VGM→MS2 export.
This option only affects the VGM→MS2 path; MML output is not changed.

| Mode | Description |
|------|-------------|
| `db` (default) | Physically-accurate dB mapping: 1 OPLL step = 3 dB = 4 OPL4 TL steps. Output range 0–63. Equal-loudness compensation (curve A) is applied automatically. |
| `legacy` | Original linear scaling `round(v × 8/3)`. Output range 0–40. Bit-identical to previous behaviour. |

**Equal-loudness compensation (curve A)** is a perceptual layer applied on top of the `db`
baseline conversion.  Motivated by the Fletcher–Munson equal-loudness curves, it boosts
low and high frequencies that the human ear perceives as quieter at the same physical level:

| Band | MS2 note range | Offset |
|------|----------------|--------|
| low  | 0–35 (below C3, < ~131 Hz) | +2 |
| mid  | 36–71 (C3–B5, ~131–988 Hz) |  0 |
| high | 72–95 (C6 and above, ≥ ~1047 Hz) | +3 |

The offset is added to the encoded MS2 volume value (0x05 domain) and the result is
clamped to the valid range 0..63.

```bash
# Default (db mode with equal-loudness compensation)
python vgm2ms2.py mysong.vgm

# Legacy (backward-compatible, no compensation)
python vgm2ms2.py mysong.vgm --vol-map legacy
```

### `--timer` and `--video` details

- `--timer 0` (VDP timer):
  - `--video ntsc` (default) → uses NTSC VDP interrupt frequency 59.988527... Hz
  - `--video pal` → uses PAL VDP interrupt frequency 50.214039... Hz
- `--timer 1` (50 Hz fixed):
  - `--video` is ignored; always uses 50.214039... Hz

Tempo formulas:
- `SongSettings+68 (timer_divider_raw) = round(freq_hz × 3840 / bpm)`
- Quantization step `ticks_per_step = max(1, round(freq_hz × 15 / bpm))`

## License

MIT License
