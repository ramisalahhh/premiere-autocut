"""Script-guided analysis.

You give AutoCut the script you were reading/following. Every spoken phrase is
located in the script with fuzzy matching (so changed / skipped / extra words
still match). Then:

* If you say a part of the script again (going BACK in the script), the earlier
  attempt is a retake -> cut, the last take is kept.
* If a phrase restarts in the middle of the previous one, the overlapping tail
  of the earlier phrase is cut.
* Phrases that don't match the script at all are "off-script": marked for
  review (you improvise sometimes), or cut when they also contain a mistake cue.
"""

import detectors as D

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover
    fuzz = None


class Script:
    def __init__(self, text):
        # one string of normalized words + char -> word index map
        self.words = D.normalize(text).split()
        self.starts = []
        pos = 0
        for w in self.words:
            self.starts.append(pos)
            pos += len(w) + 1
        self.text = " ".join(self.words)
        self.vocab = set(self.words)

    def __bool__(self):
        return len(self.words) >= 3

    def word_at(self, char_pos):
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= char_pos:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def locate(self, phrase_text, from_word=0):
        """Best fuzzy location of phrase_text in the script starting at from_word.
        Returns (score 0..100, start_word, end_word_exclusive) or None."""
        if fuzz is None or not phrase_text:
            return None
        base = self.starts[from_word] if from_word < len(self.starts) else len(self.text)
        hay = self.text[base:]
        if len(hay) < 3:
            return None
        if len(phrase_text) > len(hay):
            return (fuzz.ratio(phrase_text, hay), from_word, len(self.words))
        r = fuzz.partial_ratio_alignment(phrase_text, hay)
        if r is None:
            return None
        sw = self.word_at(base + r.dest_start)
        ew = self.word_at(base + max(r.dest_start, r.dest_end - 1)) + 1
        return (float(r.score), sw, ew)


def align(words, phrases, script, min_words=3, back_window=150):
    """Locate each phrase in the script. Returns list of None or dict
    {"score", "sw", "ew"}. Search starts a bit behind the last confident hit so
    repeated lines in the script don't confuse it, and so retakes (going back)
    are still found."""
    out = []
    cursor = 0
    for P in phrases:
        txt = D._txt(words, P)
        if len(P) < min_words and len(txt) < 12:
            out.append(None)
            continue
        loc = script.locate(txt, max(0, cursor - back_window))
        if loc is None:
            out.append(None)
            continue
        score, sw, ew = loc
        out.append({"score": score, "sw": sw, "ew": ew})
        if score >= 80:
            cursor = sw
    return out


def script_cuts(words, phrases, al, match=75, pre=0.04, post=0.06):
    """Keep-the-last-take using script positions."""
    cuts = []
    stack = []  # phrase indexes still kept, in script order
    for qi, a in enumerate(al):
        if not a or a["score"] < match:
            continue
        Q = phrases[qi]
        qs = words[Q[0]]["s"]
        # Going back in the script => earlier phrases covering this part are retakes
        while stack and al[stack[-1]]["sw"] >= a["sw"] - 1:
            pi = stack.pop()
            P = phrases[pi]
            nxt = words[phrases[pi + 1][0]]["s"]
            cuts.append(D.item(words[P[0]]["s"] - pre, min(nxt, qs) - post, "retake",
                               "script retake: “%s”" % D._short(words, P)))
        # Restarted from the middle of the previous phrase => cut its tail
        if stack:
            pi = stack[-1]
            b, P = al[pi], phrases[pi]
            if b["ew"] > a["sw"] + 1 and b["ew"] > b["sw"]:
                frac = (a["sw"] - b["sw"]) / float(b["ew"] - b["sw"])
                k = max(1, min(len(P) - 1, int(round(frac * len(P)))))
                if len(P) - k >= 2:
                    cuts.append(D.item(words[P[k]]["s"] - pre, qs - post, "retake",
                                       "script restart: “%s”" % D._short(words, P[k:])))
        stack.append(qi)
    return cuts


def coverage(script, words, P):
    """% of the phrase's words (2+ letters) that appear in the script, fuzzily.
    Works even when you paraphrase a whole paragraph in your own words."""
    ws = [words[i]["n"] for i in P if len(words[i]["n"]) >= 2]
    if not ws:
        return 0.0
    hit = 0
    for w in ws:
        if w in script.vocab:
            hit += 1
        elif fuzz is not None:
            from rapidfuzz import process
            m = process.extractOne(w, script.vocab, scorer=fuzz.ratio, score_cutoff=75)
            if m:
                hit += 1
    return 100.0 * hit / len(ws)


def off_script(words, phrases, al, script, threshold=50, min_words=2):
    """[(phrase_index, score)] for phrases that clearly aren't from the script.
    score = max(sequence match, word coverage)."""
    res = []
    for pi, P in enumerate(phrases):
        if len(P) < min_words:
            continue
        a = al[pi]
        seq = a["score"] if a else 0.0
        if seq >= threshold:
            continue
        sc = max(seq, coverage(script, words, P))
        if sc < threshold:
            res.append((pi, sc))
    return res
