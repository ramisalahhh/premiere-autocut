"""Voice-flatness features per phrase (used to spot 'reading the script' parts).

Reading aloud usually sounds flatter (less pitch movement) and slower than
natural talking. We measure each phrase against the speaker's own average in
the same clip, so it adapts to your voice.
"""
import numpy as np


def phrase_features(audio, sr, t0, words, phrases, min_dur=1.5, min_words=3):
    """Return a list (one per phrase) of {"pitch_st": float, "rate": float} or None."""
    try:
        import librosa
    except ImportError:
        return [None] * len(phrases)

    out = []
    for P in phrases:
        s, e = words[P[0]]["s"], words[P[-1]]["e"]
        dur = e - s
        if dur < min_dur or len(P) < min_words:
            out.append(None)
            continue
        a, b = int(max(0.0, s - t0) * sr), int(max(0.0, e - t0) * sr)
        seg = audio[a:b].astype(np.float32)
        if len(seg) < 2048:
            out.append(None)
            continue
        try:
            f0 = librosa.yin(seg, fmin=70, fmax=400, sr=sr,
                             frame_length=1024, hop_length=160)
            rms = librosa.feature.rms(y=seg, frame_length=1024, hop_length=160)[0]
        except Exception:
            out.append(None)
            continue
        n = min(len(f0), len(rms))
        f0, rms = f0[:n], rms[:n]
        voiced = (rms > rms.max() * 0.15) & (f0 > 75) & (f0 < 390)
        if voiced.sum() < 20:
            out.append(None)
            continue
        v = f0[voiced]
        semis = 12.0 * np.log2(v / np.median(v))
        out.append({"pitch_st": float(np.std(semis)), "rate": len(P) / dur})
    return out


def flat_flags(feats, pitch_z=-1.0, rate_z=-0.5, min_valid=6):
    """Mark phrases whose pitch variation AND speaking rate are below the
    speaker's own norm. Returns list[bool]."""
    valid = [f for f in feats if f]
    if len(valid) < min_valid:
        return [False] * len(feats)
    p = np.array([f["pitch_st"] for f in valid])
    r = np.array([f["rate"] for f in valid])
    pm, ps = p.mean(), p.std() + 1e-6
    rm, rs = r.mean(), r.std() + 1e-6
    flags = []
    for f in feats:
        if not f:
            flags.append(False)
            continue
        flags.append((f["pitch_st"] - pm) / ps <= pitch_z and
                     (f["rate"] - rm) / rs <= rate_z)
    return flags
