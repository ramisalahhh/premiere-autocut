"""Shared fixtures. The engine is a flat folder of modules (it ships inside the
CEP extension), so we put it on sys.path instead of installing a package."""
import os
import sys

import pytest

ENGINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine")
sys.path.insert(0, ENGINE)

import detectors as D  # noqa: E402


def make_words(spoken):
    """[(phrase_text, start, end), ...] -> word dicts spread evenly in each phrase."""
    words = []
    for text, s, e in spoken:
        ws = text.split()
        d = (e - s) / len(ws)
        for i, w in enumerate(ws):
            words.append({"s": round(s + i * d, 3), "e": round(s + (i + 1) * d - 0.05, 3),
                          "t": w, "n": D.normalize(w), "p": 0.9})
    return words


def analysis(words, gaps=None, noise=None):
    return {"words": words, "audio": None, "sr": 16000, "t0": 0.0,
            "gaps": gaps or [], "noise": noise or [], "gaze": None}


def clip(dur, start=0.0, path="p"):
    return {"index": 0, "nodeId": "n", "name": "clip", "path": path,
            "in": 0.0, "out": dur, "start": start, "end": start + dur}


def decisions(result):
    """{phrase text: 'keep' | cut reason} from a build_plan() result."""
    return {p["text"]: (p["cut"] or "keep") for p in result["transcript"]}


@pytest.fixture
def settings():
    import analyze
    s = dict(analyze.DEFAULTS)
    s["reading_mode"] = "off"
    return s
