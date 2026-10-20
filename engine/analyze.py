#!/usr/bin/env python3
"""AutoCut analysis engine.

Called by the Premiere panel:
    python -u analyze.py --job job.json --out result.json

job.json = {"clips": [{index, nodeId, name, path, in, out, start, end}, ...],
            "settings": {...}, "cacheDir": "..."}

Prints machine-readable lines on stdout:
    PROGRESS <0..1> <message>
    LOG <message>
    DONE
"""
import argparse
import glob
import hashlib
import json
import os
import site
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import detectors as D  # noqa: E402
import plan as P  # noqa: E402

SR = 16000
CACHE_VERSION = "v1"

DEFAULTS = {
    "model": "large-v3",
    "language": "ar",
    "initial_prompt": "ده فيديو بالعامية المصرية، والكلام طبيعي وعفوي.",
    "cut_gaps": True,
    "min_gap": 0.5,          # seconds of silence before it's cut
    "pad": 0.12,             # breathing room kept around speech at each gap
    "edge_pad": 0.25,        # room kept before first / after last word
    "trim_ends": True,
    "silence_margin_db": 10.0,
    "cut_retakes": True,
    "retake_similarity": 80,  # 0..100
    "retake_lookahead": 6,
    "retake_max_span": 25.0,
    "phrase_gap": 0.30,
    "cut_stutters": True,
    "cut_fillers": True,
    "noise_mode": "mark",     # off | mark | cut
    "reading_mode": "auto",   # off | auto | mark | cut
    "gaze_z": 4.0,            # lower = more sensitive
    "gaze_fraction": 0.6,
    "flat_pitch_z": -1.0,
    "flat_rate_z": -0.5,
    "cut_mistakes": True,     # "افف انا نسيت الكلام" etc.
    "script": "",             # optional script text
    "script_match": 75,       # how close a phrase must be to count as on-script
    "script_offscript": 50,   # below this (best of match / word coverage) = off-script
    "script_offscript_cut": 35,  # "Cut them" only cuts below this...
    "offscript_max_words": 8,    # ...and only phrases this short
    "off_script_mode": "mark",  # mark | cut | off
    "min_keep": 0.25,
    "merge_join": 0.08,
}


# ------------------------------------------------------------------ output
def emit(kind, msg=""):
    sys.stdout.write("%s %s\n" % (kind, msg))
    sys.stdout.flush()


def log(msg):
    emit("LOG", msg)


class Progress:
    def __init__(self):
        self.lo, self.hi = 0.0, 1.0

    def span(self, lo, hi):
        self.lo, self.hi = lo, hi

    def __call__(self, frac, msg):
        p = self.lo + (self.hi - self.lo) * max(0.0, min(1.0, frac))
        emit("PROGRESS", "%.3f %s" % (p, msg))


PROG = Progress()


# ------------------------------------------------------------------- cache
def cache_key(path, lo, hi, extra):
    st = os.stat(path)
    raw = "|".join([os.path.abspath(path), str(st.st_size), str(int(st.st_mtime)),
                    "%.2f" % lo, "%.2f" % hi, extra, CACHE_VERSION])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def cache_load(cache_dir, name):
    p = os.path.join(cache_dir, name + ".json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def cache_save(cache_dir, name, data):
    os.makedirs(cache_dir, exist_ok=True)
    with open(os.path.join(cache_dir, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


# ---------------------------------------------------------------- whisper
def _add_cuda_dlls():
    """Make pip-installed NVIDIA cuBLAS/cuDNN DLLs visible on Windows."""
    if os.name != "nt":
        return
    roots = list(site.getsitepackages())
    try:
        roots.append(site.getusersitepackages())
    except Exception:
        pass
    for root in roots:
        for d in glob.glob(os.path.join(root, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
            except Exception:
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


_MODEL = {}


def get_model(name):
    if name in _MODEL:
        return _MODEL[name]
    _add_cuda_dlls()
    from faster_whisper import WhisperModel
    try:
        m = WhisperModel(name, device="cuda", compute_type="float16")
        log("Whisper %s loaded on GPU (CUDA)." % name)
    except Exception as e:
        log("GPU not available (%s) - falling back to CPU, this will be slower." % str(e).splitlines()[0])
        m = WhisperModel(name, device="cpu", compute_type="int8")
    _MODEL[name] = m
    return m


def transcribe(audio, t0, S):
    model = get_model(S["model"])
    dur = len(audio) / SR
    segments, info = model.transcribe(
        audio,
        language=S["language"] or None,
        word_timestamps=True,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300, "speech_pad_ms": 200},
        beam_size=5,
        initial_prompt=S["initial_prompt"] or None,
    )
    words = []
    for seg in segments:
        PROG(seg.end / max(dur, 1e-6), "Transcribing %d:%02d / %d:%02d"
             % (seg.end // 60, seg.end % 60, dur // 60, dur % 60))
        for w in (seg.words or []):
            words.append({"s": round(t0 + w.start, 3), "e": round(t0 + w.end, 3),
                          "t": w.word, "p": round(float(w.probability), 3)})
    return words


# --------------------------------------------------------------- analysis
def decode_range(path, lo, hi):
    """Decode [lo, hi] seconds of the file's audio to 16 kHz mono float32.
    Uses PyAV directly (works with any PyAV version)."""
    import av

    chunks, got = [], 0
    with av.open(path, mode="r") as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise RuntimeError("No audio track in %s" % os.path.basename(path))
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)
        need = int((hi - lo) * SR)
        skip = int(lo * SR)
        if lo > 1.0 and stream.time_base:
            container.seek(int((lo - 1.0) / stream.time_base), stream=stream)
            skip = None  # measure from frame timestamps after seeking
        for frame in container.decode(stream):
            if skip is None:
                ft = float(frame.pts * stream.time_base) if frame.pts is not None else 0.0
                skip = max(0, int((lo - ft) * SR))
            out = resampler.resample(frame)
            for f in (out if isinstance(out, list) else [out]):
                if f is None:
                    continue
                x = f.to_ndarray().reshape(-1)
                if skip:
                    drop = min(skip, len(x))
                    x, skip = x[drop:], skip - drop
                if len(x):
                    chunks.append(x)
                    got += len(x)
            if got >= need:
                break
    if not chunks:
        raise RuntimeError("Could not decode audio from %s" % os.path.basename(path))
    audio = np.concatenate(chunks)[:need].astype(np.float32) / 32768.0
    return np.ascontiguousarray(audio)


def analyze_media(path, lo, hi, S, cache_dir, p_lo, p_hi):
    name = os.path.basename(path)
    PROG.span(p_lo, p_lo + (p_hi - p_lo) * 0.05)
    PROG(0, "Reading audio: %s" % name)
    audio = decode_range(path, lo, hi)
    t0 = lo
    hi = t0 + len(audio) / SR

    # energy / silence
    db = D.energy_db(audio, SR)
    thr = D.speech_threshold(db, S["silence_margin_db"])
    act = D.activity_mask(db, thr)
    log("%s: speech threshold %.1f dBFS" % (name, thr))

    # transcript (cached)
    key = cache_key(path, lo, hi, "words-" + S["model"] + "-" + (S["language"] or "auto"))
    words = cache_load(cache_dir, key)
    PROG.span(p_lo + (p_hi - p_lo) * 0.05, p_lo + (p_hi - p_lo) * 0.65)
    if words is None:
        PROG(0, "Loading Whisper model (first time downloads ~3 GB)...")
        words = transcribe(audio, t0, S)
        cache_save(cache_dir, key, words)
    else:
        log("%s: using cached transcript (%d words)" % (name, len(words)))

    # clean words: normalize, drop empty + hallucinations over silence
    clean, dropped = [], 0
    for w in words:
        w = dict(w)
        w["n"] = D.normalize(w["t"])
        if not w["n"]:
            continue
        if D.active_fraction(act, t0, w["s"], w["e"]) < 0.15 and w["p"] < 0.6:
            dropped += 1
            continue
        clean.append(w)
    if dropped:
        log("%s: ignored %d low-confidence words over silence" % (name, dropped))

    result = {"words": clean, "audio": audio, "sr": SR, "t0": t0,
              "gaps": D.find_gaps(act, t0, S["min_gap"], S["pad"]),
              "noise": D.detect_noise(act, t0, clean), "gaze": None}

    # gaze (cached raw features)
    PROG.span(p_lo + (p_hi - p_lo) * 0.65, p_hi)
    if S["reading_mode"] != "off":
        vkey = cache_key(path, lo, hi, "gaze5")
        data = cache_load(cache_dir, vkey)
        if data is None:
            try:
                import vision
                PROG(0, "Tracking face / eyes...")
                data = vision.extract_features(path, lo, hi, 5.0,
                                               lambda f: PROG(f, "Tracking face / eyes %d%%" % (f * 100)))
                cache_save(cache_dir, vkey, data)
            except Exception as e:
                log("Gaze tracking skipped: %s" % e)
                data = None
        if data:
            import vision
            g = vision.classify(data, z_thr=S["gaze_z"])
            if g is None:
                log("%s: no face found most of the time - gaze check skipped" % name)
            result["gaze"] = g
    PROG(1, "Analyzed %s" % name)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.job, encoding="utf-8") as f:
        job = json.load(f)
    S = dict(DEFAULTS)
    S.update({k: v for k, v in (job.get("settings") or {}).items() if v is not None})
    cache_dir = job.get("cacheDir") or os.path.join(HERE, "cache")
    clips = sorted(job["clips"], key=lambda c: c["start"])
    if not clips:
        raise SystemExit("No clips to analyze.")

    by_media = {}
    for c in clips:
        by_media.setdefault(c["path"], []).append(c)

    t_start = time.time()
    analyses = {}
    n = len(by_media)
    for k, (path, cl) in enumerate(by_media.items()):
        if not os.path.exists(path):
            log("Media file not found, skipping: %s" % path)
            continue
        lo = max(0.0, min(c["in"] for c in cl) - 0.5)
        hi = max(c["out"] for c in cl) + 0.5
        try:
            analyses[path] = analyze_media(path, lo, hi, S, cache_dir,
                                           0.95 * k / n, 0.95 * (k + 1) / n)
        except Exception as e:
            log("Failed on %s: %s" % (os.path.basename(path), e))
            log(traceback.format_exc().replace("\n", " | "))

    PROG.span(0.95, 1.0)
    PROG(0, "Planning cuts...")
    result = P.build_plan(clips, analyses, S)
    result["elapsed"] = round(time.time() - t_start, 1)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    PROG(1, "Done")
    emit("DONE")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log("FATAL: %s" % e)
        log(traceback.format_exc().replace("\n", " | "))
        sys.exit(1)
