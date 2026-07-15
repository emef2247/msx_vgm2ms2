# vgm2ms2

YM2413（OPLL）を使用した VGM ファイルを、MoonSound Music Studio（MS²）用の .MS2 バイナリに変換するスクリプトです。

## 機能概要

- YM2413 OPLL FM チャンネルのみをエクスポート（PSG・SCC は無視）
- NTSC・PAL のビデオシステムに応じたテンポ・量子化計算
- OPL4 参照音色ライブラリ（MoonBlaster v1.4）を使用

## 使い方

```bash
python vgm2ms2.py --outdir <出力ディレクトリ> <入力VGMファイル>
```

### 基本例

```bash
python vgm2ms2.py --outdir out/ mysong.vgm
```

出力例: `out/mysong.MS2`

## オプション

| オプション | 説明 |
|-----------|------|
| `--outdir OUTDIR` | 出力ディレクトリを指定（省略時: `outputs/<stem>/`） |
| `--bpm BPM` | テンポを BPM で指定（デフォルト: 120） |
| `--timer 0\|1` | タイマー選択: `0` = VDP タイマー（デフォルト）、`1` = 50 Hz 固定 |
| `--video ntsc\|pal` | ビデオシステム: `ntsc`（デフォルト）または `pal`。`--timer 0` のときのみ有効 |
| `--force_stereo` | 使用中のチャンネルを左右交互（L, R, L, R…）にパンしてステレオ化 |
| `--debug` | デバッグ情報（周波数・タイマ除数・量子化）を表示 |
| `--no-validate` | 生成後の Patterns チャンク検証をスキップ |

### `--force_stereo` の説明

指定すると、実際にノート／イベントが存在するチャンネルをチャンネル番号の昇順にソートし、
Left（-7）・Right（+7）を交互に割り当てて疑似ステレオ化します。

```bash
python vgm2ms2.py mysong.vgm --force_stereo
```

- 使用チャンネルが ch2, ch5 の場合: ch2 → L、ch5 → R
- 使用チャンネルが ch0, ch3, ch7 の場合: ch0 → L、ch3 → R、ch7 → L
- 未使用チャンネルはセンター（0）のまま
- 未指定時は全チャンネルがセンター（既存挙動を完全維持）

MS² の FM チャンネルにおけるパン値: −7〜−3 = 左、−2〜+2 = 中央、+3〜+7 = 右。

### `--timer` と `--video` の説明

- `--timer 0`（VDP タイマー）の場合：
  - `--video ntsc`（デフォルト）→ NTSC VDP 割り込み周波数 59.988527... Hz を使用
  - `--video pal` → PAL VDP 割り込み周波数 50.214039... Hz を使用
- `--timer 1`（50 Hz 固定）の場合：
  - `--video` の設定は無視され、常に 50.214039... Hz を使用

テンポ計算式：
- `SongSettings+68 (timer_divider_raw) = round(freq_hz × 3840 / bpm)`
- 量子化ステップ数 `ticks_per_step = max(1, round(freq_hz × 15 / bpm))`

## ライセンス

MIT License
