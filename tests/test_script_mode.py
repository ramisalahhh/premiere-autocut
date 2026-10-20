"""Script-guided mode: last take per script line, paraphrases are kept."""
import plan as P
from conftest import analysis, clip, decisions, make_words

SCRIPT = """النهارده هتكلم معاكم عن أهم غلطة بيعملها الناس في الدايت.
أول حاجة لازم تشرب مية كتير جدا طول اليوم.
تاني حاجة البروتين مهم في كل وجبة عشان العضلات.
وآخر حاجة النوم، لازم تنام سبع ساعات على الأقل."""

SPOKEN = [
    ("النهارده هتكلم معاكم عن أهم", 0.5, 2.5),                                   # false start
    ("افف انا نسيت الكلام", 3.3, 4.6),                                           # mistake talk
    ("النهارده هتكلم معاكم عن أكبر غلطة بيعملها الناس في الدايت", 5.5, 9.5),     # take 2, word changed
    ("أول حاجة لازم تشرب مية كتير", 10.3, 12.5),                                 # restarted below
    ("تشرب مية كتير جدا طول اليوم", 13.3, 15.5),
    ("تاني حاجة البروتين مهم في كل وجبة عشان العضلات", 19.3, 22.5),              # take 1
    ("استنى ثانية واحدة", 23.3, 24.3),                                           # weak cue, off-script
    ("تاني حاجة البروتين مهم جدا في كل وجبة عشان العضلات", 25.3, 28.8),          # take 2
    ("وآخر حاجة النوم لازم تنام سبع ساعات على الأقل", 29.6, 32.8),
]

# A long paraphrase: same meaning, mostly different wording. Must never be cut.
PARAPHRASE = ("ومش بس كده النوم ده مهم جدا يعني لازم تاخد سبع ساعات على الاقل "
              "عشان جسمك يرتاح والعضلات تبني نفسها وانت نايم بالليل")


def run(settings, spoken, script=SCRIPT, **over):
    settings.update(script=script, cut_gaps=False, trim_ends=False, **over)
    words = make_words(spoken)
    return P.build_plan([clip(spoken[-1][2] + 1)], {"p": analysis(words)}, settings)


def test_last_take_kept(settings):
    d = decisions(run(settings, SPOKEN))
    assert d["النهارده هتكلم معاكم عن أهم"] != "keep"
    assert d["النهارده هتكلم معاكم عن أكبر غلطة بيعملها الناس في الدايت"] == "keep"
    assert d["تاني حاجة البروتين مهم في كل وجبة عشان العضلات"] != "keep"
    assert d["تاني حاجة البروتين مهم جدا في كل وجبة عشان العضلات"] == "keep"
    assert d["تشرب مية كتير جدا طول اليوم"] == "keep"
    assert d["وآخر حاجة النوم لازم تنام سبع ساعات على الأقل"] == "keep"


def test_mistakes_cut(settings):
    d = decisions(run(settings, SPOKEN))
    assert d["افف انا نسيت الكلام"] == "mistake"
    assert d["استنى ثانية واحدة"] != "keep"


def test_long_paraphrase_never_cut_even_in_cut_mode(settings):
    spoken = SPOKEN + [(PARAPHRASE, 34.0, 44.0)]
    for mode in ("mark", "cut"):
        d = decisions(run(dict(settings), spoken, off_script_mode=mode))
        assert d[PARAPHRASE] == "keep", mode


def test_same_result_shape_without_script(settings):
    r = run(settings, SPOKEN, script="")
    assert r["script_used"] is False
    assert decisions(r)["النهارده هتكلم معاكم عن أكبر غلطة بيعملها الناس في الدايت"] == "keep"
