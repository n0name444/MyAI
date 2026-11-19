"""Диагностика окружения MyAI: python diagnostics.py"""
import importlib
import platform
import sys

from version import __version__

print(f"MyAI v{__version__}")
print("Python:", sys.version.split()[0], "|", platform.platform())

for mod in ("torch", "transformers", "bitsandbytes", "accelerate", "flask", "requests", "bs4", "pyautogui"):
    try:
        m = importlib.import_module(mod)
        print(f"  {mod:13} OK  {getattr(m, '__version__', '')}")
    except Exception as exc:
        print(f"  {mod:13} НЕТ ({type(exc).__name__}: {exc})")

try:
    import torch
    print("PyTorch CUDA-сборка:", torch.version.cuda or "НЕТ (CPU-сборка — модель будет работать очень медленно)")
    if torch.cuda.is_available():
        p = torch.cuda.get_device_properties(0)
        print(f"GPU: {p.name}, VRAM {p.total_memory / 1024 ** 3:.2f} GB")
    else:
        print("GPU: недоступна")
except Exception:
    pass

try:
    from memory import Memory
    print("Память:", Memory().stats())
except Exception as exc:
    print("Память: ошибка", exc)
