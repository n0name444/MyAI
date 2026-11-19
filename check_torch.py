"""Вспомогательная проверка PyTorch для start.bat.

Коды выхода:
  0 — установлена сборка PyTorch с CUDA (с флагом --gpu: и GPU доступен)
  1 — PyTorch не установлен
  2 — установлена CPU-сборка PyTorch (без CUDA)
  3 — только с --gpu: сборка с CUDA есть, но видеокарта недоступна
"""
import sys

try:
    import torch
except Exception:
    sys.exit(1)

if not torch.version.cuda:
    sys.exit(2)

if "--gpu" in sys.argv:
    if not torch.cuda.is_available():
        sys.exit(3)
    print("GPU:", torch.cuda.get_device_name(0))
sys.exit(0)
