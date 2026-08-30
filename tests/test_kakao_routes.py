from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import part3_collect_kakao_routes as kakao_routes


class RouteRequestKeyTests(unittest.TestCase):
    def test_key_is_deterministic_coordinate_sensitive_and_versioned(self) -> None:
        key = kakao_routes.route_request_key(37.5665, 126.9780, 37.5701, 126.9823)

        self.assertEqual(
            key,
            kakao_routes.route_request_key(37.5665, 126.9780, 37.5701, 126.9823),
        )
        self.assertRegex(key, re.compile(r"^[0-9a-f]{64}$"))
        self.assertNotEqual(
            key,
            kakao_routes.route_request_key(37.566501, 126.9780, 37.5701, 126.9823),
        )
        self.assertNotEqual(
            key,
            kakao_routes.route_request_key(37.5701, 126.9823, 37.5665, 126.9780),
        )

        with patch.object(kakao_routes, "ROUTE_SCHEMA_VERSION", "test-schema-v2"):
            self.assertNotEqual(
                key,
                kakao_routes.route_request_key(37.5665, 126.9780, 37.5701, 126.9823),
            )


class RoutePayloadTests(unittest.TestCase):
    def test_success_converts_metres_and_seconds(self) -> None:
        result = kakao_routes.parse_route_payload(
            {
                "routes": [
                    {
                        "result_code": 0,
                        "summary": {"distance": 12_345, "duration": 930},
                    }
                ]
            }
        )

        self.assertEqual(result["경로상태"], "성공")
        self.assertEqual(result["경로결과코드"], 0)
        self.assertAlmostEqual(result["도로거리_km"], 12.345)
        self.assertAlmostEqual(result["예상시간_분"], 15.5)

    def test_nearby_result_code_104_is_a_zero_distance_success(self) -> None:
        result = kakao_routes.parse_route_payload(
            {"routes": [{"result_code": 104, "result_msg": "same point"}]}
        )

        self.assertEqual(result["경로상태"], "성공:출도착5m이내")
        self.assertEqual(result["경로결과코드"], 104)
        self.assertEqual(result["도로거리_km"], 0.0)
        self.assertEqual(result["예상시간_분"], 0.0)

    def test_empty_error_and_malformed_payloads_are_not_successes(self) -> None:
        cases = [
            ({"routes": []}, "경로없음", pd.NA),
            ({"routes": [{"result_code": 102}]}, "경로오류:102", pd.NA),
            ({"routes": [{"result_code": 0, "summary": {"distance": 100}}]}, "응답형식오류", pd.NA),
            (
                {"routes": [{"result_code": 0, "summary": {"distance": -1, "duration": 60}}]},
                "응답값오류",
                pd.NA,
            ),
        ]

        for payload, status, expected_distance in cases:
            with self.subTest(status=status):
                result = kakao_routes.parse_route_payload(payload)
                self.assertEqual(result["경로상태"], status)
                self.assertFalse(kakao_routes.is_success(result))
                self.assertEqual(pd.isna(result["도로거리_km"]), pd.isna(expected_distance))


class RouteHttpTests(unittest.TestCase):
    def test_fetch_route_uses_mocked_kakao_request_contract(self) -> None:
        response = Mock()
        response.status_code = 200
        response.headers = {}
        response.json.return_value = {
            "routes": [{"result_code": 0, "summary": {"distance": 2_500, "duration": 300}}]
        }
        session = Mock()
        session.get.return_value = response
        task = {
            "출발위도": 37.5665,
            "출발경도": 126.978,
            "도착위도": 37.5701,
            "도착경도": 126.9823,
        }

        with (
            patch.object(kakao_routes, "_http_session", return_value=session),
            patch.object(kakao_routes, "now_iso", return_value="2026-08-30T12:00:00+09:00"),
        ):
            result = kakao_routes.fetch_route(task, "local-test-key")

        self.assertEqual(result["도로거리_km"], 2.5)
        self.assertEqual(result["예상시간_분"], 5.0)
        self.assertEqual(result["수집시각"], "2026-08-30T12:00:00+09:00")
        session.get.assert_called_once()
        _, kwargs = session.get.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "KakaoAK local-test-key")
        self.assertEqual(kwargs["params"]["origin"], "126.9780000,37.5665000")
        self.assertEqual(kwargs["params"]["destination"], "126.9823000,37.5701000")
        self.assertEqual(kwargs["params"]["priority"], "DISTANCE")
        self.assertEqual(kwargs["timeout"], (10, 35))

    def test_fetch_route_retries_throttling_response_without_network(self) -> None:
        throttled = Mock(status_code=429, headers={"Retry-After": "0"})
        success = Mock(status_code=200, headers={})
        success.json.return_value = {
            "routes": [{"result_code": 0, "summary": {"distance": 1000, "duration": 60}}]
        }
        session = Mock()
        session.get.side_effect = [throttled, success]
        task = {"출발위도": 37.0, "출발경도": 127.0, "도착위도": 37.1, "도착경도": 127.1}

        with (
            patch.object(kakao_routes, "_http_session", return_value=session),
            patch.object(kakao_routes.time, "sleep") as sleep,
        ):
            result = kakao_routes.fetch_route(task, "local-test-key", attempts=2)

        self.assertEqual(result["경로상태"], "성공")
        self.assertEqual(session.get.call_count, 2)
        sleep.assert_called_once_with(0.0)


class BoundaryOriginTests(unittest.TestCase):
    def test_representative_point_returns_square_centroid(self) -> None:
        square = [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]

        lon, lat = kakao_routes.representative_point([square])

        self.assertAlmostEqual(lon, 1.0)
        self.assertAlmostEqual(lat, 1.0)

    def test_representative_point_stays_on_land_for_polygon_with_hole(self) -> None:
        polygon = [
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
            [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
        ]

        lon, lat = kakao_routes.representative_point([polygon])

        self.assertTrue(kakao_routes._point_in_polygon(lon, lat, polygon))
        self.assertFalse(kakao_routes._point_in_ring(lon, lat, polygon[1]))

    def test_build_boundary_origins_merges_city_district_features(self) -> None:
        geojson = {
            "type": "FeatureCollection",
            "metadata": {"version": "2026-08-22"},
            "features": [
                {
                    "type": "Feature",
                    "properties": {"sido": "경기도", "name": "수원시장안구"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[127.0, 37.2], [127.1, 37.2], [127.1, 37.3], [127.0, 37.3], [127.0, 37.2]]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"sido": "경기도", "name": "수원시권선구"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[127.2, 37.2], [127.3, 37.2], [127.3, 37.3], [127.2, 37.3], [127.2, 37.2]]],
                    },
                },
            ],
        }
        hospitals = pd.DataFrame({"시도": ["경기도"], "시군구": ["수원시"]})

        with tempfile.TemporaryDirectory() as directory:
            boundary = Path(directory) / "boundary.json"
            boundary.write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")
            with patch.object(kakao_routes, "boundary_file", return_value=boundary):
                origins, polygons_by_key = kakao_routes.build_boundary_origins(hospitals)

        self.assertEqual(len(origins), 1)
        row = origins.iloc[0]
        self.assertEqual(row["시군구코드"], "경기도|수원시")
        self.assertEqual(row["중심점방법"], "최신경계기하대표점")
        self.assertEqual(row["경계버전"], "2026-08-22")
        self.assertEqual(row["원본경계수"], 2)
        polygons = polygons_by_key["경기도|수원시"]
        self.assertEqual(len(polygons), 2)
        self.assertTrue(
            any(
                kakao_routes._point_in_polygon(float(row["기준경도"]), float(row["기준위도"]), polygon)
                for polygon in polygons
            )
        )


class RouteSelectionAndCacheTests(unittest.TestCase):
    @staticmethod
    def _adaptive_candidates(straight_distances: list[float]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "시군구코드": "R",
                    "후보순위": rank,
                    "기관코드": f"H{rank}",
                    "직선거리_km": straight_distance,
                    "경로요청키": f"key-{rank}",
                }
                for rank, straight_distance in enumerate(straight_distances, start=1)
            ]
        )

    @staticmethod
    def _route_result(distance: float) -> dict:
        return {
            "도로거리_km": distance,
            "예상시간_분": distance * 2,
            "경로결과코드": 0,
            "경로상태": "성공",
            "수집시각": "2026-08-30T12:00:00+09:00",
        }

    def test_select_best_uses_road_distance_and_requires_every_region(self) -> None:
        candidates = pd.DataFrame(
            [
                {"시군구코드": "A", "기관코드": "A-straight", "직선거리_km": 1.0, "도로거리_km": 8.0, "예상시간_분": 12.0, "경로상태": "성공"},
                {"시군구코드": "A", "기관코드": "A-road", "직선거리_km": 3.0, "도로거리_km": 4.0, "예상시간_분": 9.0, "경로상태": "성공"},
                {"시군구코드": "A", "기관코드": "A-failed", "직선거리_km": 0.5, "도로거리_km": 0.5, "예상시간_분": 1.0, "경로상태": "경로오류:102"},
                {"시군구코드": "B", "기관코드": "B-nearby", "직선거리_km": 0.0, "도로거리_km": 0.0, "예상시간_분": 0.0, "경로상태": "성공:출도착5m이내"},
            ]
        )

        best = kakao_routes.select_best_accessibility(candidates, {"A", "B"})

        self.assertEqual(best["기관코드"].tolist(), ["A-road", "B-nearby"])
        with self.assertRaisesRegex(RuntimeError, "C"):
            kakao_routes.select_best_accessibility(candidates, {"A", "B", "C"})

    def test_candidate_order_is_deterministic_for_equal_straight_distances(self) -> None:
        hospitals = pd.DataFrame(
            {
                "기관코드": ["B", "A", "C"],
                "병원명": ["B 병원", "A 병원", "C 병원"],
                "시도": ["테스트도"] * 3,
                "시군구": ["테스트시"] * 3,
                "위도": [37.1, 37.1, 37.2],
                "경도": [127.0, 127.0, 127.0],
            }
        )
        origins = pd.DataFrame(
            [
                {
                    "시군구코드": "테스트도|테스트시",
                    "시도": "테스트도",
                    "시군구": "테스트시",
                    "기준위도": 37.0,
                    "기준경도": 127.0,
                    "출발위도": 37.0,
                    "출발경도": 127.0,
                    "중심점방법": "test",
                    "경계버전": "test",
                    "도로보정_km": 0.0,
                }
            ]
        )

        candidates, _ = kakao_routes.build_task_frames(hospitals, hospitals, origins)

        self.assertEqual(candidates["기관코드"].tolist(), ["A", "B", "C"])
        self.assertEqual(candidates["후보순위"].tolist(), [1, 2, 3])

    def test_collect_routes_reuses_probe_cache_for_duplicate_route(self) -> None:
        key = kakao_routes.route_request_key(37.0, 127.0, 37.1, 127.1)
        candidate = pd.DataFrame([{"경로요청키": key, "종류": "candidate"}])
        hospital = pd.DataFrame([{"경로요청키": key, "종류": "hospital"}])
        cached_result = {
            "도로거리_km": 12.3,
            "예상시간_분": 18.0,
            "경로결과코드": 0,
            "경로상태": "성공",
            "수집시각": "2026-08-30T12:00:00+09:00",
        }

        with (
            patch.object(kakao_routes, "load_route_cache", return_value={}),
            patch.object(kakao_routes, "fetch_route") as fetch_route,
        ):
            collected_candidates, collected_hospitals, cache_hits, api_calls = kakao_routes.collect_routes(
                candidate,
                hospital,
                {key: cached_result},
                api_key="",
                refresh=False,
                workers=1,
            )

        fetch_route.assert_not_called()
        self.assertEqual(cache_hits, 1)
        self.assertEqual(api_calls, 0)
        self.assertEqual(collected_candidates.loc[0, "도로거리_km"], 12.3)
        self.assertEqual(collected_hospitals.loc[0, "예상시간_분"], 18.0)

    def test_adaptive_collection_expands_until_global_road_minimum_is_proven(self) -> None:
        candidates = self._adaptive_candidates([1.0, 2.0, 3.0, 8.0])
        road_distances = {"key-1": 10.0, "key-2": 8.0, "key-3": 4.0, "key-4": 9.0}

        def fetch(task: dict, _api_key: str) -> dict:
            return self._route_result(road_distances[task["경로요청키"]])

        with (
            patch.object(kakao_routes, "load_route_cache", return_value={}),
            patch.object(kakao_routes, "fetch_route", side_effect=fetch) as fetch_route,
        ):
            evaluated, _, cache_hits, api_calls = kakao_routes.collect_adaptive_routes(
                candidates,
                pd.DataFrame(columns=["경로요청키"]),
                {},
                api_key="local-test-key",
                refresh=False,
                workers=1,
                initial_candidates=2,
            )

        self.assertEqual(evaluated["후보순위"].tolist(), [1, 2, 3])
        self.assertEqual(kakao_routes.select_best_accessibility(evaluated, {"R"}).iloc[0]["기관코드"], "H3")
        self.assertEqual([call.args[0]["경로요청키"] for call in fetch_route.call_args_list], ["key-1", "key-2", "key-3"])
        self.assertEqual(cache_hits, 0)
        self.assertEqual(api_calls, 3)

    def test_adaptive_collection_stops_when_next_straight_lower_bound_cannot_win(self) -> None:
        candidates = self._adaptive_candidates([1.0, 2.0, 2.1])
        road_distances = {"key-1": 1.5, "key-2": 3.0, "key-3": 2.2}

        def fetch(task: dict, _api_key: str) -> dict:
            return self._route_result(road_distances[task["경로요청키"]])

        with (
            patch.object(kakao_routes, "load_route_cache", return_value={}),
            patch.object(kakao_routes, "fetch_route", side_effect=fetch) as fetch_route,
        ):
            evaluated, _, _, api_calls = kakao_routes.collect_adaptive_routes(
                candidates,
                pd.DataFrame(columns=["경로요청키"]),
                {},
                api_key="local-test-key",
                refresh=False,
                workers=1,
                initial_candidates=2,
            )

        self.assertEqual(evaluated["후보순위"].tolist(), [1, 2])
        self.assertEqual([call.args[0]["경로요청키"] for call in fetch_route.call_args_list], ["key-1", "key-2"])
        self.assertEqual(api_calls, 2)

    def test_adaptive_collection_consumes_cached_expansion_prefix_in_one_loop(self) -> None:
        candidates = self._adaptive_candidates([1.0, 2.0, 3.0, 4.0, 6.0])
        cache = {
            "key-1": self._route_result(10.0),
            "key-2": self._route_result(8.0),
            "key-3": self._route_result(5.0),
            "key-4": self._route_result(4.5),
        }

        with (
            patch.object(kakao_routes, "load_route_cache", return_value=cache.copy()),
            patch.object(kakao_routes, "fetch_route") as fetch_route,
            patch.object(
                kakao_routes,
                "_adaptive_expansion_indices",
                wraps=kakao_routes._adaptive_expansion_indices,
            ) as expansion,
        ):
            evaluated, _, cache_hits, api_calls = kakao_routes.collect_adaptive_routes(
                candidates,
                pd.DataFrame(columns=["경로요청키"]),
                {},
                api_key="",
                refresh=False,
                workers=1,
                initial_candidates=1,
            )

        fetch_route.assert_not_called()
        self.assertEqual(evaluated["후보순위"].tolist(), [1, 2, 3, 4])
        self.assertEqual(cache_hits, 4)
        self.assertEqual(api_calls, 0)
        self.assertEqual(expansion.call_count, 2)

    def test_adaptive_collection_stops_cached_scan_after_first_uncached_candidate(self) -> None:
        candidates = self._adaptive_candidates([1.0, 2.0, 3.0, 4.0])
        cache = {
            "key-1": self._route_result(10.0),
            "key-2": self._route_result(9.0),
        }
        road_distances = {"key-3": 4.0, "key-4": 5.0}

        def fetch(task: dict, _api_key: str) -> dict:
            return self._route_result(road_distances[task["경로요청키"]])

        with (
            patch.object(kakao_routes, "load_route_cache", return_value=cache.copy()),
            patch.object(kakao_routes, "fetch_route", side_effect=fetch) as fetch_route,
        ):
            evaluated, _, cache_hits, api_calls = kakao_routes.collect_adaptive_routes(
                candidates,
                pd.DataFrame(columns=["경로요청키"]),
                {},
                api_key="local-test-key",
                refresh=False,
                workers=1,
                initial_candidates=1,
            )

        self.assertEqual(evaluated["후보순위"].tolist(), [1, 2, 3])
        self.assertEqual([call.args[0]["경로요청키"] for call in fetch_route.call_args_list], ["key-3"])
        self.assertEqual(cache_hits, 2)
        self.assertEqual(api_calls, 1)

    def test_global_minimum_rejects_uncertain_failures_below_current_best(self) -> None:
        uncertain_statuses = [
            "HTTP오류:500",
            "API오류:Timeout",
            "응답형식오류",
            "응답값오류",
            "경로없음",
        ]
        for status in uncertain_statuses:
            with self.subTest(status=status):
                evaluated = pd.DataFrame(
                    [
                        {
                            "시군구코드": "R",
                            "후보순위": 1,
                            "기관코드": "failed",
                            "직선거리_km": 1.0,
                            "도로거리_km": pd.NA,
                            "경로결과코드": pd.NA,
                            "경로상태": status,
                        },
                        {
                            "시군구코드": "R",
                            "후보순위": 2,
                            "기관코드": "success",
                            "직선거리_km": 2.0,
                            "도로거리_km": 5.0,
                            "경로결과코드": 0,
                            "경로상태": "성공",
                        },
                    ]
                )

                with self.assertRaisesRegex(RuntimeError, "기술 실패"):
                    kakao_routes.assert_global_road_minimum(evaluated, evaluated)

    def test_global_minimum_accepts_official_no_route_result_codes(self) -> None:
        for result_code in sorted(kakao_routes.CONFIRMED_NO_ROUTE_RESULT_CODES):
            with self.subTest(result_code=result_code):
                evaluated = pd.DataFrame(
                    [
                        {
                            "시군구코드": "R",
                            "후보순위": 1,
                            "기관코드": "no-route",
                            "직선거리_km": 1.0,
                            "도로거리_km": pd.NA,
                            "경로결과코드": result_code,
                            "경로상태": f"경로오류:{result_code}",
                        },
                        {
                            "시군구코드": "R",
                            "후보순위": 2,
                            "기관코드": "success",
                            "직선거리_km": 2.0,
                            "도로거리_km": 5.0,
                            "경로결과코드": 0,
                            "경로상태": "성공",
                        },
                    ]
                )

                kakao_routes.assert_global_road_minimum(evaluated, evaluated)

    def test_global_minimum_ignores_uncertain_failure_that_cannot_beat_best(self) -> None:
        evaluated = pd.DataFrame(
            [
                {
                    "시군구코드": "R",
                    "후보순위": 1,
                    "기관코드": "success",
                    "직선거리_km": 1.0,
                    "도로거리_km": 2.0,
                    "경로결과코드": 0,
                    "경로상태": "성공",
                },
                {
                    "시군구코드": "R",
                    "후보순위": 2,
                    "기관코드": "failed",
                    "직선거리_km": 3.0,
                    "도로거리_km": pd.NA,
                    "경로결과코드": pd.NA,
                    "경로상태": "HTTP오류:500",
                },
            ]
        )

        kakao_routes.assert_global_road_minimum(evaluated, evaluated)

    def test_cache_ttl_handles_valid_boundary_expired_and_naive_timestamps(self) -> None:
        reference = datetime(2026, 8, 30, 3, 0, tzinfo=timezone.utc)

        self.assertTrue(
            kakao_routes.is_cache_fresh(reference - timedelta(days=29), 30, reference)
        )
        self.assertTrue(
            kakao_routes.is_cache_fresh(reference - timedelta(days=30), 30, reference)
        )
        self.assertFalse(
            kakao_routes.is_cache_fresh(reference - timedelta(days=30, seconds=1), 30, reference)
        )
        self.assertFalse(kakao_routes.is_cache_fresh("2026-08-30T03:00:00", 30, reference))

    def test_load_route_cache_reuses_only_fresh_successes(self) -> None:
        reference = datetime(2026, 8, 30, 3, 0, tzinfo=timezone.utc)
        rows = []
        for key, collected_at in {
            "fresh": reference - timedelta(days=1),
            "boundary": reference - timedelta(days=30),
            "expired": reference - timedelta(days=30, seconds=1),
        }.items():
            rows.append(
                {
                    "경로요청키": key,
                    **self._route_result(1.0),
                    "수집시각": collected_at.isoformat(),
                }
            )

        with tempfile.TemporaryDirectory() as directory:
            candidate_path = Path(directory) / "candidates.csv"
            hospital_path = Path(directory) / "hospitals.csv"
            pd.DataFrame(rows).to_csv(candidate_path, index=False, encoding="utf-8-sig")
            with (
                patch.object(kakao_routes, "CANDIDATE_OUTPUT", candidate_path),
                patch.object(kakao_routes, "HOSPITAL_OUTPUT", hospital_path),
            ):
                cache = kakao_routes.load_route_cache(False, 30, reference)
                refreshed = kakao_routes.load_route_cache(True, 30, reference)

        self.assertEqual(set(cache), {"fresh", "boundary"})
        self.assertEqual(refreshed, {})

    def test_load_cached_origins_applies_same_ttl(self) -> None:
        reference = datetime(2026, 8, 30, 3, 0, tzinfo=timezone.utc)
        base_origins = pd.DataFrame(
            [
                {"시군구코드": "fresh-region"},
                {"시군구코드": "expired-region"},
            ]
        )
        cached_origins = pd.DataFrame(
            [
                {
                    "시군구코드": "fresh-region",
                    "기준점키": "same-key",
                    "도로탐색상태": "성공",
                    "수집시각": (reference - timedelta(days=1)).isoformat(),
                },
                {
                    "시군구코드": "expired-region",
                    "기준점키": "same-key",
                    "도로탐색상태": "성공",
                    "수집시각": (reference - timedelta(days=31)).isoformat(),
                },
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            origin_path = Path(directory) / "origins.csv"
            cached_origins.to_csv(origin_path, index=False, encoding="utf-8-sig")
            with (
                patch.object(kakao_routes, "ORIGIN_OUTPUT", origin_path),
                patch.object(kakao_routes, "origin_input_key", return_value="same-key"),
            ):
                reused, pending = kakao_routes.load_cached_origins(
                    base_origins,
                    refresh=False,
                    cache_ttl_days=30,
                    reference_time=reference,
                )

        self.assertEqual(set(reused), {"fresh-region"})
        self.assertEqual([row["시군구코드"] for row in pending], ["expired-region"])


if __name__ == "__main__":
    unittest.main()
