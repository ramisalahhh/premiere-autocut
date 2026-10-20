"""Cut planning: keep-ranges, timeline mapping, markers."""
import plan as P
from conftest import analysis, clip, make_words


def test_merge_and_subtract():
    assert P.merge([(1, 2), (1.9, 3), (5, 6)], join=0.05) == [(1, 3), (5, 6)]
    assert P.subtract(0, 10, [(1, 3), (5, 6)]) == [(0, 1), (3, 5), (6, 10)]


def test_pieces_are_contiguous_on_new_timeline(settings):
    words = make_words([("أنا النهاردة هتكلم", 0.5, 1.8),
                        ("انا النهارده هتكلم عن الدايت.", 3.0, 5.0),
                        ("امم", 5.6, 5.9),
                        ("وأول حاجة لازم تشرب مية", 6.5, 8.4)])
    r = P.build_plan([clip(10.0, start=20.0)], {"p": analysis(words)}, settings)
    t = 0.0
    for piece in r["pieces"]:
        assert abs(piece["new_start"] - t) < 1e-3
        assert piece["e"] > piece["s"]
        t += piece["e"] - piece["s"]
    assert abs(t - r["summary"]["kept"]) < 0.01
    # preview markers live on the ORIGINAL timeline (clip starts at 20 s)
    assert all(m["t"] >= 20.0 for m in r["preview_markers"])
    assert r["summary"]["counts"].get("retake") == 1
    assert r["summary"]["counts"].get("filler") == 1


def test_trim_ends(settings):
    words = make_words([("كلام مهم جدا", 3.0, 5.0)])
    settings.update(cut_gaps=False)
    r = P.build_plan([clip(10.0)], {"p": analysis(words)}, settings)
    (piece,) = r["pieces"]
    assert 2.7 < piece["s"] < 3.0 and 5.0 < piece["e"] < 5.3


def test_missing_media_is_reported(settings):
    r = P.build_plan([clip(5.0, path="missing.mp4")], {}, settings)
    assert r["pieces"] == [] and r["skipped"]
