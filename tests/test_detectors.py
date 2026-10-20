import numpy as np
import pytest

import detectors as D
from conftest import make_words


@pytest.mark.parametrize("raw,expected", [
    ("أنا", "انا"),
    ("إسلام", "اسلام"),
    ("النهاردة", "النهارده"),
    ("مستشفى", "مستشفي"),
    ("مَرْحَبًا", "مرحبا"),
    ("Hello, World!", "hello world"),
])
def test_normalize(raw, expected):
    assert D.normalize(raw) == expected


@pytest.mark.parametrize("text,kind", [
    ("افف انا نسيت الكلام", "strong"),
    ("ننسيت الكلام", "strong"),          # Whisper typo
    ("يوووه خليني أعيد", "strong"),
    ("ستوب ستوب", "strong"),
    ("اتلخبطت", "strong"),
    ("استنى ثانية واحدة", "weak"),
    ("معلش", "weak"),
    ("لو نسيت تاكل بعد التمرين", "weak"),   # real content -> only a review marker
    ("دي الطريقة الغلط", None),            # "الغلط" is not the cue "غلط"
    ("وهتزيد اخشن بكتير من الاول", None),  # "من الاول" is normal speech
    ("التمرين ده مهم", None),
])
def test_meta_cue(text, kind):
    assert D.meta_cue(D.normalize(text))[0] == kind


@pytest.mark.parametrize("word,is_filler", [
    ("امم", True), ("اااه", True), ("افف", True), ("أوووف", True), ("يوووه", True),
    ("um", True), ("uhh", True),
    ("آه", False),       # "yes"
    ("ام", False),       # "mother"
    ("اف", False),
])
def test_fillers(word, is_filler):
    words = make_words([(word, 1.0, 1.4)])
    assert bool(D.detect_fillers(words)) is is_filler


def test_retake_keeps_last_take():
    words = make_words([
        ("أنا النهاردة هتكلم", 0.5, 1.8),
        ("انا النهارده هتكلم عن الدايت.", 3.0, 5.0),
    ])
    phrases = D.build_phrases(words)
    cuts = D.detect_retakes(words, phrases)
    assert len(cuts) == 1
    assert cuts[0]["s"] < 0.5 < cuts[0]["e"] <= 3.0


def test_restart_mid_sentence_cuts_only_the_tail():
    words = make_words([
        ("أول حاجة لازم تشرب مية", 6.5, 8.4),
        ("لازم تشرب مية كتير جدا.", 9.2, 11.2),
    ])
    cuts = D.detect_retakes(words, D.build_phrases(words))
    assert len(cuts) == 1
    assert 6.9 < cuts[0]["s"] < 7.5          # starts at "لازم", keeps "أول حاجة"


def test_stutter():
    words = make_words([("التمرين مهم التمرين مهم جدا", 12.0, 14.2)])
    cuts = D.detect_stutters(words)
    assert len(cuts) == 1 and cuts[0]["reason"] == "stutter"


def test_stutter_ignores_repeats_across_long_pause():
    words = make_words([("هتكلم عن", 0.0, 1.0), ("هتكلم عن", 5.0, 6.0)])
    assert D.detect_stutters(words) == []


def test_detect_meta_cuts_strong_and_defers_weak():
    words = make_words([
        ("النهارده هتكلم عن الدايت", 0.0, 2.0),
        ("افف انا نسيت الكلام", 3.0, 4.2),
        ("استنى ثانية واحدة", 5.0, 6.0),
    ])
    cuts, weak = D.detect_meta(words, D.build_phrases(words))
    assert [c["reason"] for c in cuts] == ["mistake"]
    assert 2.9 < cuts[0]["s"] < 3.0
    assert len(weak) == 1 and weak[0][0] == 2


def test_gaps_and_noise_from_audio():
    sr = 16000
    rng = np.random.default_rng(0)
    audio = rng.normal(0, 0.001, sr * 6).astype(np.float32)
    audio[sr * 1:sr * 2] += rng.normal(0, 0.2, sr).astype(np.float32)      # speech 1-2 s
    audio[sr * 4:sr * 5] += rng.normal(0, 0.2, sr).astype(np.float32)      # speech 4-5 s
    db = D.energy_db(audio, sr)
    act = D.activity_mask(db, D.speech_threshold(db))
    gaps = D.find_gaps(act, 0.0, min_gap=0.5, pad=0.1)
    inner = [g for g in gaps if 2.0 <= g["s"] and g["e"] <= 4.0]
    assert len(inner) == 1 and inner[0]["e"] - inner[0]["s"] > 1.5

    words = make_words([("كلام", 1.0, 2.0)])                              # nothing said at 4-5 s
    noise = D.detect_noise(act, 0.0, words)
    assert len(noise) == 1 and 3.9 < noise[0]["s"] < 4.1
