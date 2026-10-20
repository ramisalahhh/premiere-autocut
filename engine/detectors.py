"""Audio/text detectors: gaps, retakes, stutters, fillers, non-speech noise.

All times are in SOURCE seconds (relative to the start of the media file).
A "word" is a dict: {"s": start, "e": end, "t": raw text, "n": normalized text, "p": probability}.
A "cut"/"review" item is a dict: {"s", "e", "reason", "detail"}.
"""
import re

import numpy as np

try:
    from rapidfuzz import fuzz

    def ratio(a, b):
        return float(fuzz.ratio(a, b))
except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher

    def ratio(a, b):
        return 100.0 * SequenceMatcher(None, a, b).ratio()

FRAME = 0.02  # energy frame length in seconds

# ---------------------------------------------------------------- text utils
_DIAC = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_ALEF = re.compile("[\u0625\u0623\u0622\u0671\u0627]")  # إ أ آ ٱ ا -> ا


def normalize(text):
    """Normalize Arabic/English text so retakes compare well."""
    t = _DIAC.sub("", text or "")
    t = _ALEF.sub("\u0627", t)
    t = (t.replace("\u0649", "\u064A")   # ى -> ي
          .replace("\u0629", "\u0647")   # ة -> ه
          .replace("\u0624", "\u0648")   # ؤ -> و
          .replace("\u0626", "\u064A"))  # ئ -> ي
    t = _PUNCT.sub(" ", t).replace("_", " ")
    return " ".join(t.lower().split())


def item(s, e, reason, detail=""):
    return {"s": float(s), "e": float(e), "reason": reason, "detail": detail}


def _txt(words, idx):
    return " ".join(words[i]["n"] for i in idx)


def _short(words, idx, n=6):
    raw = " ".join(words[i]["t"].strip() for i in idx[:n])
    return raw + (" …" if len(idx) > n else "")


# -------------------------------------------------------------- energy utils
def energy_db(audio, sr=16000):
    hop = int(sr * FRAME)
    n = len(audio) // hop
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    x = audio[: n * hop].reshape(n, hop).astype(np.float32)
    rms = np.sqrt(np.mean(x * x, axis=1)) + 1e-9
    return 20.0 * np.log10(rms)


def speech_threshold(db, margin_db=10.0):
    """Adaptive threshold: noise floor + margin, but never above speech level."""
    if len(db) == 0:
        return -60.0
    floor = float(np.percentile(db, 10))
    loud = float(np.percentile(db, 95))
    return min(floor + margin_db, loud - 12.0)


def runs(mask):
    """Return [(start_idx, end_idx_exclusive)] for True runs."""
    m = np.concatenate([[False], np.asarray(mask, dtype=bool), [False]])
    d = np.diff(m.astype(np.int8))
    return list(zip(np.where(d == 1)[0].tolist(), np.where(d == -1)[0].tolist(), strict=False))


def activity_mask(db, thr, fill=0.12, min_blip=0.06):
    act = db > thr
    for s, e in runs(~act):  # close tiny holes inside speech
        if (e - s) * FRAME < fill and s > 0 and e < len(act):
            act[s:e] = True
    for s, e in runs(act):  # drop clicks
        if (e - s) * FRAME < min_blip:
            act[s:e] = False
    return act


def active_fraction(act, t0, s, e):
    i = max(0, int((s - t0) / FRAME))
    j = min(len(act), max(i + 1, int(np.ceil((e - t0) / FRAME))))
    return float(act[i:j].mean()) if j > i else 0.0


# ------------------------------------------------------------------- gaps
def find_gaps(act, t0, min_gap=0.5, pad=0.12):
    out = []
    for s, e in runs(~act):
        a, b = t0 + s * FRAME, t0 + e * FRAME
        if b - a >= min_gap:
            a2, b2 = a + pad, b - pad
            if b2 - a2 > 0.04:
                out.append(item(a2, b2, "gap", "silence %.2fs" % (b - a)))
    return out


# ------------------------------------------------------------------ noise
def detect_noise(act, t0, words, min_dur=0.25, tol=0.1):
    """Sound with no recognized words (cough, click, bump, background talk)."""
    out = []
    ws = np.array([w["s"] for w in words]) if words else np.zeros(0)
    we = np.array([w["e"] for w in words]) if words else np.zeros(0)
    for s, e in runs(act):
        a, b = t0 + s * FRAME, t0 + e * FRAME
        if b - a < min_dur:
            continue
        if len(ws) and np.any((we > a - tol) & (ws < b + tol)):
            continue
        out.append(item(a, b, "noise", "sound without speech %.2fs" % (b - a)))
    return out


# ---------------------------------------------------------------- phrases
SENT_END = ".!?\u061F\u2026"  # . ! ? ؟ …


def build_phrases(words, gap=0.30):
    """Split the word stream into phrases at pauses / sentence ends."""
    phrases, cur = [], []
    for i, w in enumerate(words):
        if cur and w["s"] - words[cur[-1]]["e"] > gap:
            phrases.append(cur)
            cur = []
        cur.append(i)
        if w["t"].strip()[-1:] in SENT_END:
            phrases.append(cur)
            cur = []
    if cur:
        phrases.append(cur)
    return phrases


# ---------------------------------------------------------------- retakes
def detect_retakes(words, phrases, thr=80.0, lookahead=6, max_span=25.0,
                   pre=0.04, post=0.06):
    """Keep-the-last-take logic.

    1) Phrase P is re-said at the start of a later phrase Q  -> cut P..Q.
    2) The END of P is re-said at the start of the next phrase (restart
       from the middle of a sentence)                       -> cut that tail.
    """
    cuts = []
    for pi, P in enumerate(phrases):
        n = len(P)
        tp = _txt(words, P)
        ps, pe = words[P[0]]["s"], words[P[-1]]["e"]
        hit = False
        for pj in range(pi + 1, min(len(phrases), pi + 1 + lookahead)):
            Q = phrases[pj]
            qs = words[Q[0]]["s"]
            if qs - pe > max_span:
                break
            if min(len(Q), n) < max(1, int(np.ceil(n * 0.6))):
                continue  # Q is a short false start, look further
            tq = _txt(words, Q[:n])
            single_ok = (n == 1 and len(tp) >= 3 and tp == words[Q[0]]["n"]
                         and qs - pe < 3.0)
            if (n >= 2 or single_ok) and ratio(tp, tq) >= thr:
                cuts.append(item(ps - pre, qs - post, "retake",
                                 "re-said: \u201C%s\u201D" % _short(words, P)))
                hit = True
                break
        if hit or pi + 1 >= len(phrases):
            continue
        Q = phrases[pi + 1]
        qs = words[Q[0]]["s"]
        if qs - pe > max_span:
            continue
        for k in range(min(n - 1, len(Q), 8), 1, -1):
            if ratio(_txt(words, P[-k:]), _txt(words, Q[:k])) >= thr:
                cuts.append(item(words[P[-k]]["s"] - pre, qs - post, "retake",
                                 "restarted mid-sentence: \u201C%s\u201D"
                                 % _short(words, P[-k:])))
                break
    return cuts


def detect_stutters(words, max_k=6, max_gap=1.0, thr=90.0, pre=0.03, post=0.03):
    """Immediate repeats of 2+ words with almost no pause ("انا النهارده انا النهارده")."""
    cuts, i, N = [], 0, len(words)
    while i < N:
        found = False
        for k in range(min(max_k, (N - i) // 2), 1, -1):
            # the whole repeated block must be continuous speech
            if any(words[x + 1]["s"] - words[x]["e"] > max_gap
                   for x in range(i, i + 2 * k - 1)):
                continue
            a = _txt(words, range(i, i + k))
            b = _txt(words, range(i + k, i + 2 * k))
            if a == b or ratio(a, b) >= thr:
                cuts.append(item(words[i]["s"] - pre, words[i + k]["s"] - post,
                                 "stutter", "repeated: \u201C%s\u201D"
                                 % _short(words, list(range(i, i + k)))))
                i += k
                found = True
                break
        if not found:
            i += 1
    return cuts


# ---------------------------------------------------------------- fillers
# "اااه", "امم", "ممم", "uh", "um", "hmm" ... (not "اه" = yes, not "ام")
_FILLER = re.compile(
    "^(\u0627{2,}[\u0647\u0645]*|\u0627\u0645{2,}|\u0645{2,}|\u0647\u0645{2,}"
    "|\u0627+\u0641{2,}|\u0627+\u0648+\u0641+|\u064A\u0648+\u0647"
    "|u+[mh]+|e+h+|h+m+|a+h+|e+r+m*|u+g+h+)$")


def detect_fillers(words, pad=0.02):
    cuts = []
    for i, w in enumerate(words):
        if _FILLER.match(w["n"].replace(" ", "")):
            s = max(w["s"] - pad, words[i - 1]["e"] if i else w["s"] - pad)
            e = min(w["e"] + pad, words[i + 1]["s"] if i + 1 < len(words) else w["e"] + pad)
            cuts.append(item(s, e, "filler", "\u201C%s\u201D" % w["t"].strip()))
    return cuts


# ------------------------------------------------- talking about the mistake
# Patterns run on NORMALIZED text (أ/إ/آ->ا, ة->ه, ى->ي, no diacritics).
# STRONG = you only say this when something went wrong -> cut.
# WEAK   = could be real content ("الطريقه الغلط") -> marker, or cut when the
#          phrase is also off-script in script mode.
_STRONG = [
    "ا+ف{2,}", "ا+و+ف+", "يو+ه",                       # افف / اوف / يوه
    "انا [نف]?نسيت", "[نف]?نسيت (الكلام|هقول|كنت|ايه|انا)", "[نف]?نسيت$",
    "هعيد", "نعيد", "اعيدها", "خليني اعيد", "اعيد تاني",
    "نبدا من الاول", "من الاول تاني",
    "اتلخبطت", "لخبطت", "اتلغبطت", "اتلخبط",
    "ستوب", "stop", "cut", "again",
    "مش عارف اقول", "يلا تاني", "ماشي تاني",
]
_WEAK = [
    "استني", "ثانيه واحده", "ثواني$", "معلش",
    "غلط", "مش كده", "لا لا", "ايه ده", "[نف]?نسيت",
    "الله يخربيت", "تاني كده",
]


def _compile(pats):
    # word boundaries that also work for Arabic letters
    return [re.compile(r"(?:^|(?<=\s))(?:" + p + r")(?=\s|$)") for p in pats]


_STRONG_RE = _compile(_STRONG)
_WEAK_RE = _compile(_WEAK)


def meta_cue(text):
    """('strong'|'weak', matched text) or (None, None) for NORMALIZED text."""
    for r in _STRONG_RE:
        m = r.search(text)
        if m:
            return "strong", m.group(0)
    for r in _WEAK_RE:
        m = r.search(text)
        if m:
            return "weak", m.group(0)
    return None, None


def _cue_word_index(words, P, cue):
    first = cue.split()[0]
    for k, i in enumerate(P):
        if words[i]["n"] == first:
            return k
    return 0


def detect_meta(words, phrases, pre=0.05, post=0.05, head_words=6):
    """Phrases where you talk about the mistake ("افف انا نسيت الكلام").

    Returns (cuts, weak); weak = [(phrase_index, item)] for the caller to decide
    (review marker, or cut when the phrase is also off-script).
    """
    cuts, weak = [], []
    for pi, P in enumerate(phrases):
        kind, cue = meta_cue(_txt(words, P))
        if not kind:
            continue
        s, e = words[P[0]]["s"], words[P[-1]]["e"]
        nxt = words[phrases[pi + 1][0]]["s"] if pi + 1 < len(phrases) else e + 0.6
        end = min(nxt - post, e + 0.6)
        detail = "“%s”" % _short(words, P, 8)
        if kind == "strong":
            k = _cue_word_index(words, P, cue)
            start = s if k < head_words else words[P[k]]["s"]
            cuts.append(item(start - pre, end, "mistake", "said: " + detail))
        else:
            weak.append((pi, item(s - pre, e + post, "mistake?", "maybe a mistake: " + detail)))
    return cuts, weak
