from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import part4_analyze


class HeatmapTimezoneTests(unittest.TestCase):
    def test_mixed_offsets_are_grouped_by_korean_day_and_hour(self) -> None:
        history = pd.DataFrame(
            {
                "수집시각": [
                    "2026-08-31T23:30:00+09:00",
                    "2026-08-31T14:30:00+00:00",
                    "2026-08-31T15:30:00+00:00",
                ],
                "포화율": [10, 30, 50],
            }
        )
        saved = {}

        def capture(frame: pd.DataFrame, path: Path) -> None:
            saved["frame"] = frame.copy()
            saved["path"] = path

        with (
            patch.object(part4_analyze, "read_csv", return_value=history),
            patch.object(part4_analyze, "save_csv", side_effect=capture),
        ):
            part4_analyze.build_heatmap()

        matrix = saved["frame"]
        monday = matrix.loc[matrix["요일"].eq("월")].iloc[0]
        tuesday = matrix.loc[matrix["요일"].eq("화")].iloc[0]
        self.assertEqual(monday[23], 20)
        self.assertEqual(tuesday[0], 50)
        self.assertEqual(saved["path"].name, "heatmap_matrix.csv")


if __name__ == "__main__":
    unittest.main()
