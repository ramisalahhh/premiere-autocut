"""Turn detections into a cut plan: which source ranges to keep, plus markers."""
import detectors as D
import prosody
import script_align as SA
import vision

# Premiere marker color indexes
COLORS = {"gap": 5, "head": 5, "tail": 5, "retake": 3, "stutter": 3,
          "filler": 2, "reading": 1, "noise": 7, "look-away": 4,
          "flat-voice": 4, "review": 4, "mistake": 3, "mistake?": 4,
          "off-script": 4}

LABELS = {"gap": "Gap", "head": "Start trim", "tail": "End trim",
          "retake": "Retake", "stutter": "Repeat", "filler": "Filler",
          "reading": "Reading script", "noise": "Noise / no speech",
          "look-away": "Looking away", "flat-voice": "Flat voice",
          "mistake": "Mistake talk", "mistake?": "Maybe mistake", "off-script": "Off-script"}


PRIORITY = ["mistake", "reading", "filler", "stutter", "retake", "off-script",
            "look-away", "noise", "head", "tail", "gap"]


def merge(items, join=0.05):
    items = sorted((a, b) for a, b in items if b > a)
    out = []
    for a, b in items:
        if out and a <= out[-1][1] + join:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def subtract(lo, hi, cuts):
    keep, cur = [], lo
    for a, b in cuts:
        a, b = max(a, lo), min(b, hi)
        if b <= a:
            continue
        if a > cur:
            keep.append((cur, a))
        cur = max(cur, b)
    if cur < hi:
        keep.append((cur, hi))
    return keep


def _merge_items(items, join=0.6):
    """Merge neighbouring review items with the same reason."""
    items = sorted(items, key=lambda x: (x["reason"], x["s"]))
    out = []
    for it in items:
        if out and out[-1]["reason"] == it["reason"] and it["s"] <= out[-1]["e"] + join:
            out[-1]["e"] = max(out[-1]["e"], it["e"])
        else:
            out.append(dict(it))
    return sorted(out, key=lambda x: x["s"])


def plan_clip(clip, A, S, script=None):
    """A = media analysis dict (see analyze.py).
    Returns (keep, cuts, reviews, phrase_info)."""
    lo, hi = float(clip["in"]), float(clip["out"])
    words = [w for w in A["words"] if w["e"] > lo and w["s"] < hi]
    cuts, reviews = [], []

    # --- gaps & head/tail trim
    if S["cut_gaps"]:
        cuts += [c for c in A["gaps"] if c["e"] > lo and c["s"] < hi]
    if S["trim_ends"] and words:
        head_end = words[0]["s"] - S["edge_pad"]
        tail_start = words[-1]["e"] + S["edge_pad"]
        if head_end - lo > 0.1:
            cuts.append(D.item(lo, head_end, "head", "before first word"))
        if hi - tail_start > 0.1:
            cuts.append(D.item(tail_start, hi, "tail", "after last word"))

    phrases = D.build_phrases(words, gap=S["phrase_gap"])

    # --- retakes / repeats / fillers
    if S["cut_retakes"]:
        cuts += D.detect_retakes(words, phrases, thr=S["retake_similarity"],
                                 lookahead=S["retake_lookahead"],
                                 max_span=S["retake_max_span"])
    if S["cut_stutters"]:
        cuts += D.detect_stutters(words)
    if S["cut_fillers"]:
        cuts += D.detect_fillers(words)

    # --- talking about the mistake ("افف انا نسيت الكلام")
    weak = []
    if S["cut_mistakes"]:
        mc, weak = D.detect_meta(words, phrases)
        cuts += mc

    # --- script-guided: last take per script part + off-script parts
    al = [None] * len(phrases)
    off = set()
    if script:
        al = SA.align(words, phrases, script)
        if S["cut_retakes"]:
            cuts += SA.script_cuts(words, phrases, al, match=S["script_match"])
        offl = SA.off_script(words, phrases, al, script, threshold=S["script_offscript"])
        off = {pi for pi, _ in offl}
        weak_idx = {pi for pi, _ in weak}
        for pi, score in offl:
            P = phrases[pi]
            s0, e0 = words[P[0]]["s"], words[P[-1]]["e"]
            it = D.item(s0 - 0.04, e0 + 0.04, "off-script",
                        "not in script (%d%%): \u201C%s\u201D" % (score, D._short(words, P, 8)))
            short = len(P) <= S["offscript_max_words"]
            if short and pi in weak_idx:
                it["reason"] = "mistake"          # "استنى ثانية" etc. that isn't in the script
                cuts.append(it)
            elif short and S["off_script_mode"] == "cut" and score < S["script_offscript_cut"]:
                cuts.append(it)
            elif S["off_script_mode"] != "off":
                reviews.append(it)               # long or borderline: never auto-cut
    for pi, it in weak:
        if pi not in off or not script:
            reviews.append(it)

    # --- noise without speech
    if S["noise_mode"] != "off":
        noise = [n for n in A["noise"] if n["e"] > lo and n["s"] < hi]
        (cuts if S["noise_mode"] == "cut" else reviews).extend(noise)

    # --- reading the script (gaze + flat voice)
    mode = S["reading_mode"]
    if mode != "off" and phrases:
        gaze = A.get("gaze")  # (times, away) or None
        flat = [False] * len(phrases)
        if A.get("audio") is not None:
            feats = prosody.phrase_features(A["audio"], A["sr"], A["t0"], words, phrases)
            flat = prosody.flat_flags(feats, pitch_z=S["flat_pitch_z"], rate_z=S["flat_rate_z"])
        for pi, P in enumerate(phrases):
            s, e = words[P[0]]["s"], words[P[-1]]["e"]
            looked_away = bool(gaze) and vision.away_fraction(gaze[0], gaze[1], s, e) >= S["gaze_fraction"]
            is_flat = pi < len(flat) and flat[pi]
            txt = D._short(words, P)
            if looked_away and is_flat:
                it = D.item(s - 0.05, e + 0.05, "reading", "looking away + flat voice: “%s”" % txt)
                (cuts if mode in ("auto", "cut") else reviews).append(it)
            elif looked_away:
                it = D.item(s - 0.05, e + 0.05, "look-away", "looking away: “%s”" % txt)
                (cuts if mode == "cut" else reviews).append(it)
            elif is_flat:
                reviews.append(D.item(s, e, "flat-voice", "flat / read-sounding: “%s”" % txt))

    # --- build keep list
    merged = merge([(max(lo, c["s"]), min(hi, c["e"])) for c in cuts], join=S["merge_join"])
    keep = subtract(lo, hi, merged)
    keep = [(a, b) for a, b in keep if b - a >= S["min_keep"]]
    cuts = [c for c in cuts if min(hi, c["e"]) > max(lo, c["s"])]

    # --- per-phrase info for the transcript view
    info = []
    for pi, P in enumerate(phrases):
        s0, e0 = words[P[0]]["s"], words[P[-1]]["e"]
        span = max(1e-6, e0 - s0)
        covered = sum(max(0.0, min(b, e0) - max(a, s0)) for a, b in merged)
        reason = ""
        if covered / span > 0.5:
            best = None
            for c in cuts:
                ov = min(c["e"], e0) - max(c["s"], s0)
                if ov < 0.3 * span:
                    continue
                key = (PRIORITY.index(c["reason"]) if c["reason"] in PRIORITY else 99, -ov)
                if best is None or key < best:
                    best, reason = key, c["reason"]
            reason = reason or "gap"
        flag = ""
        for r in reviews:
            if min(r["e"], e0) - max(r["s"], s0) > 0.3 * span:
                flag = r["reason"]
                break
        a = al[pi] if pi < len(al) else None
        info.append({"s": s0, "e": e0,
                     "text": " ".join(words[i]["t"].strip() for i in P),
                     "cut": reason, "check": flag,
                     "match": round(a["score"]) if a else None})
    return keep, cuts, _merge_items(reviews), info


def build_plan(clips, analyses, S):
    """clips sorted by timeline start. Returns the result dict for the panel."""
    pieces, new_markers, preview = [], [], []
    counts, removed_by = {}, {}
    total_orig = total_kept = 0.0
    t_new = 0.0
    skipped = []
    transcript = []
    script = SA.Script(S.get("script") or "") if (S.get("script") or "").strip() else None
    if script is not None and not script:
        script = None

    for clip in clips:
        A = analyses.get(clip["path"])
        if A is None:
            skipped.append({"name": clip.get("name"), "why": "analysis failed"})
            continue
        keep, cuts, reviews, info = plan_clip(clip, A, S, script)
        lo, hi = float(clip["in"]), float(clip["out"])
        total_orig += hi - lo

        def tl(src):  # source time -> ORIGINAL timeline time
            return float(clip["start"]) + (src - lo)

        for ph in info:
            s0, e0 = max(lo, ph.pop("s")), min(hi, ph.pop("e"))
            ph["t"], ph["d"] = round(tl(s0), 3), round(max(0.0, e0 - s0), 3)
            transcript.append(ph)

        for c in cuts:
            counts[c["reason"]] = counts.get(c["reason"], 0) + 1
            removed_by[c["reason"]] = removed_by.get(c["reason"], 0.0) + (min(hi, c["e"]) - max(lo, c["s"]))
            preview.append(_marker(tl(max(lo, c["s"])), min(hi, c["e"]) - max(lo, c["s"]), c, cut=True))
        for r in reviews:
            preview.append(_marker(tl(max(lo, r["s"])), min(hi, r["e"]) - max(lo, r["s"]), r, cut=False))

        clip_pieces = []
        for a, b in keep:
            clip_pieces.append({"clipIndex": clip["index"], "nodeId": clip.get("nodeId"),
                                "s": round(a, 4), "e": round(b, 4), "new_start": round(t_new, 4)})
            t_new += b - a
            total_kept += b - a
        pieces += clip_pieces

        for r in reviews:  # place review markers on the NEW sequence
            for p in clip_pieces:
                a, b = max(r["s"], p["s"]), min(r["e"], p["e"])
                if b > a:
                    new_markers.append(_marker(p["new_start"] + (a - p["s"]), b - a, r, cut=False))
                    break

    return {
        "pieces": pieces,
        "new_markers": sorted(new_markers, key=lambda m: m["t"]),
        "preview_markers": sorted(preview, key=lambda m: m["t"]),
        "skipped": skipped,
        "transcript": transcript,
        "script_used": bool(script),
        "summary": {
            "original": round(total_orig, 2),
            "kept": round(total_kept, 2),
            "removed": round(total_orig - total_kept, 2),
            "pieces": len(pieces),
            "counts": counts,
            "removed_by_reason": {k: round(v, 2) for k, v in removed_by.items()},
            "reviews": len(new_markers),
        },
    }


def _marker(t, d, it, cut):
    label = LABELS.get(it["reason"], it["reason"])
    return {
        "t": round(max(0.0, t), 4),
        "d": round(max(0.0, d), 4),
        "name": "AC: %s%s" % ("CUT " if cut else "CHECK ", label),
        "comment": it.get("detail", ""),
        "color": COLORS.get(it["reason"], 4),
    }
