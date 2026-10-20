# AGENTS.md: guide for AI coding agents

This file is for AI assistants (Claude Code, Codex, Cursor, Copilot, Aider, …) working on this repo. Humans: see [README.md](README.md).

## What this project is
A **Premiere Pro 2022+ CEP panel** plus a **local Python engine** that auto-cuts talking-head footage. The engine removes silences, retakes (keeps the last take), mistake talk ("افف انا نسيت الكلام"), fillers, and "reading the script" moments, then the panel builds a new sequence from subclips. Its primary language is **Egyptian Arabic**; English also works.

## Repo map
| Path | Language / runtime | Role |
|---|---|---|
| `CSXS/manifest.xml` | XML | CEP manifest. Host `PPRO [22.0,99.9]`, CSXS 11, Node enabled (`--enable-nodejs --mixed-context`) |
| `index.html`, `css/`, `js/main.js` | Browser JS **+ Node** (CEP) | Panel UI. Spawns the engine with `child_process`, parses stdout, calls JSX |
| `jsx/host.jsx` | **ExtendScript = ES3** | Premiere DOM: read V1 clips, create subclips, build sequence, markers, seek |
| `engine/analyze.py` | Python ≥3.10 | CLI entry: `python -u analyze.py --job job.json --out result.json` |
| `engine/detectors.py` | Python | Pure functions: normalization, gaps, noise, retakes, stutters, fillers, mistake cues |
| `engine/script_align.py` | Python | Fuzzy alignment of phrases to a user script; off-script scoring |
| `engine/plan.py` | Python | Combines detections into cuts → keep ranges, markers, and transcript view |
| `engine/vision.py` | Python | MediaPipe FaceLandmarker gaze/head-pose; robust z-score "looking away" |
| `engine/prosody.py` | Python | Per-phrase pitch variance + speech rate → "flat voice" |
| `tests/` | pytest | Synthetic data only; no GPU, Whisper or MediaPipe needed |

## Commands
```bash
pip install -r requirements-dev.txt
pytest -q                     # must stay green
ruff check engine tests       # must stay clean
node --check js/main.js
node -e "new Function(require('fs').readFileSync('jsx/host.jsx','utf8'))"
```
Full runtime deps (Whisper, CUDA DLLs, MediaPipe, OpenCV) are in `engine/requirements.txt` and are only needed on the user's Windows machine.

## Contracts (don't break these)
1. **Engine stdout protocol**, one line per message:
   `PROGRESS <0..1> <text>` · `LOG <text>` · `DONE`. The panel parses these with regexes in `js/main.js → runEngine`.
2. **job.json** (panel → engine): `{clips:[{index,nodeId,name,path,in,out,start,end}], settings:{...}, cacheDir}`. Times are seconds. `in/out` are **source** times; `start/end` are **timeline** times.
3. **result.json** (engine → panel): `pieces[{clipIndex,nodeId,s,e,new_start}]`, `new_markers[]`, `preview_markers[{t,d,name,comment,color}]`, `transcript[{t,d,text,cut,check,match}]`, `summary{...}`, `script_used`, `skipped`, `elapsed`.
4. **Settings**: every key the panel sends must exist in `analyze.DEFAULTS`. To add a setting: add it to `DEFAULTS`, to `FIELDS` in `js/main.js`, and to an element with the same `id` in `index.html`.
5. **Marker names start with `AC:`**. `AutoCut.clearMarkers` relies on that prefix.
6. **Cache keys** (`analyze.cache_key`) include path, size, mtime, range and `CACHE_VERSION`. Bump `CACHE_VERSION` if you change what is cached (word format, gaze features).

## Gotchas
- **`host.jsx` is ES3**: no `JSON`, `let/const`, arrow functions, `Array.prototype.map/forEach`, or template strings. Use the provided `AutoCut.str()` serializer.
- **Arabic text is always compared NORMALIZED** (`detectors.normalize`): diacritics removed, أ/إ/آ→ا, ة→ه, ى→ي, ؤ→و, ئ→ي. Write cue patterns in normalized form.
- **Arabic regex boundaries**: use the `(?:^|(?<=\s))…(?=\s|$)` wrapper from `detectors._compile`, not `\b` around multi-word phrases.
- **Mistake cues**: `_STRONG` cues are always cut, so only add phrases that are *never* real content. Anything that could be content ("غلط", "معلش", "نسيت" alone) goes in `_WEAK`, which only produces a review marker unless the phrase is also off-script and short.
- **Never auto-cut long phrases as off-script.** Users paraphrase the script heavily. A real 30 s paraphrase scored 51% on sequence match but 63% on word coverage. That is why `off_script()` uses `max(sequence match, word coverage)` and `plan.py` only cuts phrases of ≤ `offscript_max_words` words.
- **Prefer review markers to cuts when unsure.** False cuts destroy content; false keeps cost one click.
- Windows paths and non-ASCII (Arabic) file names are normal. Always open files with `encoding="utf-8"`.
- `engine/setup_engine.bat` must keep **CRLF** line endings (`.gitattributes` enforces this).
- Whisper is loaded with `device="cuda", compute_type="float16"`, falling back to CPU int8. CUDA DLLs come from pip `nvidia-*` wheels and are registered in `analyze._add_cuda_dlls()`.

## How to add a new detector
1. Write a pure function in `engine/detectors.py` returning `item(s, e, reason, detail)` dicts in **source seconds**.
2. Call it from `plan.plan_clip()`, appending to `cuts` (removed) or `reviews` (marker only).
3. Add a label/color in `plan.LABELS` / `plan.COLORS`, a priority in `plan.PRIORITY`, and a display name in `NAMES`/`TAGS` in `js/main.js`.
4. Add a toggle (see Contract 4) and a test in `tests/`.

## Testing philosophy
Tests build transcripts from `(phrase, start, end)` tuples (`tests/conftest.make_words`) and generate audio with numpy. When fixing a misdetection reported by a user, reproduce it as a test with synthetic text that has the same structure. **Don't commit users' real scripts or transcripts.**
