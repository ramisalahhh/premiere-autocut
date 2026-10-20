"""End-to-end: run analyze.py as the panel does, with a cached transcript."""
import json
import os
import subprocess
import sys
import wave

import numpy as np

from conftest import ENGINE

WORDS = [("أنا", 0.5, 0.8), ("النهاردة", 0.85, 1.3), ("هتكلم", 1.35, 1.8),
         ("انا", 3.0, 3.3), ("النهارده", 3.35, 3.8), ("هتكلم", 3.85, 4.3), ("عن", 4.35, 4.5),
         ("الدايت.", 4.55, 5.0)]


def test_cli_roundtrip(tmp_path):
    sr, dur = 16000, 7.0
    rng = np.random.default_rng(1)
    a = rng.normal(0, 0.001, int(dur * sr))
    for _, s, e in WORDS:
        a[int(s * sr):int(e * sr)] += rng.normal(0, 0.2, int(e * sr) - int(s * sr))
    wav = tmp_path / "clip.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())

    sys.path.insert(0, ENGINE)
    import analyze
    lo, hi = 0.0, 7.0 + 0.5
    hi = lo + len(analyze.decode_range(str(wav), lo, hi)) / 16000
    S = analyze.DEFAULTS
    key = analyze.cache_key(str(wav), lo, hi, "words-%s-%s" % (S["model"], S["language"]))
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / (key + ".json")).write_text(json.dumps(
        [{"s": s, "e": e, "t": t, "p": 0.9} for t, s, e in WORDS], ensure_ascii=False), encoding="utf-8")

    job = {"clips": [{"index": 0, "nodeId": "n", "name": "clip.wav", "path": str(wav),
                      "in": 0.0, "out": 7.0, "start": 0.0, "end": 7.0}],
           "settings": {"reading_mode": "off"}, "cacheDir": str(cache)}
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    out = tmp_path / "out.json"
    p = subprocess.run([sys.executable, "-u", os.path.join(ENGINE, "analyze.py"),
                        "--job", str(tmp_path / "job.json"), "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "DONE" in p.stdout and "using cached transcript" in p.stdout

    r = json.loads(out.read_text(encoding="utf-8"))
    assert r["summary"]["counts"]["retake"] == 1
    (piece,) = r["pieces"]
    assert 2.8 < piece["s"] < 3.0 and 5.0 < piece["e"] < 5.4
