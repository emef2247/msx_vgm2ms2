#!/bin/bash

# vgm2ms2.pyでVGMをMS2に変換
python3 vgm2ms2.py --outdir MS2TEST/OPLL/T01ALL MS2TEST/OPLL/T01ALL/reference/T01ALL.vgm
python3 vgm2ms2.py --outdir MS2TEST/OPLL/T01P01 MS2TEST/OPLL/T01P01/reference/T01P01.vgm
python3 vgm2ms2.py --outdir MS2TEST/OPLL/T01P02 MS2TEST/OPLL/T01P02/reference/T01P02.vgm
python3 vgm2ms2.py --outdir MS2TEST/OPLL/T01P03 MS2TEST/OPLL/T01P03/reference/T01P03.vgm
python3 vgm2ms2.py --outdir MS2TEST/OPLL/T01P04 MS2TEST/OPLL/T01P04/reference/T01P04.vgm
python3 vgm2ms2.py --outdir MS2TEST/OPLL/T01P05 MS2TEST/OPLL/T01P05/reference/T01P05.vgm
python3 vgm2ms2.py --outdir MS2TEST/OPLL/T01P06 MS2TEST/OPLL/T01P06/reference/T01P06.vgm

# py/ms2_dump.pyで変換したMS2の内容を.dump(テキスト)としてダンプ // MS2のファイル内容確認用
python3 py/ms2_dump.py MS2TEST/OPLL/T01ALL/T01ALL.MS2 > MS2TEST/OPLL/T01ALL/T01ALL.MS2.dump
python3 py/ms2_dump.py MS2TEST/OPLL/T01P01/T01P01.MS2 > MS2TEST/OPLL/T01P01/T01P01.MS2.dump
python3 py/ms2_dump.py MS2TEST/OPLL/T01P02/T01P02.MS2 > MS2TEST/OPLL/T01P02/T01P02.MS2.dump
python3 py/ms2_dump.py MS2TEST/OPLL/T01P03/T01P03.MS2 > MS2TEST/OPLL/T01P03/T01P03.MS2.dump
python3 py/ms2_dump.py MS2TEST/OPLL/T01P04/T01P04.MS2 > MS2TEST/OPLL/T01P04/T01P04.MS2.dump
python3 py/ms2_dump.py MS2TEST/OPLL/T01P05/T01P05.MS2 > MS2TEST/OPLL/T01P05/T01P05.MS2.dump
python3 py/ms2_dump.py MS2TEST/OPLL/T01P06/T01P06.MS2 > MS2TEST/OPLL/T01P06/T01P06.MS2.dump

# リファレンスデータ（MS2で手動で作成)の内容
 python3 py/ms2_dump.py MS2TEST/OPLL/T01P01/reference/T01P01EX.MS2 > MS2TEST/OPLL/T01P01/reference/T01P01EX.MS2.dump
 python3 py/ms2_dump.py MS2TEST/OPLL/T01P02/reference/T01P02EX.MS2 > MS2TEST/OPLL/T01P02/reference/T01P02EX.MS2.dump
 python3 py/ms2_dump.py MS2TEST/OPLL/T01P03/reference/T01P03EX.MS2 > MS2TEST/OPLL/T01P03/reference/T01P03EX.MS2.dump
 python3 py/ms2_dump.py MS2TEST/OPLL/T01P04/reference/T01P04EX.MS2 > MS2TEST/OPLL/T01P04/reference/T01P04EX.MS2.dump
 python3 py/ms2_dump.py MS2TEST/OPLL/T01P05/reference/T01P05EX.MS2 > MS2TEST/OPLL/T01P05/reference/T01P05EX.MS2.dump
 python3 py/ms2_dump.py MS2TEST/OPLL/T01P06/reference/T01P06EX.MS2 > MS2TEST/OPLL/T01P06/reference/T01P06EX.MS2.dump
 python3 py/ms2_dump.py MS2TEST/OPLL/T01ALL/reference/T01ALLEX.MS2 > MS2TEST/OPLL/T01ALL/reference/T01ALLEX.MS2.dump
