"""Bakım betikleri.

Betikler `app` paketini içe aktarıyor, o da `backend/` kökünde duruyor. Depo
kökünden `python -m backend.scripts.<ad>` diye çağrıldığında sys.path'e depo
kökü giriyor ve `app` görünmüyor — komut `ModuleNotFoundError: No module named
'app'` ile ölüyordu. Paket kendi kökünü buraya ekliyor, böylece iki çağırma
biçimi de çalışıyor:

    PYTHONPATH=backend python -m scripts.admin_users
    python -m backend.scripts.admin_users
"""

import sys
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parents[1])
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
