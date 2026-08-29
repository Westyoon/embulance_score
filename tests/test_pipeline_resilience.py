import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import part4_analyze
import run_pipeline


class PipelinePromotionTests(unittest.TestCase):
    def test_keyboard_interrupt_restores_data_and_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live_data = root / "data"
            staged_data = root / "staging" / "data"
            live_boundary = root / "koreaGeo.json"
            staged_boundary = root / "staging" / "koreaGeo.json"
            backup_data = root / "backup-data"
            backup_boundary = root / "backup-koreaGeo.json"
            live_data.mkdir()
            staged_data.mkdir(parents=True)
            (live_data / "marker.txt").write_text("old-data", encoding="utf-8")
            (staged_data / "marker.txt").write_text("new-data", encoding="utf-8")
            live_boundary.write_text("old-boundary", encoding="utf-8")
            staged_boundary.write_text("new-boundary", encoding="utf-8")

            original_replace = os.replace

            def interrupt_boundary(source, target):
                if Path(source) == staged_boundary:
                    raise KeyboardInterrupt
                return original_replace(source, target)

            with (
                patch.object(run_pipeline, "LIVE_DATA", live_data),
                patch.object(run_pipeline, "LIVE_BOUNDARY", live_boundary),
                patch.object(run_pipeline.os, "replace", side_effect=interrupt_boundary),
                self.assertRaises(KeyboardInterrupt),
            ):
                run_pipeline.promote(
                    staged_data,
                    staged_boundary,
                    backup_data,
                    backup_boundary,
                )

            self.assertEqual((live_data / "marker.txt").read_text(encoding="utf-8"), "old-data")
            self.assertEqual(live_boundary.read_text(encoding="utf-8"), "old-boundary")
            self.assertFalse(backup_data.exists())
            self.assertFalse(backup_boundary.exists())


class ClusterResilienceTests(unittest.TestCase):
    def test_identical_feature_vectors_emit_header_only_outputs(self) -> None:
        frame = pd.DataFrame(
            {
                "시군구코드": ["A", "B", "C"],
                "시군구명": ["A", "B", "C"],
                "병상포화도점수": [50.0, 50.0, 50.0],
                "접근성점수": [50.0, 50.0, 50.0],
                "인구대비병상점수": [50.0, 50.0, 50.0],
                "의료진부족점수": [50.0, 50.0, 50.0],
                "regionRisk": [50.0, 50.0, 50.0],
            }
        )
        outputs = {}

        def capture(output, path):
            outputs[Path(path).name] = output.copy()

        with (
            patch.object(part4_analyze, "read_csv", return_value=frame),
            patch.object(part4_analyze, "save_csv", side_effect=capture),
        ):
            part4_analyze.build_clusters()

        self.assertEqual(
            set(outputs),
            {"cluster_k_evaluation.csv", "cluster_result.csv", "cluster_profile.csv"},
        )
        self.assertTrue(all(output.empty for output in outputs.values()))
        self.assertEqual(list(outputs["cluster_k_evaluation.csv"].columns), ["k", "실루엣점수"])


if __name__ == "__main__":
    unittest.main()
