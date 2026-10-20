"""'Reading the script' signals: gaze classifier and flat-voice detector."""
import numpy as np

import prosody
import vision


def test_gaze_detects_looking_down_and_ignores_blinks():
    rng = np.random.default_rng(0)
    times, feats = list(np.arange(0, 60, 0.2)), []
    for t in times:
        f = [0.30 + rng.normal(0, 0.005), 0.05 + rng.normal(0, 0.002),
             0.5 + rng.normal(0, 0.02), 0.5 + rng.normal(0, 0.01)]
        if 20 <= t < 30:            # looking down at notes
            f[0] += 0.08
            f[1] -= 0.02
        if abs(t - 10) < 0.1:       # a blink
            f[1] = 0.01
        feats.append(None if 45 <= t < 47 else f)   # face out of frame
    t_arr, away = vision.classify({"times": times, "feats": feats})
    regions = vision.away_regions(t_arr, away)
    assert len(regions) == 2
    assert abs(regions[0][0] - 20) < 0.5 and abs(regions[0][1] - 30) < 0.5
    assert abs(regions[1][0] - 45) < 0.5


def test_gaze_skipped_without_face():
    assert vision.classify({"times": [0, 1, 2], "feats": [None, None, None]}) is None


def test_flat_voice():
    sr = 16000

    def tone(f0, dur):
        f = np.linspace(f0[0], f0[1], int(dur * sr))
        ph = 2 * np.pi * np.cumsum(f) / sr
        return (0.3 * np.sin(ph) + 0.15 * np.sin(2 * ph)).astype(np.float32)

    audio, words, phrases, t = [], [], [], 0.0
    for k in range(12):
        flat = k in (4, 9)
        seg = tone((150, 152) if flat else (110 + 20 * (k % 3), 230), 3.0)
        n = 5 if flat else 9                   # flat phrases are also slower
        idx = []
        for j in range(n):
            words.append({"s": t + j * 3.0 / n, "e": t + (j + 1) * 3.0 / n - 0.02, "t": "x", "n": "x"})
            idx.append(len(words) - 1)
        phrases.append(idx)
        audio += [seg, np.zeros(int(0.5 * sr), np.float32)]
        t += 3.5
    feats = prosody.phrase_features(np.concatenate(audio), sr, 0.0, words, phrases)
    flags = prosody.flat_flags(feats)
    assert [i for i, f in enumerate(flags) if f] == [4, 9]
