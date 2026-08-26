#!/usr/bin/env python3
"""Compatibility entry point; the PC implementation lives in pc_tools/."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pc_tools.pc_stream_dataset import PcStreamDataset, main

__all__ = ["PcStreamDataset"]


if __name__ == "__main__":
    main()
