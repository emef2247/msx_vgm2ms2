    # Set byte 65 to max(1, min(255, n_patterns-1)) because MS² expects
    # song length to be the last pattern index (patterns-1) even when
    # patterns count includes extra pattern(s).
    song_settings_data[65] = max(1, min(255, n_patterns - 1))
