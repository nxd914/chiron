import sys
from strategies.crypto.research.datamodule import _load_cpp_backend

backend = _load_cpp_backend()
if backend is None:
    print("FAILED: C++ backend failed to load. Falling back to Python.")
    sys.exit(1)
else:
    print("SUCCESS: C++ backend loaded successfully.")
    sys.exit(0)
