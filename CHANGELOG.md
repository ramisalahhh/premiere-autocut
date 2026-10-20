# Changelog

## 1.2.0
- **Fix:** script mode no longer deletes long paraphrased content. The off-script score is now `max(sequence match, word coverage)`, and only short phrases (≤ 8 words) can be auto-cut.
- **Fix:** "من الاول" removed from the mistake cues (common in normal speech).
- Whisper misspellings like "ننسيت" are matched as "نسيت".
- "Cut them" option renamed to "Cut short ones (long parts are only marked)".
- Added pytest suite (45 tests), ruff, and GitHub Actions CI on Python 3.10/3.12/3.14.

## 1.1.0
- **Script-guided mode:** paste your script; keeps the last take of every script line, even with changed words.
- **Mistake talk detection:** "افف انا نسيت الكلام", "خليني أعيد", "اتلخبطت", "ستوب"…
- New fillers: افف، اوف، يوه.
- **Transcript view** in the panel: every phrase colored kept / cut / check; click to jump.

## 1.0.1
- Python 3.13/3.14 support: MediaPipe Tasks `FaceLandmarker` (with legacy FaceMesh fallback), unpinned numpy.
- Audio decoding via PyAV directly (works with PyAV ≥ 15).

## 1.0.0
- First release: gaps, head/tail trim, retakes, stutters, fillers, noise, gaze + flat-voice reading detection; non-destructive subclip sequence; preview markers; caching.
