import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import part2_collect_bed_status
from common import PublicDataApiError


def response(hpid: str = "A") -> ET.Element:
    return ET.fromstring(
        f"<response><totalCount>1</totalCount><item><hpid>{hpid}</hpid></item></response>"
    )


class BedApiCircuitBreakerTests(unittest.TestCase):
    def test_retry_success_does_not_close_circuit_tripped_by_another_worker(self) -> None:
        circuit = part2_collect_bed_status.BedApiCircuitBreaker()

        self.assertTrue(circuit.begin_quota_retry())
        circuit.trip()
        circuit.finish_quota_retry(recovered=True)

        self.assertTrue(circuit.is_tripped())
        with self.assertRaises(part2_collect_bed_status.BedApiQuotaCircuitOpen):
            circuit.wait_until_request_allowed()

    def test_retry_sleep_interrupt_opens_circuit_and_releases_waiter(self) -> None:
        quota = PublicDataApiError(
            "quota exhausted",
            kind="http",
            status_code=429,
        )
        circuit = part2_collect_bed_status.BedApiCircuitBreaker()
        waiter_started = threading.Event()
        waiter_finished = threading.Event()
        waiter_errors: list[BaseException] = []
        waiter: threading.Thread | None = None

        def wait_for_circuit() -> None:
            waiter_started.set()
            try:
                circuit.wait_until_request_allowed()
            except BaseException as error:
                waiter_errors.append(error)
            finally:
                waiter_finished.set()

        def interrupt_retry_sleep(_delay: float) -> None:
            nonlocal waiter
            waiter = threading.Thread(target=wait_for_circuit, daemon=True)
            waiter.start()
            self.assertTrue(waiter_started.wait(timeout=1))
            raise KeyboardInterrupt()

        with (
            patch.dict(os.environ, {"BED_API_MAX_ATTEMPTS": "3"}),
            patch.object(
                part2_collect_bed_status,
                "request_xml",
                side_effect=quota,
            ),
            patch.object(
                part2_collect_bed_status.time,
                "sleep",
                side_effect=interrupt_retry_sleep,
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                part2_collect_bed_status.collect_region(
                    "province",
                    "district",
                    circuit,
                )

        self.assertTrue(waiter_finished.wait(timeout=1))
        self.assertIsNotNone(waiter)
        waiter.join(timeout=1)
        self.assertFalse(waiter.is_alive())
        self.assertTrue(circuit.is_tripped())
        self.assertEqual(len(waiter_errors), 1)
        self.assertIsInstance(
            waiter_errors[0],
            part2_collect_bed_status.BedApiQuotaCircuitOpen,
        )

    def test_retry_request_base_exception_opens_circuit(self) -> None:
        quota = PublicDataApiError(
            "quota exhausted",
            kind="result",
            result_code="22",
        )
        circuit = part2_collect_bed_status.BedApiCircuitBreaker()

        with (
            patch.dict(os.environ, {"BED_API_MAX_ATTEMPTS": "3"}),
            patch.object(
                part2_collect_bed_status,
                "request_xml",
                side_effect=[quota, SystemExit("interrupted")],
            ),
            patch.object(part2_collect_bed_status.time, "sleep"),
        ):
            with self.assertRaises(SystemExit):
                part2_collect_bed_status.collect_region(
                    "province",
                    "district",
                    circuit,
                )

        self.assertTrue(circuit.is_tripped())
        with self.assertRaises(part2_collect_bed_status.BedApiQuotaCircuitOpen):
            circuit.wait_until_request_allowed()

    def test_float_csv_timestamp_is_parsed_as_korea_local_time(self) -> None:
        fresh = part2_collect_bed_status.fresh_bed_source_mask(
            pd.Series([20260831180000.0, pd.NA]),
            reference=pd.Timestamp("2026-08-31T10:00:00Z"),
            max_age_hours=12,
        )

        self.assertEqual(fresh.tolist(), [True, False])

    def test_quota_response_gets_one_bounded_retry_then_opens_shared_circuit(self) -> None:
        quota = PublicDataApiError(
            "공공데이터 API 응답 오류(resultCode=22)",
            kind="result",
            result_code="22",
        )
        with (
            patch.dict(os.environ, {"BED_API_MAX_ATTEMPTS": "3"}),
            patch.object(
                part2_collect_bed_status,
                "request_xml",
                side_effect=quota,
            ) as request,
            patch.object(part2_collect_bed_status.time, "sleep") as sleep,
        ):
            records, failures = part2_collect_bed_status.collect_regions(
                [("서울", "종로"), ("서울", "중구"), ("서울", "용산")],
                max_workers=1,
            )

        self.assertEqual(records, [])
        self.assertEqual(len(failures), 3)
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_retry_after_allows_one_shared_retry_and_collection_recovers(self) -> None:
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
                side_effect=[throttled, response("A"), response("B")],
            ) as request,
            patch.object(part2_collect_bed_status.time, "sleep") as sleep,
        ):
            records, failures = part2_collect_bed_status.collect_regions(
                [("서울", "종로"), ("서울", "중구")],
                max_workers=1,
            )

        self.assertEqual(records, [{"hpid": "A"}, {"hpid": "B"}])
        self.assertEqual(failures, [])
        self.assertEqual(request.call_count, 3)
        sleep.assert_called_once_with(7.0)

    def test_result_code_21_also_opens_the_shared_circuit(self) -> None:
        disabled_key = PublicDataApiError(
            "공공데이터 API 응답 오류(resultCode=21)",
            kind="result",
            result_code="21",
        )
        with (
            patch.dict(os.environ, {"BED_API_MAX_ATTEMPTS": "3"}),
            patch.object(
                part2_collect_bed_status,
                "request_xml",
                side_effect=disabled_key,
            ) as request,
            patch.object(part2_collect_bed_status.time, "sleep"),
        ):
            _, failures = part2_collect_bed_status.collect_regions(
                [("서울", "종로"), ("서울", "중구"), ("서울", "용산")],
                max_workers=1,
            )

        self.assertEqual(len(failures), 3)
        self.assertEqual(request.call_count, 2)

    def test_concurrent_quota_failures_never_start_more_than_workers_plus_one_calls(self) -> None:
        quota = PublicDataApiError(
            "공공데이터 API HTTP 오류: 429",
            kind="http",
            status_code=429,
        )
        worker_count = 4
        first_wave = threading.Barrier(worker_count)
        counter_lock = threading.Lock()
        request_count = 0

        def concurrent_quota(*_args, **_kwargs):
            nonlocal request_count
            with counter_lock:
                request_count += 1
                current = request_count
            if current <= worker_count:
                first_wave.wait(timeout=2)
            raise quota

        with (
            patch.dict(os.environ, {"BED_API_MAX_ATTEMPTS": "3"}),
            patch.object(
                part2_collect_bed_status,
                "request_xml",
                side_effect=concurrent_quota,
            ),
            patch.object(part2_collect_bed_status.time, "sleep"),
        ):
            records, failures = part2_collect_bed_status.collect_regions(
                [("서울", str(index)) for index in range(20)],
                max_workers=worker_count,
            )

        self.assertEqual(records, [])
        self.assertEqual(len(failures), 20)
        self.assertLessEqual(request_count, worker_count + 1)

    def test_non_quota_transient_errors_keep_configured_retry_policy(self) -> None:
        unavailable = PublicDataApiError(
            "공공데이터 API HTTP 오류: 503",
            kind="http",
            status_code=503,
        )
        with (
            patch.dict(os.environ, {"BED_API_MAX_ATTEMPTS": "3"}),
            patch.object(
                part2_collect_bed_status,
                "request_xml",
                side_effect=[unavailable, unavailable, response()],
            ) as request,
            patch.object(part2_collect_bed_status.time, "sleep") as sleep,
        ):
            records = part2_collect_bed_status.collect_region("서울", "종로")

        self.assertEqual(records, [{"hpid": "A"}])
        self.assertEqual(request.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])


if __name__ == "__main__":
    unittest.main()
