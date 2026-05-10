from __future__ import annotations

import os
import subprocess
import sys


def test_ticks_per_step_less_than_two_is_rejected():
    repo_root = os.path.dirname(os.path.dirname(__file__))
    script = os.path.join(repo_root, 'vgm2ms2.py')
    proc = subprocess.run(
        [sys.executable, script, '--ticks-per-step', '1'],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert '--ticks-per-step must be >= 2' in proc.stderr
