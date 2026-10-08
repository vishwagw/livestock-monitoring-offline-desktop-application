#!/usr/bin/env python3
"""Write the 55k-detection station survey used in the product video as raw logs.

    python demo/video/make_survey.py demo/video/data
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from livestock_engine.simulate import export_raw_logs, profile_config, simulate_survey  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo/video/data")
dataset = simulate_survey(profile_config("stress", seed=0))
export_raw_logs(dataset, out)
print(f"{out}: {len(dataset.frames)} images, {dataset.n_detections} detections, {len(dataset.ground_truth)} animals")
