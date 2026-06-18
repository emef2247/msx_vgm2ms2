# vgm2midi

VGM（YM2413 / OPLL）ファイルを Standard MIDI File（SMF フォーマット 0）に変換するスクリプトです。

---

## 機能概要

- YM2413 OPLL FM チャンネル（ch0〜8）をメロディとして出力
- OPLL リズムチャンネル（BD / SD / TOM / TC / HH）を GM ドラムノートとして出力
- OPLL プリセット音色を GM（General MIDI）プログラムに自動マッピング
- BPM は VGM から自動検出
- ユーザー定義音色は `<stem>.user_voice.json` で GM 音色番号を手動指定可能
- ポルタメント（`is_portamento=1`）を MIDI CC84 / CC65 / CC5 として出力

---

## 使い方

```bash
python vgm2midi.py [オプション] <入力VGMファイル>
```

### 出力ファイル

| ファイル | 説明 |
|---------|------|
| `<stem>.mid` | SMF フォーマット 0 の MIDI ファイル |
| `<stem>.user_voice.json` | ユーザー定義音色の GM 割り当て設定ファイル（ユーザー音色が存在する場合のみ生成） |

### 基本例

```bash
python vgm2midi.py mysong.vgm
```

出力: `mysong.mid`、`mysong.user_voice.json`（ユーザー音色がある場合）

---

## オプション

| オプション | 説明 |
|-----------|------|
| `--outdir DIR` | 出力ディレクトリを指定（省略時: VGM ファイルと同じディレクトリ） |
| `--ppq N` | MIDI の PPQ（クォーターノートあたりのティック数）を指定（デフォルト: 480） |
| `--debug` | デバッグ情報を表示し、中間 CSV ファイルを保持 |

---

## 基本フロー

### 1. VGM ファイルを MIDI に変換する

```bash
python vgm2midi.py test.vgm
```

`test.mid` と `test.user_voice.json`（ユーザー音色がある場合）が生成されます。

### 2. ユーザー音色のマッピングを確認・編集する

変換時にユーザー定義音色が検出されると、レジスタの設定値がコンソールに表示されます。

```
User patch Inst 20 (1-indexed:21) -> GM program 20
Inst 20: 'User patch v15' regs = 00 00 05 02 F0 E0 02 FF
        TL= 5 FB=1
        MO: AR=15 DR= 0 SL= 0 RR= 2 KL=0 MT= 0 AM=0 VB=0 EG=0 KR=0 DT=0
        CA: AR=14 DR= 0 SL=15 RR=15 KL=0 MT= 0 AM=0 VB=0 EG=0 KR=0 DT=0
```

`TL` から `DT` までのテキストをコピーして、この音色に近い GM 音色を AI に尋ねてみてください。
いくつか候補を提示してもらったら、気に入った番号を `test.user_voice.json` の該当エントリの値として書き換えます。

> **GM プログラム番号は 0-indexed です。**  
> DAW などで表示される 1-indexed の番号から 1 を引いた値を設定してください。  
> 例: DAW 表示「Flute = 74」→ JSON には `73` を指定。

### 3. 再度変換する

```bash
python vgm2midi.py test.vgm
```

`test.user_voice.json` が既に存在する場合はそのマッピングが読み込まれ、指定した GM 音色が反映された `test.mid` が生成されます。`test.user_voice.json` の内容は上書きされません。

---

## ユーザー定義音色について

### user_voice.json の構造

```json
{
  "_comment": "...",
  "0000050002f0e002ff": 20,
  "_voice_info": {
    "0000050002f0e002ff": [
      "User patch Inst 20 (1-indexed:21) -> GM program 20",
      "..."
    ]
  }
}
```

| キー | 説明 |
|-----|------|
| `"<patch_hex>"` | OPLL パッチデータ（16 進数 16 文字） |
| 値（整数） | GM プログラム番号（0-indexed） |
| `_comment` | ファイル説明（自動生成・編集不要） |
| `_voice_info` | 各パッチのレジスタデコード情報（参照用・編集不要） |

`_` で始まるキーはすべて読み込み時に無視されます。

### all-zero パッチについて

レジスタへの書き込みが一切なかった場合、パッチデータは `0000000000000000` となります。
これは「未使用」を示す sentinel として自動的に **GM 127（Gunshot）** に固定され、以下のように表示されます。

```
User patch v15: all-zero (no register written) -> GM program 127 (1-indexed:128, unused)
```

このエントリは `user_voice.json` を編集しても反映されません（毎回 GM 127 に上書きされます）。

---

## OPLL → GM プログラム対応表

| OPLL プリセット | GM プログラム（0-indexed） | 音色名（目安） |
|:--------------:|:------------------------:|:-------------|
| 0（ユーザー定義） | 81 | Lead 2 (sawtooth) |
| 1 | 40 | Violin |
| 2 | 25 | Acoustic Guitar (steel) |
| 3 | 0 | Acoustic Grand Piano |
| 4 | 73 | Flute |
| 5 | 71 | Clarinet |
| 6 | 68 | Oboe |
| 7 | 56 | Trumpet |
| 8 | 19 | Church Organ |
| 9 | 60 | French Horn |
| 10 | 81 | Lead 2 (sawtooth) |
| 11 | 6 | Harpsichord |
| 12 | 11 | Vibraphone |
| 13 | 38 | Synth Bass 1 |
| 14 | 32 | Acoustic Bass |
| 15 | 27 | Electric Guitar (clean) |

ユーザー定義音色（プリセット 0）は、`user_voice.json` で個別に GM 音色を指定できます（上記の 81 はフォールバック値です）。

---

## リズム → GM ノート対応表

リズムチャンネルは MIDI チャンネル 10（0-indexed: 9）に出力されます。

| OPLL リズム | GM ノート | 説明 |
|:-----------:|:--------:|:-----|
| BD | 35 | Acoustic Bass Drum |
| SD | 38 | Acoustic Snare |
| TOM | 41 | Low Floor Tom |
| TC | 49 | Crash Cymbal 1 |
| HH | 42 | Closed Hi-Hat |

---

## ライセンス

MIT License
