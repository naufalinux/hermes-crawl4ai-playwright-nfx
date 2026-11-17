# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Naufalinux
import pathlib
import sys

# Make `provider` importable as a top-level module in tests; `agent.*` must come from a Hermes
# checkout on PYTHONPATH (see README, "Development").
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
