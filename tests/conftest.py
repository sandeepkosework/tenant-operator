import os
import sys

# Run from the repo root: `python -m pytest tests`. Keeps the operator's real
# Vault/Mongo/Kubernetes out of the tests: settings are patched per test.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
