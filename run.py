#!/usr/bin/env python3
"""
Convenient launcher for Video Vault 4K.
Usage:
    python run.py gui          # Launches desktop GUI
    python run.py encode ...   # Command-line encode
    python run.py decode ...   # Command-line decode
"""

import sys
from video_vault.cli import main

if __name__ == "__main__":
    sys.exit(main())
