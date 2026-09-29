"""PyInstaller entry point for the frozen ``livestock-engine`` executable."""

import multiprocessing
import sys

from livestock_engine.cli import main

if __name__ == "__main__":
    # Required for any library that may spawn worker processes when frozen.
    multiprocessing.freeze_support()
    sys.exit(main())
