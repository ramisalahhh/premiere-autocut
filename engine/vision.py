"""Gaze / head-pose tracking to detect 'looking away from camera' (reading).

We sample the video a few times per second, track the face with MediaPipe
FaceMesh, and measure head pitch, eye openness, iris position and yaw.
Your *normal* pose is learned from the clip itself (median), so anything far
from it for a while (looking down at a paper/phone, looking to the side,
leaving the frame) is flagged.
"""
import math
import os

import numpy as np

FEATURES = ("pitch", "openness", "iris_v", "yaw")


MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/latest/face_landmarker.task")
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "face_landmarker.task")


def ensure_model():
    """Download the FaceLandmarker model once (needed by mediapipe >= 0.10.20 / 1.x)."""
    if os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 100000:
        return MODEL_PATH
    import urllib.request
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    tmp = MODEL_PATH + ".part"
    urllib.request.urlretrieve(MODEL_URL, tmp)
    os.replace(tmp, MODEL_PATH)
    return MODEL_PATH


class _Tracker:
    """Face landmarks via the new Tasks API, or the legacy FaceMesh if present."""

    def __init__(self):
        import mediapipe as mp
        self.mp = mp
        self.legacy = None
        self.task = None
        try:
            from mediapipe.tasks import python as mpt
            from mediapipe.tasks.python import vision as mpv
            opts = mpv.FaceLandmarkerOptions(
                base_options=mpt.BaseOptions(model_asset_path=ensure_model()),
                running_mode=mpv.RunningMode.VIDEO, num_faces=1,
                min_face_detection_confidence=0.5, min_tracking_confidence=0.5)
            self.task = mpv.FaceLandmarker.create_from_options(opts)
        except Exception as e_task:
            sols = getattr(mp, "solutions", None)
            if sols is None or not hasattr(sols, "face_mesh"):
                raise RuntimeError("MediaPipe face tracking unavailable: %s" % e_task) from e_task
            self.legacy = sols.face_mesh.FaceMesh(
                static_image_mode=False, max_num_faces=1, refine_landmarks=True,
                min_detection_confidence=0.5, min_tracking_confidence=0.5)
        self._last_ms = -1

    def landmarks(self, rgb, t_sec):
        if self.task is not None:
            ms = max(self._last_ms + 1, int(t_sec * 1000))  # must strictly increase
            self._last_ms = ms
            img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
            res = self.task.detect_for_video(img, ms)
            return res.face_landmarks[0] if res.face_landmarks else None
        res = self.legacy.process(rgb)
        return res.multi_face_landmarks[0].landmark if res.multi_face_landmarks else None

    def close(self):
        for obj in (self.task, self.legacy):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass


def extract_features(path, lo, hi, sample_fps=5.0, on_progress=None):
    """Return {"times": [...], "feats": [[pitch, openness, iris_v, yaw] or None]}."""
    import cv2

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("OpenCV could not open the video: %s" % path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(fps / sample_fps)))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, lo) * 1000.0)
    idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or round(lo * fps))
    first = idx

    mesh = _Tracker()

    times, feats = [], []
    span = max(1e-6, hi - lo)
    last_report = -1.0
    try:
        while True:
            t = idx / fps
            if t > hi:
                break
            if (idx - first) % step == 0:
                ok, frame = cap.read()
                if not ok:
                    break
                times.append(t)
                feats.append(_face_features(frame, mesh, cv2, t))
                frac = (t - lo) / span
                if on_progress and frac - last_report >= 0.02:
                    last_report = frac
                    on_progress(frac)
            else:
                if not cap.grab():
                    break
            idx += 1
    finally:
        mesh.close()
        cap.release()
    return {"times": times, "feats": feats}


def _face_features(frame, mesh, cv2, t):
    h, w = frame.shape[:2]
    if w > 640:
        frame = cv2.resize(frame, (640, int(h * 640 / w)))
    rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    lm = mesh.landmarks(rgb, t)
    if lm is None or len(lm) < 474:  # need the iris points (468-477)
        return None

    def X(i):
        return lm[i].x

    def Y(i):
        return lm[i].y

    face_h = Y(152) - Y(10)
    width = X(454) - X(234)
    if face_h <= 1e-4 or abs(width) <= 1e-4:
        return None
    open_l = Y(145) - Y(159)
    open_r = Y(374) - Y(386)
    eye_mid = (Y(159) + Y(386) + Y(145) + Y(374)) / 4.0
    pitch = (Y(1) - eye_mid) / face_h
    openness = (open_l + open_r) / (2.0 * face_h)
    iris_v = ((Y(468) - Y(159)) / max(open_l, 1e-4) +
              (Y(473) - Y(386)) / max(open_r, 1e-4)) / 2.0
    yaw = (X(1) - X(234)) / width
    vals = [pitch, openness, iris_v, yaw]
    return vals if all(math.isfinite(v) for v in vals) else None


def classify(data, z_thr=4.0, smooth=5):
    """Return (times ndarray, away bool ndarray) or None if no usable face."""
    times = np.array(data["times"], dtype=float)
    if len(times) == 0:
        return None
    F = np.array([f if f else [np.nan] * 4 for f in data["feats"]], dtype=float)
    present = ~np.isnan(F[:, 0])
    if present.sum() < max(10, 0.3 * len(times)):
        return None  # no face most of the time (screen recording, B-roll...)

    med = np.nanmedian(F, axis=0)
    mad = np.nanmedian(np.abs(F - med), axis=0) * 1.4826 + 1e-6
    Z = np.nan_to_num((F - med) / mad, nan=0.0)
    score = np.max(np.stack([
        np.abs(Z[:, 0]),               # head up/down
        np.clip(-Z[:, 1], 0, None),    # eyes lowered (lids closing)
        np.abs(Z[:, 2]),               # iris up/down
        np.abs(Z[:, 3]),               # head turned
    ], axis=1), axis=1)
    away = (~present) | (np.nan_to_num(score, nan=0.0) >= z_thr)

    if smooth > 1 and len(away) >= smooth:  # majority filter (kills blinks)
        k = smooth // 2
        padded = np.pad(away.astype(int), k, mode="edge")
        win = np.lib.stride_tricks.sliding_window_view(padded, smooth)
        away = win.sum(axis=1) > k
    return times, away


def away_fraction(times, away, s, e):
    m = (times >= s) & (times <= e)
    return float(away[m].mean()) if m.any() else 0.0


def away_regions(times, away, min_dur=1.2):
    out = []
    if len(times) < 2:
        return out
    dt = float(np.median(np.diff(times)))
    i = 0
    while i < len(away):
        if away[i]:
            j = i
            while j + 1 < len(away) and away[j + 1]:
                j += 1
            s, e = times[i], times[j] + dt
            if e - s >= min_dur:
                out.append((float(s), float(e)))
            i = j + 1
        else:
            i += 1
    return out
