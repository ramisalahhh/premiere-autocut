"""Quick health check: run  .venv\\Scripts\\python check_setup.py"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ok = True
print("Python", sys.version.split()[0])
for mod in ("numpy", "faster_whisper", "ctranslate2", "av", "rapidfuzz", "librosa", "cv2", "mediapipe"):
    try:
        m = importlib.import_module(mod)
        print("  [ok] %-15s %s" % (mod, getattr(m, "__version__", "")))
    except Exception as e:
        ok = False
        print("  [!!] %-15s %s" % (mod, e))

try:
    from analyze import _add_cuda_dlls
    _add_cuda_dlls()
    import ctranslate2
    n = ctranslate2.get_cuda_device_count()
    print("  CUDA GPUs visible to Whisper:", n)
    if n == 0:
        print("  -> Whisper will run on CPU (slower). Update your NVIDIA driver if you have a GPU.")
except Exception as e:
    print("  CUDA check failed:", e)

try:
    import vision
    t = vision._Tracker()
    print("  [ok] face tracking:", "Tasks FaceLandmarker" if t.task else "legacy FaceMesh")
    t.close()
except Exception as e:
    ok = False
    print("  [!!] face tracking:", e)

print("\nAll good!" if ok else "\nSomething is missing - re-run setup_engine.bat")
