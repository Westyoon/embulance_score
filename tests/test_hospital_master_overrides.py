from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import part1_collect_hospital_master as hospital_master


class HospitalRegionOverrideTests(unittest.TestCase):
    @staticmethod
    def base_hospitals() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "기관코드": "A0001",
                    "병원명": "첫번째병원",
                    "시도": "경기도",
                    "시군구": "구주소시",
                },
                {
                    "기관코드": "B0002",
                    "병원명": "두번째병원",
                    "시도": "서울특별시",
                    "시군구": "종로구",
                },
            ]
        )

    def apply(self, frame: pd.DataFrame, overrides: pd.DataFrame) -> pd.DataFrame:
        override_path = Mock()
        override_path.exists.return_value = True
        with (
            patch.object(hospital_master, "REGION_OVERRIDES", override_path),
            patch.object(hospital_master, "read_csv", return_value=overrides),
        ):
            return hospital_master.apply_region_overrides(frame)

    @staticmethod
    def valid_override(**changes) -> dict:
        row = {
            "기관코드": "A0001",
            "병원명": "첫번째병원",
            "원본시도": "경기도",
            "원본시군구": "구주소시",
            "시도": "충청남도",
            "시군구": "천안시",
            "근거URL": "https://example.test/hospitals/A0001",
            "확인일": "2026-08-30",
        }
        row.update(changes)
        return row

    def test_applies_verified_region_and_preserves_other_hospitals(self) -> None:
        frame = self.base_hospitals()
        overrides = pd.DataFrame([
            self.valid_override(
                원본시도=" 경기도 ",
                원본시군구=" 구주소시 ",
                시도=" 충청남도 ",
                시군구=" 천안시 ",
            )
        ])

        result = self.apply(frame, overrides)

        self.assertIs(result, frame)
        corrected = result.set_index("기관코드").loc["A0001"]
        untouched = result.set_index("기관코드").loc["B0002"]
        self.assertEqual((corrected["시도"], corrected["시군구"]), ("충청남도", "천안시"))
        self.assertEqual((untouched["시도"], untouched["시군구"]), ("서울특별시", "종로구"))
        self.assertEqual(corrected["병원명"], "첫번째병원")

    def test_rejects_duplicate_hospital_codes(self) -> None:
        overrides = pd.DataFrame(
            [
                self.valid_override(근거URL="https://example.test/one"),
                self.valid_override(
                    시도="경기도",
                    시군구="수원시",
                    근거URL="https://example.test/two",
                ),
            ]
        )

        with self.assertRaisesRegex(ValueError, "기관코드가 중복"):
            self.apply(self.base_hospitals(), overrides)

    def test_rejects_missing_required_columns(self) -> None:
        override = self.valid_override()
        override.pop("원본시군구")
        overrides = pd.DataFrame([override])

        with self.assertRaisesRegex(ValueError, "필수 컬럼"):
            self.apply(self.base_hospitals(), overrides)

    def test_rejects_blank_or_csv_missing_evidence_values(self) -> None:
        missing_values = ["", "   ", None, float("nan")]
        for column in ("병원명", "원본시도", "원본시군구", "시도", "시군구", "근거URL", "확인일"):
            for missing in missing_values:
                with self.subTest(column=column, missing=missing):
                    override = self.valid_override()
                    override[column] = missing
                    with self.assertRaisesRegex(ValueError, "근거가 불완전"):
                        self.apply(self.base_hospitals(), pd.DataFrame([override]))

    def test_rejects_when_current_nemc_region_differs_from_expected_source(self) -> None:
        override = self.valid_override(원본시군구="수원시")

        with self.assertRaisesRegex(ValueError, "NEMC 원천 지역이 기대값과 달라"):
            self.apply(self.base_hospitals(), pd.DataFrame([override]))

    def test_rejects_override_target_missing_from_nemc_population(self) -> None:
        overrides = pd.DataFrame([
            self.valid_override(
                기관코드="NOT-IN-NEMC",
                병원명="모집단외병원",
                근거URL="https://example.test/hospitals/not-in-nemc",
            )
        ])

        with self.assertRaisesRegex(ValueError, "NEMC 모집단에 없습니다: NOT-IN-NEMC"):
            self.apply(self.base_hospitals(), overrides)


if __name__ == "__main__":
    unittest.main()
