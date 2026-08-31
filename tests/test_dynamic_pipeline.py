import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import part2_collect_bed_status
import run_bed_refresh
from common import PublicDataApiError


class BedApiRetryTests(unittest.TestCase):
    def test_collect_region_retries_transient_error(self) -> None:
        root = ET.fromstring("<response><totalCount>1</totalCount><item><hpid>A</hpid></item></response>")
        with (
            patch.dict(os.environ, {"BED_API_MAX_ATTEMPTS": "3"}),
            patch.object(
                part2_collect_bed_status,
                "request_xml",
                side_effect=[RuntimeError("공공데이터 API HTTP 오류: 503"), root],
            ) as request,
            patch.object(part2_collect_bed_status.time, "sleep") as sleep,
        ):
            result = part2_collect_bed_status.collect_region("서울특별시", "종로구")

        self.assertEqual(result, [{"hpid": "A"}])
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_collect_region_does_not_retry_permanent_error(self) -> None:
        with (
            patch.dict(os.environ, {"BED_API_MAX_ATTEMPTS": "3"}),
            patch.object(
                part2_collect_bed_status,
                "request_xml",
                side_effect=RuntimeError("공공데이터 API HTTP 오류: 400"),
            ) as request,
            patch.object(part2_collect_bed_status.time, "sleep") as sleep,
            self.assertRaises(RuntimeError),
        ):
            part2_collect_bed_status.collect_region("서울특별시", "종로구")

        request.assert_called_once()
        sleep.assert_not_called()

    def test_collect_region_retries_portal_rate_limit_and_honors_retry_after(self) -> None:
        root = ET.fromstring("<response><totalCount>1</totalCount><item><hpid>A</hpid></item></response>")
        throttled = PublicDataApiError(
            "공공데이터 API HTTP 오류: 429",
            kind="http",
            status_code=429,
            retry_after="7",
        )
        with (
            patch.dict(os.environ, {"BED_API_MAX_ATTEMPTS": "3"}),
            patch.object(
                part2_collect_bed_status,
                "request_xml",
                side_effect=[throttled, root],
            ) as request,
            patch.object(part2_collect_bed_status.time, "sleep") as sleep,
        ):
            result = part2_collect_bed_status.collect_region("서울특별시", "종로구")

        self.assertEqual(result, [{"hpid": "A"}])
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(7.0)

    def test_collect_region_retries_temporary_xml_result_error(self) -> None:
        root = ET.fromstring("<response><totalCount>1</totalCount><item><hpid>A</hpid></item></response>")
        temporary = PublicDataApiError(
            "공공데이터 API 응답 오류(resultCode=22)",
            kind="result",
            result_code="22",
        )
        with (
            patch.dict(os.environ, {"BED_API_MAX_ATTEMPTS": "3"}),
            patch.object(
                part2_collect_bed_status,
                "request_xml",
                side_effect=[temporary, root],
            ) as request,
            patch.object(part2_collect_bed_status.time, "sleep") as sleep,
        ):
            result = part2_collect_bed_status.collect_region("서울특별시", "종로구")

        self.assertEqual(result, [{"hpid": "A"}])
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_collect_region_retries_malformed_xml_response(self) -> None:
        root = ET.fromstring("<response><totalCount>1</totalCount><item><hpid>A</hpid></item></response>")
        malformed = PublicDataApiError(
            "공공데이터 API 응답 형식을 해석할 수 없습니다.",
            kind="parse",
        )
        with (
            patch.dict(os.environ, {"BED_API_MAX_ATTEMPTS": "3"}),
            patch.object(
                part2_collect_bed_status,
                "request_xml",
                side_effect=[malformed, root],
            ) as request,
            patch.object(part2_collect_bed_status.time, "sleep") as sleep,
        ):
            result = part2_collect_bed_status.collect_region("서울특별시", "종로구")

        self.assertEqual(result, [{"hpid": "A"}])
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1)


class BedHistoryRetentionTests(unittest.TestCase):
    def test_trim_history_removes_rows_older_than_retention_window(self) -> None:
        now = pd.Timestamp.now(tz="UTC")
        history = pd.DataFrame(
            {
                "기관코드": ["OLD", "FRESH"],
                "수집시각": [
                    (now - pd.Timedelta(days=91)).isoformat(),
                    (now - pd.Timedelta(days=1)).isoformat(),
                ],
            }
        )

        with patch.dict(os.environ, {"BED_HISTORY_RETENTION_DAYS": "90"}):
            result = part2_collect_bed_status.trim_history(history)

        self.assertEqual(result["기관코드"].tolist(), ["FRESH"])
        self.assertEqual(result.index.tolist(), [0])

    def test_trim_history_is_disabled_when_setting_is_empty(self) -> None:
        history = pd.DataFrame(
            {
                "기관코드": ["OLD"],
                "수집시각": ["2000-01-01T00:00:00+00:00"],
            }
        )

        with patch.dict(os.environ, {"BED_HISTORY_RETENTION_DAYS": ""}):
            result = part2_collect_bed_status.trim_history(history)

        self.assertIs(result, history)

    def test_trim_history_rejects_invalid_retention_settings(self) -> None:
        history = pd.DataFrame({"수집시각": []})

        for value in ("0", "-1", "not-a-number"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"BED_HISTORY_RETENTION_DAYS": value}):
                    with self.assertRaises(RuntimeError):
                        part2_collect_bed_status.trim_history(history)


class BedRefreshPromotionTests(unittest.TestCase):
    def test_keyboard_interrupt_restores_previous_live_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live_data = root / "data"
            staged_data = root / "staging" / "data"
            backup_data = root / "backup"
            state_dir = root / "state"
            live_data.mkdir()
            staged_data.mkdir(parents=True)
            state_dir.mkdir()
            (live_data / "marker.txt").write_text("old-data", encoding="utf-8")
            (staged_data / "marker.txt").write_text("new-data", encoding="utf-8")

            original_replace = os.replace

            def interrupt_staged_promotion(source, target):
                if Path(source) == staged_data:
                    raise KeyboardInterrupt
                return original_replace(source, target)

            with (
                patch.object(run_bed_refresh, "LIVE_DATA", live_data),
                patch.object(run_bed_refresh, "PIPELINE_STATE_DIR", state_dir),
                patch.object(
                    run_bed_refresh.os,
                    "replace",
                    side_effect=interrupt_staged_promotion,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                run_bed_refresh.promote_data(staged_data, backup_data)

            self.assertEqual(
                (live_data / "marker.txt").read_text(encoding="utf-8"),
                "old-data",
            )
            self.assertFalse(backup_data.exists())

    def test_successful_promotion_replaces_live_data_and_leaves_safe_commit_witness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live_data = root / "data"
            staged_data = root / "staging" / "data"
            backup_data = root / "backup"
            live_data.mkdir()
            staged_data.mkdir(parents=True)
            (live_data / "marker.txt").write_text("old-data", encoding="utf-8")
            (staged_data / "marker.txt").write_text("new-data", encoding="utf-8")

            with patch.object(run_bed_refresh, "LIVE_DATA", live_data):
                run_bed_refresh.promote_data(staged_data, backup_data)

            self.assertEqual(
                (live_data / "marker.txt").read_text(encoding="utf-8"),
                "new-data",
            )
            self.assertFalse(backup_data.exists())
            self.assertTrue(run_bed_refresh.committed_backup_path(backup_data).exists())

    def test_post_commit_cleanup_failure_does_not_fail_bed_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live_data = root / "data"
            staging_root = root / "staging"
            backup_data = root / ".bed-refresh-backup-test"
            committed_data = run_bed_refresh.committed_backup_path(backup_data)
            lock_path = root / ".pipeline.lock"
            live_data.mkdir()
            staging_root.mkdir()
            committed_data.mkdir()
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)

            with patch.object(
                run_bed_refresh.shutil,
                "rmtree",
                side_effect=PermissionError("simulated post-commit cleanup failure"),
            ):
                run_bed_refresh.cleanup_bed_refresh_run(
                    staging_root=staging_root,
                    backup_data=backup_data,
                    lock_fd=lock_fd,
                    lock_path=lock_path,
                    promotion_committed=True,
                )

            self.assertTrue(live_data.exists())
            self.assertFalse(lock_path.exists())

    def test_pre_commit_cleanup_retries_data_rollback_without_losing_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live_data = root / "data"
            staging_root = root / "staging"
            staged_data = staging_root / "data"
            backup_data = root / ".bed-refresh-backup-test"
            committed_data = run_bed_refresh.committed_backup_path(backup_data)
            lock_path = root / ".pipeline.lock"
            live_data.mkdir()
            staged_data.mkdir(parents=True)
            (live_data / "marker.txt").write_text("old-data", encoding="utf-8")
            (staged_data / "marker.txt").write_text("new-data", encoding="utf-8")
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
                patch.object(run_bed_refresh, "LIVE_DATA", live_data),
                patch.object(
                    run_bed_refresh.os,
                    "replace",
                    side_effect=fail_commit_and_first_data_restore,
                ),
            ):
                with self.assertRaises(PermissionError):
                    run_bed_refresh.promote_data(staged_data, backup_data)
                self.assertTrue(backup_data.exists())
                run_bed_refresh.cleanup_bed_refresh_run(
                    staging_root=staging_root,
                    backup_data=backup_data,
                    lock_fd=lock_fd,
                    lock_path=lock_path,
                    promotion_committed=False,
                )

            self.assertEqual(
                (live_data / "marker.txt").read_text(encoding="utf-8"),
                "old-data",
            )
            self.assertEqual(data_restore_attempts, 2)
            self.assertFalse(backup_data.exists())
            self.assertFalse(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
