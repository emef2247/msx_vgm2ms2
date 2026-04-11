#!/bin/bash

mv ym2413_3ch_test.mml 3chtest.mml
mv ym2413_3ch_test.vgm 3chtest.vgm
mv ym2413_block_boundary.mml blockb.mml
mv ym2413_block_boundary.vgm blockb.vgm
mv ym2413_chords_mix.mml chmix.mml
mv ym2413_chords_mix.vgm chmix.vgm
mv ym2413_highlow_range.mml highlow.mml
mv ym2413_highlow_range.vgm highlow.vgm
mv ym2413_legato_patch_mix.mml legato.mml
mv ym2413_legato_patch_mix.vgm legato.vgm
mv ym2413_patch_change_midnote.mml midnote.mml
mv ym2413_patch_change_midnote.vgm midnote.vgm
mv ym2413_redundant_fnum_writes.mml fwrites.mml
mv ym2413_redundant_fnum_writes.vgm fwrites.vgm
mv ym2413_release_retrigger.mml relretrg.mml
mv ym2413_release_retrigger.vgm relretrg.vgm
mv ym2413_retrigger.mml retrig.mml
mv ym2413_retrigger.vgm retrig.vgm
mv ym2413_rhythm_mode_basic.mml rhythmb.mml
mv ym2413_rhythm_mode_basic.txt rhythmb.txt
mv ym2413_rhythm_mode_basic.vgm rhythmb.vgm
mv ym2413_rhythm_mode_toggle.mml rhythmtgl.mml
mv ym2413_rhythm_mode_toggle.vgm rhythmtgl.vgm
mv ym2413_scale_chromatic.mml  scalech.mml
mv ym2413_scale_chromatic.txt  scalech.txt
mv ym2413_scale_chromatic.vgm  scalech.vgm
mv ym2413_scale_rom1.mml scalerom.mml
mv ym2413_scale_rom1.vgm scalerom.vgm
mv ym2413_short_pulses.mml shortpl.mml
mv ym2413_short_pulses.vgm shortpl.vgm
mv ym2413_volume_sweep.mml volsweep.mml
mv ym2413_volume_sweep.vgm volsweep.vgm

base64 3chtest.vgm > 3chtest.vgm.base64
base64 blockb.vgm > blockb.vgm.base64
base64 chmix.vgm > chmix.vgm.base64
base64 highlow.vgm > highlow.vgm.base64
base64 legato.vgm > legato.vgm.base64
base64 midnote.vgm > midnote.vgm.base64
base64 fwrites.vgm > fwrites.vgm.base64
base64 relretrg.vgm > relretrg.vgm.base64
base64 retrig.vgm > retrig.vgm.base64
base64 rhythmb.vgm > rhythmb.vgm.base64
base64 rhythmtgl.vgm > rhythmtgl.vgm.base64
base64 scalech.vgm > scalech.vgm.base64
base64 scalerom.vgm > scalerom.vgm.base64
base64 shortpl.vgm > shortpl.vgm.base64
base64 volsweep.vgm > volsweep.vgm.base64



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