#!/bin/bash


mv exp_ym2413_3ch_test_OPL2.vgm 3chtest_OPL2.vgm
mv exp_ym2413_3ch_test_OPL3.vgm 3chtest_OPL3.vgm
mv exp_ym2413_3ch_test_OPL3.txt 3chtest_OPL3.txt
mv exp_ym2413_block_boundary_OPL2.vgm blockb_OPL2.vgm
mv exp_ym2413_block_boundary_OPL3.vgm blockb_OPL3.vgm
mv exp_ym2413_block_boundary_OPL3.txt blockb_OPL3.txt
mv exp_ym2413_chords_mix_OPL2.vgm chmix_OPL2.vgm
mv exp_ym2413_chords_mix_OPL3.vgm chmix_OPL3.vgm
mv exp_ym2413_chords_mix_OPL3.txt chmix_OPL3.txt
mv exp_ym2413_highlow_range_OPL2.vgm highlow_OPL2.vgm
mv exp_ym2413_highlow_range_OPL3.vgm highlow_OPL3.vgm
mv exp_ym2413_highlow_range_OPL3.txt highlow_OPL3.txt
mv exp_ym2413_legato_patch_mix_OPL2.vgm legato_OPL2.vgm
mv exp_ym2413_legato_patch_mix_OPL3.vgm legato_OPL3.vgm
mv exp_ym2413_legato_patch_mix_OPL3.txt legato_OPL3.txt
mv exp_ym2413_patch_change_midnote_OPL2.vgm midnote_OPL2.vgm
mv exp_ym2413_patch_change_midnote_OPL3.vgm midnote_OPL3.vgm
mv exp_ym2413_patch_change_midnote_OPL3.txt midnote_OPL3.txt
mv exp_ym2413_redundant_fnum_writes_OPL2.vgm fwrites_OPL2.vgm
mv exp_ym2413_redundant_fnum_writes_OPL3.vgm fwrites_OPL3.vgm
mv exp_ym2413_redundant_fnum_writes_OPL3.txt fwrites_OPL3.txt
mv exp_ym2413_release_retrigger_OPL2.vgm relretrg_OPL2.vgm
mv exp_ym2413_release_retrigger_OPL3.vgm relretrg_OPL3.vgm
mv exp_ym2413_release_retrigger_OPL3.txt relretrg_OPL3.txt
mv exp_ym2413_retrigger_OPL2.vgm retrig_OPL2.vgm
mv exp_ym2413_retrigger_OPL3.vgm retrig_OPL3.vgm
mv exp_ym2413_retrigger_OPL3.txt retrig_OPL3.txt
mv exp_ym2413_rhythm_mode_basic_OPL2.vgm rhythmb_OPL2.vgm
mv exp_ym2413_rhythm_mode_basic_OPL3.vgm rhythmb_OPL3.vgm
mv exp_ym2413_rhythm_mode_basic_OPL3.txt rhythmb_OPL3.txt
mv exp_ym2413_rhythm_mode_toggle_OPL2.vgm rhythmtgl_OPL2.vgm
mv exp_ym2413_rhythm_mode_toggle_OPL3.vgm rhythmtgl_OPL3.vgm
mv exp_ym2413_rhythm_mode_toggle_OPL3.txt rhythmtgl_OPL3.txt
mv exp_ym2413_scale_chromatic_OPL2.vgm scalech_OPL2.vgm
mv exp_ym2413_scale_chromatic_OPL3.vgm scalech_OPL3.vgm
mv exp_ym2413_scale_chromatic_OPL3.txt scalech_OPL3.txt
mv exp_ym2413_scale_rom1_OPL2.vgm scalerom_OPL2.vgm
mv exp_ym2413_scale_rom1_OPL3.vgm scalerom_OPL3.vgm
mv exp_ym2413_scale_rom1_OPL3.txt scalerom_OPL3.txt
mv exp_ym2413_short_pulses_OPL2.vgm shortpl_OPL2.vgm
mv exp_ym2413_short_pulses_OPL3.vgm shortpl_OPL3.vgm
mv exp_ym2413_short_pulses_OPL3.txt shortpl_OPL3.txt
mv exp_ym2413_volume_sweep_OPL2.vgm volsweep_OPL2.vgm
mv exp_ym2413_volume_sweep_OPL3.vgm volsweep_OPL3.vgm
mv exp_ym2413_volume_sweep_OPL3.txt volsweep_OPL3.txt

base64 blockb_OPL2.vgm > blockb_OPL2.vgm.base64
base64 blockb_OPL3.vgm > blockb_OPL3.vgm.base64
base64 chmix_OPL2.vgm  > chmix_OPL2.vgm.base64
base64 chmix_OPL3.vgm  > chmix_OPL3.vgm.base64
base64 fwrites_OPL2.vgm > fwrites_OPL2.vgm.base64
base64 fwrites_OPL3.vgm > fwrites_OPL3.vgm.base64
base64 highlow_OPL2.vgm > highlow_OPL2.vgm.base64
base64 highlow_OPL3.vgm > highlow_OPL3.vgm.base64
base64 legato_OPL2.vgm > legato_OPL2.vgm.base64
base64 legato_OPL3.vgm > legato_OPL3.vgm.base64
base64 midnote_OPL2.vgm > midnote_OPL2.base64
base64 midnote_OPL3.vgm > midnote_OPL3.base64
base64 relretrg_OPL2.vgm > relretrg_OPL2.base64
base64 relretrg_OPL3.vgm > relretrg_OPL3.base64
base64 retrig_OPL2.vgm > retrig_OPL2.vgm.base64
base64 retrig_OPL3.vgm > retrig_OPL3.vgm.base64
base64 rhythmb_OPL2.vgm > rhythmb_OPL2.vgm.base64
base64 rhythmb_OPL3.vgm > rhythmb_OPL3.vgm.base64
base64 rhythmtgl_OPL2.vgm > rhythmtgl_OPL2.vgm.base64
base64 rhythmtgl_OPL3.vgm > rhythmtgl_OPL3.vgm.base64
base64 scalech_OPL2.vgm > scalech_OPL2.vgm.base64
base64 scalech_OPL3.vgm > scalech_OPL3.vgm.base64
base64 scalerom_OPL2.vgm > scalerom_OPL2.vgm.base64
base64 scalerom_OPL3.vgm > scalerom_OPL3.vgm.base64
base64 shortpl_OPL2.vgm > shortpl_OPL2.vgm.base64
base64 shortpl_OPL3.vgm > shortpl_OPL3.vgm.base64
base64 volsweep_OPL2.vgm > volsweep_OPL2.vgm.base64
base64 volsweep_OPL3.vgm > volsweep_OPL3.vgm.base64
