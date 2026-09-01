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


def write_bed_reuse_fixture(
    root: Path,
    *,
    master_codes: tuple[str, ...] = ("B", "A"),
    bed_codes: tuple[str, ...] = ("A", "B"),
    collected_at: str = "2026-08-31T09:00:00+00:00",
) -> tuple[Path, Path]:
    master_path = root / "hospital_master.csv"
    bed_path = root / "bed_status.csv"
    master = pd.DataFrame(
        {
            "기관코드": master_codes,
            "병원명": [f"신규-{code}" for code in master_codes],
            "등급": ["센터"] * len(master_codes),
            "시도": ["서울특별시"] * len(master_codes),
            "시군구": ["종로구"] * len(master_codes),
        }
    )
    beds = pd.DataFrame(
        {
            "기관코드": bed_codes,
            "병원명": [f"이전-{code}" for code in bed_codes],
            "등급": ["이전등급"] * len(bed_codes),
            "시도": ["이전시도"] * len(bed_codes),
            "시군구": ["이전시군구"] * len(bed_codes),
            "가용병상": [5.0, 2.0][: len(bed_codes)],
            "전체병상": [10.0, 20.0][: len(bed_codes)],
            "포화율": [50.0, 90.0][: len(bed_codes)],
            "상태": ["여유", "포화"][: len(bed_codes)],
            "API기준시각": ["20260831180000"] * len(bed_codes),
            "수집시각": [collected_at] * len(bed_codes),
        }
    )
    master.to_csv(master_path, index=False, encoding="utf-8-sig")
    beds.to_csv(bed_path, index=False, encoding="utf-8-sig")
    return master_path, bed_path


class ManagedInputGenerationTests(unittest.TestCase):
    def test_explicit_fresh_bed_full_refresh_mode_is_supported(self) -> None:
        with patch.dict(os.environ, {"FULL_REFRESH_REUSE_BEDS": "false"}):
            self.assertFalse(run_pipeline.reuse_beds_enabled())

    def test_full_refresh_replaces_only_staged_managed_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_data = root / "image-data"
            live_data = root / "live-data"
            staged_data = root / "staged-data"
            source_data.mkdir()
            live_data.mkdir()
            staged_data.mkdir()

            for filename in run_pipeline.MANAGED_INPUT_FILENAMES:
                (source_data / filename).write_text(
                    f"image:{filename}",
                    encoding="utf-8",
                )
                (live_data / filename).write_text(
                    f"live:{filename}",
                    encoding="utf-8",
                )
                (staged_data / filename).write_text(
                    f"old-stage:{filename}",
                    encoding="utf-8",
                )

            run_pipeline.copy_managed_inputs(staged_data, source_data)

            for filename in run_pipeline.MANAGED_INPUT_FILENAMES:
                self.assertEqual(
                    (staged_data / filename).read_text(encoding="utf-8"),
                    f"image:{filename}",
                )
                self.assertEqual(
                    (live_data / filename).read_text(encoding="utf-8"),
                    f"live:{filename}",
                )

    def test_full_refresh_rejects_missing_managed_input_before_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_data = root / "image-data"
            staged_data = root / "staged-data"
            source_data.mkdir()
            staged_data.mkdir()
            missing = run_pipeline.MANAGED_INPUT_FILENAMES[-1]
            for filename in run_pipeline.MANAGED_INPUT_FILENAMES[:-1]:
                (source_data / filename).write_text("image", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, missing):
                run_pipeline.copy_managed_inputs(staged_data, source_data)

            self.assertEqual(list(staged_data.iterdir()), [])


class BedSnapshotReuseTests(unittest.TestCase):
    NOW = pd.Timestamp("2026-08-31T10:00:00+00:00")

    def test_valid_snapshot_rebases_fresh_master_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            master_path, bed_path = write_bed_reuse_fixture(Path(directory))

            rebased, audit = run_pipeline.validate_reusable_bed_snapshot(
                master_path,
                bed_path,
                max_age_hours=6,
                minimum_usable_hospitals=2,
                now=self.NOW,
            )

        self.assertEqual(rebased["기관코드"].tolist(), ["B", "A"])
        self.assertEqual(rebased["병원명"].tolist(), ["신규-B", "신규-A"])
        self.assertEqual(rebased["가용병상"].tolist(), [2.0, 5.0])
        self.assertEqual(
            audit,
            {
                "reused": True,
                "snapshotCollectedAt": "2026-08-31T09:00:00+00:00",
                "snapshotAgeMinutes": 60.0,
                "usableHospitals": 2,
                "staleSourceHospitals": 0,
                "sanitizedSourceHospitals": 0,
                "maxAgeHours": 6,
                "sourceMaxAgeHours": 12.0,
            },
        )

    def test_stale_upstream_timestamp_is_cleared_before_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            master_path, bed_path = write_bed_reuse_fixture(Path(directory))
            beds = pd.read_csv(bed_path)
            beds.loc[beds["기관코드"].eq("B"), "API기준시각"] = 20180503175041
            beds.to_csv(bed_path, index=False, encoding="utf-8-sig")

            rebased, audit = run_pipeline.validate_reusable_bed_snapshot(
                master_path,
                bed_path,
                max_age_hours=6,
                max_source_age_hours=12,
                minimum_usable_hospitals=1,
                now=self.NOW,
            )

        stale = rebased.loc[rebased["기관코드"].eq("B")].iloc[0]
        self.assertTrue(pd.isna(stale["가용병상"]))
        self.assertTrue(pd.isna(stale["전체병상"]))
        self.assertTrue(pd.isna(stale["포화율"]))
        self.assertEqual(stale["상태"], "결측")
        self.assertEqual(audit["usableHospitals"], 1)
        self.assertEqual(audit["staleSourceHospitals"], 1)
        self.assertEqual(audit["sanitizedSourceHospitals"], 1)

        with tempfile.TemporaryDirectory() as directory:
            master_path, bed_path = write_bed_reuse_fixture(Path(directory))
            rebased.to_csv(bed_path, index=False, encoding="utf-8-sig")
            _, second_audit = run_pipeline.validate_reusable_bed_snapshot(
                master_path,
                bed_path,
                max_age_hours=6,
                max_source_age_hours=12,
                minimum_usable_hospitals=1,
                now=self.NOW,
            )

        self.assertEqual(second_audit["staleSourceHospitals"], 1)
        self.assertEqual(second_audit["sanitizedSourceHospitals"], 0)

    def test_stale_snapshot_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            master_path, bed_path = write_bed_reuse_fixture(
                Path(directory),
                collected_at="2026-08-30T23:59:59+00:00",
            )
            with self.assertRaisesRegex(RuntimeError, "너무 오래되었습니다"):
                run_pipeline.validate_reusable_bed_snapshot(
                    master_path,
                    bed_path,
                    max_age_hours=6,
                    minimum_usable_hospitals=2,
                    now=self.NOW,
                )

    def test_master_code_set_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            master_path, bed_path = write_bed_reuse_fixture(
                Path(directory),
                master_codes=("A", "C"),
            )
            with self.assertRaisesRegex(RuntimeError, "모집단과 정확히 일치하지 않습니다"):
                run_pipeline.validate_reusable_bed_snapshot(
                    master_path,
                    bed_path,
                    max_age_hours=6,
                    minimum_usable_hospitals=2,
                    now=self.NOW,
                )

    def test_insufficient_usable_hospitals_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            master_path, bed_path = write_bed_reuse_fixture(Path(directory))
            beds = pd.read_csv(bed_path)
            beds.loc[beds["기관코드"].eq("B"), ["가용병상", "전체병상", "포화율"]] = pd.NA
            beds.to_csv(bed_path, index=False, encoding="utf-8-sig")
            with self.assertRaisesRegex(RuntimeError, "유효 기관 수"):
                run_pipeline.validate_reusable_bed_snapshot(
                    master_path,
                    bed_path,
                    max_age_hours=6,
                    minimum_usable_hospitals=2,
                    now=self.NOW,
                )

    def test_inconsistent_saturation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            master_path, bed_path = write_bed_reuse_fixture(Path(directory))
            beds = pd.read_csv(bed_path)
            beds.loc[beds["기관코드"].eq("A"), "포화율"] = 49.0
            beds.to_csv(bed_path, index=False, encoding="utf-8-sig")
            with self.assertRaisesRegex(RuntimeError, "맞지 않는 포화율"):
                run_pipeline.validate_reusable_bed_snapshot(
                    master_path,
                    bed_path,
                    max_age_hours=6,
                    minimum_usable_hospitals=2,
                    now=self.NOW,
                )

    def test_future_timestamp_beyond_skew_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            master_path, bed_path = write_bed_reuse_fixture(
                Path(directory),
                collected_at="2026-08-31T10:06:00+00:00",
            )
            with self.assertRaisesRegex(RuntimeError, "미래 오차"):
                run_pipeline.validate_reusable_bed_snapshot(
                    master_path,
                    bed_path,
                    max_age_hours=6,
                    minimum_usable_hospitals=2,
                    now=self.NOW,
                )


class PipelinePromotionTests(unittest.TestCase):
    def test_post_commit_cleanup_failure_does_not_fail_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live_data = root / "data"
            staging_root = root / "staging"
            backup_data = root / ".pipeline-backup-test"
            backup_boundary = root / ".pipeline-backup-test.koreaGeo.json"
            committed_data = run_pipeline.committed_backup_path(backup_data)
            lock_path = root / ".pipeline.lock"
            live_data.mkdir()
            staging_root.mkdir()
            committed_data.mkdir()
            backup_boundary.write_text("old-boundary", encoding="utf-8")
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)

            with patch.object(
                run_pipeline.shutil,
                "rmtree",
                side_effect=PermissionError("simulated post-commit cleanup failure"),
            ):
                run_pipeline.cleanup_pipeline_run(
                    staging_root=staging_root,
                    backup_data=backup_data,
                    backup_boundary=backup_boundary,
                    lock_fd=lock_fd,
                    lock_path=lock_path,
                    promotion_committed=True,
                )

            self.assertTrue(live_data.exists())
            self.assertFalse(backup_boundary.exists())
            self.assertFalse(lock_path.exists())

    def test_pre_commit_cleanup_retries_boundary_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live_data = root / "data"
            staged_data = root / "staging" / "data"
            live_boundary = root / "koreaGeo.json"
            staged_boundary = root / "staging" / "koreaGeo.json"
            backup_data = root / ".pipeline-backup-test"
            backup_boundary = root / ".pipeline-backup-test.koreaGeo.json"
            committed_data = run_pipeline.committed_backup_path(backup_data)
            lock_path = root / ".pipeline.lock"
            live_data.mkdir()
            staged_data.mkdir(parents=True)
            (live_data / "marker.txt").write_text("old-data", encoding="utf-8")
            (staged_data / "marker.txt").write_text("new-data", encoding="utf-8")
            live_boundary.write_text("old-boundary", encoding="utf-8")
            staged_boundary.write_text("new-boundary", encoding="utf-8")
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)

            original_replace = os.replace
            boundary_restore_attempts = 0

            def fail_commit_and_first_boundary_restore(source, target):
                nonlocal boundary_restore_attempts
                source_path, target_path = Path(source), Path(target)
                if source_path == backup_data and target_path == committed_data:
                    raise PermissionError("simulated commit rename failure")
                if source_path == backup_boundary and target_path == live_boundary:
                    boundary_restore_attempts += 1
                    if boundary_restore_attempts == 1:
                        raise PermissionError("simulated first boundary rollback failure")
                return original_replace(source, target)

            with (
                patch.object(run_pipeline, "LIVE_DATA", live_data),
                patch.object(run_pipeline, "LIVE_BOUNDARY", live_boundary),
                patch.object(
                    run_pipeline.os,
                    "replace",
                    side_effect=fail_commit_and_first_boundary_restore,
                ),
            ):
                with self.assertRaises(PermissionError):
                    run_pipeline.promote(
                        staged_data,
                        staged_boundary,
                        backup_data,
                        backup_boundary,
                    )
                run_pipeline.cleanup_pipeline_run(
                    staging_root=staged_data.parent,
                    backup_data=backup_data,
                    backup_boundary=backup_boundary,
                    lock_fd=lock_fd,
                    lock_path=lock_path,
                    promotion_committed=False,
                )

            self.assertEqual((live_data / "marker.txt").read_text(encoding="utf-8"), "old-data")
            self.assertEqual(live_boundary.read_text(encoding="utf-8"), "old-boundary")
            self.assertFalse(backup_boundary.exists())
            self.assertEqual(boundary_restore_attempts, 2)

    def test_pre_commit_cleanup_retries_data_rollback_without_losing_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live_data = root / "data"
            staging_root = root / "staging"
            staged_data = staging_root / "data"
            live_boundary = root / "koreaGeo.json"
            staged_boundary = staging_root / "koreaGeo.json"
            backup_data = root / ".pipeline-backup-test"
            backup_boundary = root / ".pipeline-backup-test.koreaGeo.json"
            committed_data = run_pipeline.committed_backup_path(backup_data)
            lock_path = root / ".pipeline.lock"
            live_data.mkdir()
            staged_data.mkdir(parents=True)
            (live_data / "marker.txt").write_text("old-data", encoding="utf-8")
            (staged_data / "marker.txt").write_text("new-data", encoding="utf-8")
            live_boundary.write_text("old-boundary", encoding="utf-8")
            staged_boundary.write_text("new-boundary", encoding="utf-8")
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)

            original_replace = os.replace
            data_restore_attempts = 0

            def fail_commit_and_first_data_restore(source, target):
                nonlocal data_restore_attempts
                source_path, target_path = Path(source), Path(target)
                if source_path == backup_data and target_path == committed_data:
                    raise PermissionError("simulated commit rename failure")
                if (
                    source_path == live_data
                    and target_path.parent == staging_root
                    and target_path.name.startswith("failed-live-data-")
                ):
                    data_restore_attempts += 1
                    if data_restore_attempts == 1:
                        raise PermissionError("simulated first data rollback failure")
                return original_replace(source, target)

            with (
                patch.object(run_pipeline, "LIVE_DATA", live_data),
                patch.object(run_pipeline, "LIVE_BOUNDARY", live_boundary),
                patch.object(
                    run_pipeline.os,
                    "replace",
                    side_effect=fail_commit_and_first_data_restore,
                ),
            ):
                with self.assertRaises(PermissionError):
                    run_pipeline.promote(
                        staged_data,
                        staged_boundary,
                        backup_data,
                        backup_boundary,
                    )
                self.assertTrue(backup_data.exists())
                run_pipeline.cleanup_pipeline_run(
                    staging_root=staging_root,
                    backup_data=backup_data,
                    backup_boundary=backup_boundary,
                    lock_fd=lock_fd,
                    lock_path=lock_path,
                    promotion_committed=False,
                )

            self.assertEqual((live_data / "marker.txt").read_text(encoding="utf-8"), "old-data")
            self.assertEqual(live_boundary.read_text(encoding="utf-8"), "old-boundary")
            self.assertEqual(data_restore_attempts, 2)
            self.assertFalse(backup_data.exists())
            self.assertFalse(backup_boundary.exists())

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
