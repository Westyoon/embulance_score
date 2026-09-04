import json
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class HospitalPopulationPlan:
    carried: pd.DataFrame
    audit: dict[str, object]

    @property
    def carried_codes(self) -> set[str]:
        if self.carried.empty:
            return set()
        return set(self.carried["기관코드"].astype("string").str.strip())


def load_population_audit(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"NEMC 모집단 감사 파일을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise RuntimeError(f"NEMC 모집단 감사 파일 형식이 올바르지 않습니다: {path}")
    carried = value.get("carriedForwardHospitals", [])
    if not isinstance(carried, list) or any(not isinstance(item, dict) for item in carried):
        raise RuntimeError(f"NEMC 모집단 감사 파일 형식이 올바르지 않습니다: {path}")
    return value


def _normalized_codes(frame: pd.DataFrame, label: str) -> pd.Series:
    if "기관코드" not in frame.columns:
        raise RuntimeError(f"{label}에 기관코드 컬럼이 없습니다.")
    codes = frame["기관코드"].astype("string").str.strip()
    if codes.isna().any() or codes.eq("").any() or codes.duplicated().any():
        raise RuntimeError(f"{label}의 기관코드가 비었거나 중복되었습니다.")
    return codes


def _utc_timestamp(value: object | None) -> pd.Timestamp:
    timestamp = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def plan_hospital_population(
    current: pd.DataFrame,
    previous: pd.DataFrame | None,
    previous_audit: dict[str, object] | None,
    *,
    expected_hospitals: int,
    expected_regions: int,
    observed_at: object | None = None,
    max_carry_forward_hospitals: int = 3,
    max_consecutive_misses: int = 3,
    max_missing_age_hours: float = 72.0,
) -> HospitalPopulationPlan:
    """Plan a tightly bounded fallback for transient NEMC list omissions.

    A missing institution is not proof that its emergency designation ended.
    Small removal-only deltas can therefore reuse the last validated master row
    temporarily. Additions/replacements and sustained omissions still require a
    reviewed population change instead of silently changing the analysis cohort.
    """
    if expected_hospitals <= 0 or expected_regions <= 0:
        raise ValueError("검토 기준 모집단은 양수여야 합니다.")
    if max_carry_forward_hospitals < 0 or max_consecutive_misses < 1:
        raise ValueError("모집단 임시 승계 한도가 올바르지 않습니다.")
    if not math.isfinite(max_missing_age_hours) or max_missing_age_hours <= 0:
        raise ValueError("모집단 임시 승계 시간은 양수여야 합니다.")

    now = _utc_timestamp(observed_at)
    current = current.copy()
    current["기관코드"] = _normalized_codes(current, "신규 NEMC 마스터")
    source_regions = (
        len(current[["시도", "시군구"]].drop_duplicates())
        if {"시도", "시군구"}.issubset(current.columns)
        else None
    )

    empty_carried = current.iloc[0:0].copy()
    base_audit: dict[str, object] = {
        "schemaVersion": 1,
        "observedAt": now.isoformat(),
        "sourceHospitals": len(current),
        "sourceRegions": source_regions,
        "expectedHospitals": expected_hospitals,
        "expectedRegions": expected_regions,
    }

    if previous is None:
        if len(current) != expected_hospitals:
            raise RuntimeError(
                "NEMC 모집단이 검토 기준보다 작지만 승계할 이전 검증 마스터가 없습니다: "
                f"hospitals={len(current)}, expected={expected_hospitals}"
            )
        return HospitalPopulationPlan(
            carried=empty_carried,
            audit={**base_audit, "status": "source_exact", "carriedForwardHospitals": []},
        )

    previous = previous.copy()
    previous["기관코드"] = _normalized_codes(previous, "이전 검증 NEMC 마스터")
    previous_regions = (
        len(previous[["시도", "시군구"]].drop_duplicates())
        if {"시도", "시군구"}.issubset(previous.columns)
        else None
    )
    if len(previous) != expected_hospitals or previous_regions != expected_regions:
        raise RuntimeError(
            "임시 승계에 사용할 이전 NEMC 마스터가 검토 기준과 다릅니다: "
            f"hospitals={len(previous)}, regions={previous_regions}"
        )

    current_codes = set(current["기관코드"])
    previous_codes = set(previous["기관코드"])
    added = sorted(current_codes - previous_codes)
    missing = sorted(previous_codes - current_codes)
    if added:
        raise RuntimeError(
            "NEMC 신규/교체 기관은 검토 없이 자동 반영하지 않습니다: "
            f"added_hospitals={added}, removed_hospitals={missing}"
        )
    if not missing:
        if len(current) != expected_hospitals:
            raise RuntimeError(
                "NEMC 모집단 수가 검토 기준과 다릅니다: "
                f"hospitals={len(current)}, expected={expected_hospitals}"
            )
        return HospitalPopulationPlan(
            carried=empty_carried,
            audit={**base_audit, "status": "source_exact", "carriedForwardHospitals": []},
        )

    if len(current) >= expected_hospitals or len(missing) > max_carry_forward_hospitals:
        raise RuntimeError(
            "NEMC 누락 기관이 안전한 임시 승계 범위를 벗어났습니다: "
            f"source_hospitals={len(current)}, missing_hospitals={missing}, "
            f"max_carry_forward={max_carry_forward_hospitals}"
        )

    prior_entries = {}
    if previous_audit:
        entries = previous_audit.get("carriedForwardHospitals", [])
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            raise RuntimeError("이전 NEMC 모집단 감사 내역이 올바르지 않습니다.")
        prior_entries = {
            str(item.get("institutionCode", "")).strip(): item
            for item in entries
            if str(item.get("institutionCode", "")).strip()
        }

    previous_by_code = previous.set_index("기관코드", drop=False)
    carried_entries: list[dict[str, object]] = []
    for code in missing:
        prior = prior_entries.get(code)
        if prior is None:
            first_missing_at = now
            consecutive_misses = 1
        else:
            try:
                first_missing_at = _utc_timestamp(prior["firstMissingAt"])
                consecutive_misses = int(prior["consecutiveMisses"]) + 1
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"NEMC 모집단 감사 내역이 올바르지 않습니다: {code}") from exc
            if first_missing_at > now + pd.Timedelta(minutes=5):
                raise RuntimeError(f"NEMC 모집단 최초 누락 시각이 미래입니다: {code}")

        missing_age = now - first_missing_at
        if consecutive_misses > max_consecutive_misses or missing_age > pd.Timedelta(
            hours=max_missing_age_hours
        ):
            raise RuntimeError(
                "NEMC 기관 누락이 임시 승계 기한을 초과해 모집단 검토가 필요합니다: "
                f"institution_code={code}, consecutive_misses={consecutive_misses}, "
                f"first_missing_at={first_missing_at.isoformat()}"
            )
        row = previous_by_code.loc[code]
        carried_entries.append(
            {
                "institutionCode": code,
                "hospitalName": str(row.get("병원명", "")).strip(),
                "firstMissingAt": first_missing_at.isoformat(),
                "lastMissingAt": now.isoformat(),
                "consecutiveMisses": consecutive_misses,
            }
        )

    carried = previous[previous["기관코드"].isin(missing)].copy()
    carried = carried.reindex(columns=current.columns)
    audit = {
        **base_audit,
        "status": "transient_omission_reconciled",
        "carriedForwardHospitals": carried_entries,
    }
    return HospitalPopulationPlan(carried=carried, audit=audit)
