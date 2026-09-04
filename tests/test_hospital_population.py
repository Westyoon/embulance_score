from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from hospital_population import load_population_audit, plan_hospital_population
import part1_collect_hospital_master as hospital_master
import run_pipeline


def hospitals(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "기관코드": code,
                "병원명": f"병원-{code}",
                "시도": "강원특별자치도",
                "시군구": "강릉시" if index < 2 else "원주시",
                "주소": f"주소-{code}",
            }
            for index, code in enumerate(codes)
        ]
    )


class HospitalPopulationReconciliationTests(unittest.TestCase):
    NOW = pd.Timestamp("2026-09-04T10:00:00Z")

    def plan(self, current, previous, audit=None, **changes):
        options = {
            "expected_hospitals": 4,
            "expected_regions": 2,
            "observed_at": self.NOW,
            "max_carry_forward_hospitals": 2,
            "max_consecutive_misses": 3,
            "max_missing_age_hours": 72,
        }
        options.update(changes)
        return plan_hospital_population(current, previous, audit, **options)

    def test_small_removal_only_delta_is_carried_with_audit(self) -> None:
        previous = hospitals(["A", "B", "C", "D"])
        plan = self.plan(hospitals(["A", "B", "C"]), previous)

        self.assertEqual(plan.carried_codes, {"D"})
        self.assertEqual(plan.audit["status"], "transient_omission_reconciled")
        entry = plan.audit["carriedForwardHospitals"][0]
        self.assertEqual(entry["institutionCode"], "D")
        self.assertEqual(entry["consecutiveMisses"], 1)
        self.assertEqual(entry["firstMissingAt"], self.NOW.isoformat())

    def test_recovered_source_clears_previous_carry(self) -> None:
        previous = hospitals(["A", "B", "C", "D"])
        audit = self.plan(hospitals(["A", "B", "C"]), previous).audit

        plan = self.plan(previous.copy(), previous, audit)

        self.assertTrue(plan.carried.empty)
        self.assertEqual(plan.audit["status"], "source_exact")
        self.assertEqual(plan.audit["carriedForwardHospitals"], [])

    def test_sustained_missing_hospital_is_not_carried_forever(self) -> None:
        previous = hospitals(["A", "B", "C", "D"])
        audit = None
        for hour in (0, 24, 48):
            plan = self.plan(
                hospitals(["A", "B", "C"]),
                previous,
                audit,
                observed_at=self.NOW + pd.Timedelta(hours=hour),
            )
            audit = plan.audit

        with self.assertRaisesRegex(RuntimeError, "임시 승계 기한을 초과"):
            self.plan(
                hospitals(["A", "B", "C"]),
                previous,
                audit,
                observed_at=self.NOW + pd.Timedelta(hours=72),
            )

    def test_addition_or_replacement_requires_review(self) -> None:
        previous = hospitals(["A", "B", "C", "D"])
        with self.assertRaisesRegex(RuntimeError, "신규/교체 기관"):
            self.plan(hospitals(["A", "B", "C", "E"]), previous)

    def test_large_source_drop_is_rejected(self) -> None:
        previous = hospitals(["A", "B", "C", "D"])
        with self.assertRaisesRegex(RuntimeError, "안전한 임시 승계 범위"):
            self.plan(hospitals(["A"]), previous)

    def test_invalid_previous_audit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.json"
            path.write_text(json.dumps({"schemaVersion": 2}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "형식이 올바르지 않습니다"):
                load_population_audit(path)

    def test_full_refresh_audit_uses_persistent_state_not_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            staged_data = root / "staging" / "data"
            staged_boundary = root / "staging" / "koreaGeo.json"
            with patch.object(run_pipeline, "PIPELINE_STATE_DIR", state):
                environment = run_pipeline.build_pipeline_environment(
                    staged_data,
                    staged_boundary,
                    None,
                )
            with patch.dict("os.environ", environment, clear=True):
                audit_path = hospital_master.population_audit_path()

            self.assertEqual(audit_path, state.resolve() / "hospital_population_audit.json")
            self.assertFalse(audit_path.is_relative_to(staged_data.resolve()))


if __name__ == "__main__":
    unittest.main()
