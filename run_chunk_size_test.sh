#!/bin/bash

python3 vgm2ms2.py --debug --outdir MS2TEST/OPLL/DEBUG4 MS2TEST/OPLL/Aleste2/reference/Alest202.vgm
python3 py/ms2_dump.py  MS2TEST/OPLL/DEBUG4/Alest202.MS2 >  MS2TEST/OPLL/DEBUG4/Alest202.MS2.dump







