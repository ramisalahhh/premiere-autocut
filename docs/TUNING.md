# Tuning guide

Re-runs on the same footage are instant (transcripts are cached), so experiment freely.

| Symptom | Setting | Try |
|---|---|---|
| Cuts feel choppy / breaths cut off | Breathing room kept at cuts | 0.15–0.20 s |
| Natural dramatic pauses removed | Minimum gap to cut | 0.7–1.0 s |
| Silence not detected (noisy room, AC, fan) | Silence threshold above noise floor | 6–8 dB |
| Breathing/room tone treated as speech | Silence threshold | 12–14 dB |
| Retakes missed | Retake similarity | ~72 |
| Different lines treated as retakes | Retake similarity | ~88 |
| Reading moments missed | Gaze sensitivity | ~3.25 |
| Normal head movement flagged | Gaze sensitivity | 5+ |
| You improvise a lot | Parts not in the script | *Mark for review* |
| You follow the script closely | Parts not in the script | *Cut short ones* |

## Script mode tips
- Paste the script **as you wrote it**. Exact punctuation or spelling isn't needed, because everything is normalized (أ/إ/ا, ة/ه, ى/ي, diacritics).
- Changing words, skipping sentences, or adding a sentence is fine. Long paraphrases are never auto-cut.
- If you repeat the same line *on purpose* (e.g. a hook at the start and the end), expect a CHECK marker on one of them.

## Reading the transcript view
- **Green**: kept · **Red, struck through**: cut (the reason is on the right) · **Orange**: needs a human decision.
- `script 63%` = how well that phrase matched your script.
- Click a line to jump the playhead to it on the original sequence.

## If speech is missing from the transcript
Whisper's voice-activity filter can drop very quiet speech. If a section you spoke in shows no lines:
1. check the clip's audio level (normalize it to about −3 dB peak),
2. or set **Language** to Arabic explicitly instead of auto,
3. and open an issue with the timestamp.
