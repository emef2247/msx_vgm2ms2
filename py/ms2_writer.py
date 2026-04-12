# existing code above remains unchanged

# Modify OPLL volume mapping to MS2
# Changing opll_vol_to_ms2_vol to map 0-15 to 0-63

def opll_vol_to_ms2_vol(v):
    ms2 = int(((v+1)/16)*64 - 1)
    return max(0, min(ms2, 63))