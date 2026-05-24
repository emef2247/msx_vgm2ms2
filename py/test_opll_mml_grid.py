from __future__ import annotations

from opll_mml_grid import _grid_to_pattern_mml, _steps_to_lengths


def _default_state():
    return (0, 0, 0, 15, False, '', 0)


def test_steps_to_lengths_supports_dotted_and_whole_tie_split():
    assert _steps_to_lengths(1) == ['16']
    assert _steps_to_lengths(3) == ['8.']
    assert _steps_to_lengths(5) == ['4', '16']
    assert _steps_to_lengths(17) == ['1', '16']


def test_grid_to_pattern_mml_outputs_pattern_lines_and_rhythm_merge():
    total_steps = 16
    grid = {}

    for ch in range(14):
        for step in range(total_steps):
            grid[(ch, step)] = _default_state()

    # ch0(track 9): C4 for 4 steps, then rest
    grid[(0, 0)] = (1, 36, 1, 0, True, '@3', 0)
    for s in range(1, 4):
        grid[(0, s)] = (1, 36, 1, 0, False, '@3', 0)

    # rhythm: bd+sd at the same step
    grid[(9, 0)] = (1, 0, 0, 15, True, '', 0)
    grid[(10, 0)] = (1, 0, 0, 15, True, '', 0)

    mml = _grid_to_pattern_mml(grid, n_patterns=1, stem='unit', user_patches={}, voice_table={}, warnings=[])

    assert '; === Pattern 0 ===' in mml
    assert '\n9 @3 v15 o4 c4 r2.\n' in mml
    assert '\na r1\n' in mml
    assert '\nb r1\n' in mml
    assert '\nc r1\n' in mml
    assert '\nd r1\n' in mml
    assert '\ne r1\n' in mml
    assert '\nf bs16 r2. r8.\n' in mml


def test_grid_to_pattern_mml_prefers_relative_octave_changes():
    total_steps = 16
    grid = {}
    for ch in range(14):
        for step in range(total_steps):
            grid[(ch, step)] = _default_state()

    # C4 (2 steps) -> C5 (2 steps)
    grid[(0, 0)] = (1, 36, 1, 0, True, '@1', 0)
    grid[(0, 1)] = (1, 36, 1, 0, False, '@1', 0)
    grid[(0, 2)] = (1, 48, 1, 0, True, '@1', 0)
    grid[(0, 3)] = (1, 48, 1, 0, False, '@1', 0)

    mml = _grid_to_pattern_mml(grid, n_patterns=1, stem='unit', user_patches={}, voice_table={}, warnings=[])

    assert '9 @1 v15 o4 c8 > c8' in mml
