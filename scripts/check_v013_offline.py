"""Backward-compatible entry point for the retained energy-history checks.

The v13 shared refresh queue was removed in v0.9.19.14. Run the current
regressions instead of importing the deleted queue implementation.
"""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("check_v014_offline.py")), run_name="__main__")
