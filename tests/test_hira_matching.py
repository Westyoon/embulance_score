from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import part3_collect_hira_doctors as hira


def target(
    code: str,
    *,
    name: str = "새봄중앙병원",
    address: str = "서울특별시 강남구 테헤란로 10",
    phone: str = "02-123-4567",
    latitude: float = 37.5000,
    longitude: float = 127.0000,
    province: str = "서울특별시",
    district: str = "강남구",
) -> dict:
    return {
        "기관코드": code,
        "병원명": name,
        "주소": address,
        "전화": phone,
        "위도": latitude,
        "경도": longitude,
        "시도": province,
        "시군구": district,
    }


def candidate(
    identifier: str,
    *,
    name: str = "의료법인 새봄중앙병원",
    address: str = "서울특별시 강남구 테헤란로 10",
    phone: str = "02-123-4567",
    latitude: str = "37.5000",
    longitude: str = "127.0000",
) -> dict:
    return {
        "ykiho": identifier,
        "yadmNm": name,
        "addr": address,
        "telno": phone,
        "YPos": latitude,
        "XPos": longitude,
    }


class HiraNameNormalizationTests(unittest.TestCase):
    def test_normalize_name_keeps_existing_legal_entity_and_suffix_behavior(self) -> None:
        self.assertEqual(
            hira.normalize_name("의료법인 가톨릭대학교 서울성모병원 (반포동)"),
            "가톨릭서울성모",
        )
        self.assertEqual(hira.normalize_name(" ABC-서울 병원（분원） "), "abc서울")
        self.assertEqual(hira.normalize_name(None), "")


class HiraCandidateScoreTests(unittest.TestCase):
    def test_score_is_pure_bounded_and_rewards_all_identity_signals(self) -> None:
        hospital = target("N001")
        exact = candidate("H001")
        unrelated = candidate(
            "H999",
            name="푸른대학교병원",
            address="부산광역시 해운대구 해운대로 999",
            phone="051-999-9999",
            latitude="35.1600",
            longitude="129.1600",
        )
        original_hospital = deepcopy(hospital)
        original_exact = deepcopy(exact)

        exact_score = hira.score_candidate(hospital, exact)
        unrelated_score = hira.score_candidate(hospital, unrelated)

        self.assertGreater(exact_score, unrelated_score)
        self.assertGreaterEqual(exact_score, 0.0)
        self.assertLessEqual(exact_score, 1.0)
        self.assertGreaterEqual(unrelated_score, 0.0)
        self.assertLessEqual(unrelated_score, 1.0)
        self.assertEqual(hospital, original_hospital)
        self.assertEqual(exact, original_exact)

    def test_address_phone_and_coordinates_each_improve_the_score(self) -> None:
        hospital = target("N001", name="새봄중앙응급병원")
        baseline = candidate(
            "BASE",
            name="새봄중앙의료원",
            address="",
            phone="",
            latitude="",
            longitude="",
        )

        address_match = {**baseline, "ykiho": "ADDRESS", "addr": hospital["주소"]}
        phone_match = {**baseline, "ykiho": "PHONE", "telno": "02 123 4567"}
        coordinate_match = {
            **baseline,
            "ykiho": "COORDINATE",
            "YPos": "37.5001",
            "XPos": "127.0001",
        }
        baseline_score = hira.score_candidate(hospital, baseline)

        with self.subTest(signal="address"):
            self.assertGreater(hira.score_candidate(hospital, address_match), baseline_score)
        with self.subTest(signal="phone"):
            self.assertGreater(hira.score_candidate(hospital, phone_match), baseline_score)
        with self.subTest(signal="coordinates"):
            self.assertGreater(hira.score_candidate(hospital, coordinate_match), baseline_score)

    def test_same_name_in_the_correct_district_beats_wrong_region(self) -> None:
        hospital = target("N001")
        local = candidate("LOCAL")
        remote = candidate(
            "REMOTE",
            address="부산광역시 강서구 테헤란로 10",
            phone="",
            latitude="35.1700",
            longitude="129.0700",
        )

        self.assertGreater(
            hira.score_candidate(hospital, local),
            hira.score_candidate(hospital, remote),
        )

    def test_district_matching_does_not_use_partial_city_names(self) -> None:
        hospital = target(
            "N001",
            province="경기도",
            district="양주시",
            address="경기도 양주시 평화로 10",
        )
        namyangju = candidate(
            "REMOTE",
            address="경기도 남양주시 평화로 10",
        )
        namyangju["sgguCdNm"] = "남양주시"
        yangju = candidate(
            "LOCAL",
            address="경기도 양주시 평화로 10",
        )
        yangju["sgguCdNm"] = "양주시"

        self.assertFalse(hira._district_matches(hospital, namyangju))
        self.assertTrue(hira._district_matches(hospital, yangju))


class HiraUniqueAssignmentTests(unittest.TestCase):
    def test_default_policy_auto_matches_a_strong_identity(self) -> None:
        matches = hira.assign_unique_matches([target("N001")], [candidate("H001")])

        self.assertIn("N001", matches)
        selected, score, method = matches["N001"]
        self.assertEqual(selected["ykiho"], "H001")
        self.assertGreaterEqual(score, hira.AUTO_MATCH_THRESHOLD)
        self.assertEqual(method, "자동매칭")

    def test_assigns_same_named_hospitals_by_address_phone_and_coordinates(self) -> None:
        hospitals = [
            target(
                "N001",
                name="새봄병원",
                address="서울특별시 강남구 테헤란로 10",
                phone="02-111-1111",
                latitude=37.5000,
                longitude=127.0000,
            ),
            target(
                "N002",
                name="새봄병원",
                address="서울특별시 강남구 역삼로 20",
                phone="02-222-2222",
                latitude=37.5100,
                longitude=127.0100,
            ),
        ]
        candidates = [
            candidate(
                "H002",
                name="의료법인 새봄병원",
                address="서울특별시 강남구 역삼로 20",
                phone="02-222-2222",
                latitude="37.5101",
                longitude="127.0101",
            ),
            candidate(
                "H001",
                name="의료법인 새봄병원",
                address="서울특별시 강남구 테헤란로 10",
                phone="02-111-1111",
                latitude="37.5001",
                longitude="127.0001",
            ),
        ]

        matches = hira.assign_unique_matches(hospitals, candidates, threshold=0.0, margin=0.0)

        self.assertEqual(set(matches), {"N001", "N002"})
        self.assertEqual(matches["N001"][0]["ykiho"], "H001")
        self.assertEqual(matches["N002"][0]["ykiho"], "H002")
        self.assertTrue(all(match[2] == "자동매칭" for match in matches.values()))
        assigned_identifiers = [match[0]["ykiho"] for match in matches.values()]
        self.assertEqual(len(assigned_identifiers), len(set(assigned_identifiers)))

    def test_never_assigns_one_hira_identifier_to_two_nemc_hospitals(self) -> None:
        best = target("N001")
        weaker = target(
            "N002",
            address="서울특별시 강남구 다른로 20",
            phone="02-999-9999",
            latitude=37.6000,
            longitude=127.1000,
        )
        duplicated_candidates = [candidate("SHARED"), candidate("SHARED")]

        matches = hira.assign_unique_matches(
            [weaker, best],
            duplicated_candidates,
            threshold=0.0,
            margin=0.0,
        )

        self.assertIn("N001", matches)
        self.assertNotIn("N002", matches)
        self.assertEqual(matches["N001"][0]["ykiho"], "SHARED")

    def test_defers_ambiguous_candidates_within_the_margin(self) -> None:
        hospital = target("N001")
        candidates = [candidate("H001"), candidate("H002")]

        matches = hira.assign_unique_matches(
            [hospital],
            candidates,
            threshold=0.0,
            margin=0.01,
        )

        self.assertNotIn("N001", matches)

    def test_rejects_a_candidate_below_the_requested_threshold(self) -> None:
        unrelated = candidate(
            "H999",
            name="푸른의원",
            address="부산광역시 해운대구 해운대로 999",
            phone="051-999-9999",
            latitude="35.1600",
            longitude="129.1600",
        )

        matches = hira.assign_unique_matches(
            [target("N001")],
            [unrelated],
            threshold=0.99,
            margin=0.0,
        )

        self.assertNotIn("N001", matches)

    def test_waegwan_road_address_and_coordinates_beat_similar_clinic_name(self) -> None:
        hospital = target(
            "A2700066",
            name="의료법인왜관병원",
            address="경상북도 칠곡군 왜관읍 군청2길 10",
            phone="054-971-1004",
            latitude=35.99606388888889,
            longitude=128.4015222222222,
            province="경상북도",
            district="칠곡군",
        )
        similarly_named_clinic = candidate(
            "HIRA-WAEGWAN-CLINIC",
            name="왜관한의원",
            address="경상북도 칠곡군 왜관읍 중앙로 237, 1, 2층",
            phone="054-975-8875",
            latitude="35.9910",
            longitude="128.3980",
        )
        same_site_hospital = candidate(
            "HIRA-WAEGWAN-HOSPITAL",
            name="의료법인건용의료재단 왜관병원",
            address="경상북도 칠곡군 왜관읍 군청2길 10, (왜관읍)",
            phone="054-971-1002",
            latitude="35.996064",
            longitude="128.401522",
        )

        matches = hira.assign_unique_matches(
            [hospital],
            [similarly_named_clinic, same_site_hospital],
        )

        self.assertGreater(
            hira.score_candidate(hospital, same_site_hospital),
            hira.score_candidate(hospital, similarly_named_clinic),
        )
        self.assertIn("A2700066", matches)
        self.assertEqual(matches["A2700066"][0]["ykiho"], "HIRA-WAEGWAN-HOSPITAL")
        self.assertEqual(matches["A2700066"][2], "자동매칭")

    def test_onjae_address_and_nearby_coordinates_overcome_legal_name_change(self) -> None:
        hospital = target(
            "A2200046",
            name="의료법인온세움의료재단온재병원",
            address="강원특별자치도 속초시 중앙로 11 (교동)",
            phone="033-639-8988",
            latitude=38.19802124533656,
            longitude=128.57838660480326,
            province="강원특별자치도",
            district="속초시",
        )
        renamed_candidate = candidate(
            "HIRA-ONJAE",
            name="온재병원",
            address="강원특별자치도 속초시 중앙로 11-0, 속초보광병원",
            phone="033-639-8500",
            latitude="38.198020",
            longitude="128.578390",
        )

        matches = hira.assign_unique_matches([hospital], [renamed_candidate])

        self.assertIn("A2200046", matches)
        selected, score, method = matches["A2200046"]
        self.assertEqual(selected["ykiho"], "HIRA-ONJAE")
        self.assertGreaterEqual(score, hira.AUTO_MATCH_THRESHOLD)
        self.assertEqual(method, "자동매칭")

    def test_global_assignment_recovers_two_matches_that_greedy_would_drop(self) -> None:
        hospitals = [target("A"), target("B")]
        candidates = [candidate("H1"), candidate("H2")]
        scores = {
            ("A", "H1"): 0.91,
            ("A", "H2"): 0.85,
            ("B", "H1"): 0.90,
            ("B", "H2"): 0.10,
        }

        def fixed_score(hospital: dict, hira_candidate: dict) -> float:
            return scores[(hospital["기관코드"], hira_candidate["ykiho"])]

        with patch.object(hira, "score_candidate", side_effect=fixed_score):
            matches = hira.assign_unique_matches(
                hospitals,
                candidates,
                threshold=0.84,
                margin=0.05,
            )

        self.assertEqual(set(matches), {"A", "B"})
        self.assertEqual(matches["A"][0]["ykiho"], "H2")
        self.assertEqual(matches["B"][0]["ykiho"], "H1")
        self.assertAlmostEqual(matches["A"][1], 0.85)
        self.assertAlmostEqual(matches["B"][1], 0.90)


class LegacyChooseMatchCompatibilityTests(unittest.TestCase):
    def test_choose_match_still_returns_the_existing_tuple_contract(self) -> None:
        local = candidate("H001")

        selected, score, method = hira.choose_match("새봄중앙병원", "강남구", [local])

        self.assertIs(selected, local)
        self.assertIsInstance(score, float)
        self.assertEqual(method, "자동매칭")

    def test_choose_match_still_rejects_candidates_from_another_district(self) -> None:
        remote = candidate("H001", address="부산광역시 해운대구 해운대로 999")

        selected, _, method = hira.choose_match("새봄중앙병원", "강남구", [remote])

        self.assertIsNone(selected)
        self.assertEqual(method, "지역불일치")


class HiraOverrideValidationTests(unittest.TestCase):
    @staticmethod
    def valid_override(**changes) -> dict:
        row = {
            "기관코드": "A2700066",
            "HIRA병원명": "의료법인건용의료재단 왜관병원",
            "HIRA주소": "경상북도 칠곡군 왜관읍 군청2길 10",
            "암호화요양기호": "HIRA-WAEGWAN-HOSPITAL",
            "응급의학과전문의수": 1,
            "근거URL": "https://www.hira.or.kr/ra/hosp/example",
            "확인일": "2026-08-31",
        }
        row.update(changes)
        return row

    def test_accepts_complete_evidence_backed_override(self) -> None:
        result = hira.validate_overrides(pd.DataFrame([self.valid_override()]))

        self.assertIsNone(result)

    def test_requires_evidence_url_confirmation_date_and_identifier(self) -> None:
        for column in ("근거URL", "확인일", "암호화요양기호"):
            for missing in ("", "   ", None, pd.NA):
                with self.subTest(column=column, missing=missing):
                    invalid = self.valid_override(**{column: missing})
                    with self.assertRaises(ValueError):
                        hira.validate_overrides(pd.DataFrame([invalid]))

    def test_rejects_missing_evidence_columns(self) -> None:
        invalid = self.valid_override()
        invalid.pop("근거URL")

        with self.assertRaises(ValueError):
            hira.validate_overrides(pd.DataFrame([invalid]))

    def test_rejects_one_hira_identifier_assigned_to_two_institutions(self) -> None:
        overrides = pd.DataFrame(
            [
                self.valid_override(),
                self.valid_override(
                    기관코드="A2200046",
                    HIRA병원명="온재병원",
                    HIRA주소="강원특별자치도 속초시 중앙로 11",
                    근거URL="https://www.hira.or.kr/ra/hosp/onjae",
                ),
            ]
        )

        with self.assertRaises(ValueError):
            hira.validate_overrides(overrides)

    def test_rejects_manual_identifier_missing_from_latest_catalog(self) -> None:
        override = self.valid_override(
            근거URL=(
                "https://www.hira.or.kr/ra/hosp/hospInfoAjax.do"
                "?ykiho=HIRA-WAEGWAN-HOSPITAL"
            )
        )

        with self.assertRaises(ValueError):
            hira.validate_overrides_against_catalog(pd.DataFrame([override]), [])


class HiraExclusionValidationTests(unittest.TestCase):
    @staticmethod
    def valid_exclusion(**changes) -> dict:
        identifier = "A" * 80
        row = {
            "기관코드": "TEST001",
            "병원명": "강남힐병원",
            "사유코드": "HIRA_SOURCE_NOT_FOUND",
            "사유": "HIRA전체목록·전문의API·상세페이지미제공",
            "확인요양기호": identifier,
            "근거URL": f"https://www.hira.or.kr/ra/hosp/hospInfoAjax.do?ykiho={identifier}",
            "확인일": date.today().strftime("%Y-%m-%d"),
        }
        row.update(changes)
        return row

    def test_accepts_current_official_source_evidence(self) -> None:
        result = hira.validate_exclusions(pd.DataFrame([self.valid_exclusion()]))

        self.assertIsNone(result)

    def test_rejects_future_stale_or_non_hira_evidence(self) -> None:
        invalid_rows = [
            self.valid_exclusion(확인일=(date.today() + timedelta(days=1)).isoformat()),
            self.valid_exclusion(확인일=(date.today() - timedelta(days=31)).isoformat()),
            self.valid_exclusion(
                근거URL=f"https://example.com/detail?ykiho={'A' * 80}"
            ),
        ]
        for invalid in invalid_rows:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    hira.validate_exclusions(pd.DataFrame([invalid]))

    def test_rejects_duplicate_confirmed_identifier(self) -> None:
        duplicate = pd.DataFrame(
            [
                self.valid_exclusion(),
                self.valid_exclusion(기관코드="TEST002", 병원명="두번째병원"),
            ]
        )

        with self.assertRaises(ValueError):
            hira.validate_exclusions(duplicate)

    def test_rejects_hospital_name_that_changed_in_nemc_master(self) -> None:
        master = pd.DataFrame([target("TEST001", name="변경된병원명")])

        with patch.object(hira, "OVERRIDES", ROOT / "missing-test-overrides.csv"):
            with self.assertRaises(ValueError):
                hira.validate_exclusions_against_master(
                    pd.DataFrame([self.valid_exclusion()]),
                    master,
                )

    def test_rejects_reappearing_identifier_or_new_valid_candidate(self) -> None:
        exclusion = pd.DataFrame([self.valid_exclusion()])
        removed = candidate("A" * 80)
        current_target = target("TEST001", name="강남힐병원")
        replacement = candidate(
            "HIRA-REPLACEMENT",
            name="강남힐병원",
            address=current_target["주소"],
            phone=current_target["전화"],
        )

        with self.assertRaises(RuntimeError):
            hira.validate_exclusions_against_catalog(
                exclusion,
                [removed],
                {"TEST001": []},
                {"TEST001": current_target},
            )
        with self.assertRaises(RuntimeError):
            hira.validate_exclusions_against_catalog(
                exclusion,
                [],
                {"TEST001": [(1.0, replacement)]},
                {"TEST001": current_target},
            )

    def test_apply_exclusion_clears_hira_values_and_sets_explicit_state(self) -> None:
        detail = pd.DataFrame(
            [
                {
                    **target("TEST001", name="강남힐병원"),
                    "HIRA병원명": "잘못된후보병원",
                    "HIRA주소": "서울특별시 관악구",
                    "암호화요양기호": "WRONG",
                    "매칭점수": 0.4,
                    "매칭상태": "낮은유사도",
                    "응급의학과전문의수": 3,
                }
            ]
        )

        result = hira.apply_exclusions(
            detail,
            pd.DataFrame([self.valid_exclusion()]),
        ).iloc[0]

        self.assertEqual(result["매칭상태"], "HIRA원천불일치")
        for column in ("HIRA병원명", "HIRA주소", "암호화요양기호", "응급의학과전문의수"):
            self.assertTrue(pd.isna(result[column]))

    def test_low_score_shared_identifier_is_not_mislabeled_as_collision(self) -> None:
        current_target = target("TEST001", name="강남힐병원")
        low_score_candidate = candidate(
            "SHARED-ID",
            name="강남고려병원",
            address="서울특별시 관악구 관악로 1",
        )

        score, status = hira.classify_unassigned_ranking(
            current_target,
            [(0.4, low_score_candidate)],
            {"SHARED-ID"},
        )

        self.assertEqual(score, 0.4)
        self.assertEqual(status, "낮은유사도")


if __name__ == "__main__":
    unittest.main()
