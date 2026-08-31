import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from build_missingness_report import build_missingness_report, write_missingness_report


class MissingnessReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        pd.DataFrame(
            [
                ["A1", "무응답", "센터", "서울", "가구", None, None, None, "결측", None, "2026-08-31T20:00:00+09:00"],
                ["A2", "만료", "센터", "서울", "나구", None, None, None, "결측", 20260830070000, "2026-08-31T20:00:00+09:00"],
                ["A3", "총병상누락", "센터", "서울", "다구", 2, None, None, "결측", 20260831190000, "2026-08-31T20:00:00+09:00"],
                ["A4", "음수가용", "센터", "서울", "라구", -1, 10, None, "결측", 20260831190000, "2026-08-31T20:00:00+09:00"],
                ["A5", "정상", "센터", "서울", "마구", 2, 10, 80, "포화", 20260831190000, "2026-08-31T20:00:00+09:00"],
            ],
            columns=["기관코드", "병원명", "등급", "시도", "시군구", "가용병상", "전체병상", "포화율", "상태", "API기준시각", "수집시각"],
        ).to_csv(self.data_dir / "bed_status.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(
            [
                ["서울|가구", "가구", None, 0, 10, None, 20, 2, None, None, None, "원천데이터부족"],
                ["서울|마구", "마구", 80, 1, 10, 20, 30, 4, 44, 3, "보통", "완료"],
            ],
            columns=["시군구코드", "시군구명", "병상포화도점수", "병상데이터기관수", "접근성점수", "인구대비병상점수", "의료진부족점수", "완성항목수", "regionRisk", "위험등급", "위험등급명", "산출상태"],
        ).to_csv(self.data_dir / "region_risk_final.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(
            [
                ["A5", "정상", "주소", "전화", 37, 127, "서울", "마구", "정상", "주소", "HIRA1", 1.0, "자동매칭", 3],
                ["A6", "후보", "주소", "전화", 37, 127, "서울", "바구", None, None, None, 0.9, "후보모호", None],
            ],
            columns=["기관코드", "병원명", "주소", "전화", "위도", "경도", "시도", "시군구", "HIRA병원명", "HIRA주소", "암호화요양기호", "매칭점수", "매칭상태", "응급의학과전문의수"],
        ).to_csv(self.data_dir / "hira_doctor_matches.csv", index=False, encoding="utf-8-sig")
        (self.data_dir / "hira_catalog_manifest.json").write_text(
            json.dumps({"collected_at_utc": "2026-08-31T11:00:00+00:00"}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_classifies_distinct_bed_causes_and_hira_gap(self) -> None:
        report, summary = build_missingness_report(self.data_dir)
        self.assertEqual(
            summary["reason_counts"],
            {
                "BED_API_NO_RESPONSE": 1,
                "BED_AVAILABLE_NEGATIVE": 1,
                "BED_SOURCE_STALE": 1,
                "BED_TOTAL_MISSING": 1,
                "HIRA_MATCH_DEFERRED": 1,
            },
        )
        self.assertEqual(summary["entity_counts"], {"hospital_bed": 4, "hospital_hira": 1, "region": 1})
        region = report.loc[report["entity_type"].eq("region")].iloc[0]
        self.assertIn("BED_API_NO_RESPONSE 1건", region["reason"])
        self.assertEqual(region["priority"], "P0")

    def test_writes_outputs_without_mutating_sources(self) -> None:
        before = (self.data_dir / "bed_status.csv").read_bytes()
        write_missingness_report(self.data_dir)
        self.assertEqual(before, (self.data_dir / "bed_status.csv").read_bytes())
        saved = pd.read_csv(self.data_dir / "missingness_followup.csv")
        summary = json.loads((self.data_dir / "missingness_followup_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(len(saved), 6)
        self.assertEqual(summary["total_open_items"], 6)


if __name__ == "__main__":
    unittest.main()
