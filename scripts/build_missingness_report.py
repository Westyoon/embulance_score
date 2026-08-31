from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path

import pandas as pd

from common import DATA_DIR, read_csv, save_csv, save_json
from part2_collect_bed_status import fresh_bed_source_at_collection_mask


MATCHED_HIRA_STATES = {"자동매칭", "수동검증"}
SCORE_COLUMNS = [
    "병상포화도점수",
    "접근성점수",
    "인구대비병상점수",
    "의료진부족점수",
]
OUTPUT_COLUMNS = [
    "entity_type",
    "entity_id",
    "region_code",
    "name",
    "missing_fields",
    "reason_code",
    "reason",
    "priority",
    "status",
    "next_action",
    "source_checked_at",
]
REASON_LABELS = {
    "BED_API_NO_RESPONSE": "NEMC 병상 API 응답 없음",
    "BED_SOURCE_STALE": "병원 API기준시각이 수집시각보다 12시간 초과",
    "BED_TOTAL_MISSING": "전체병상 누락 또는 0 이하",
    "BED_AVAILABLE_MISSING": "가용병상 누락",
    "BED_AVAILABLE_NEGATIVE": "가용병상 음수 이상값",
    "BED_SATURATION_MISSING": "병상 원천값은 있으나 포화율 미산출",
    "HIRA_MATCH_DEFERRED": "HIRA 후보를 1:1로 확정할 근거 부족",
    "REGION_COMPONENT_MISSING": "최종 위험도 구성점수 결측",
}
NEXT_ACTIONS = {
    "BED_API_NO_RESPONSE": "NEMC 운영 쿼터·응답 상태 확인 후 beds 재수집",
    "BED_SOURCE_STALE": "NEMC 원천 API기준시각 갱신 여부 확인 후 beds 재수집",
    "BED_TOTAL_MISSING": "NEMC 전체병상 원천값 재조회 및 기관 확인",
    "BED_AVAILABLE_MISSING": "NEMC 가용병상 원천값 재조회 및 기관 확인",
    "BED_AVAILABLE_NEGATIVE": "NEMC 음수 가용병상 재조회 및 이상값 확인",
    "BED_SATURATION_MISSING": "병상 계산식과 원천 필드를 재검증",
    "HIRA_MATCH_DEFERRED": "HIRA 공식 상세에서 이름·주소·전화·요양기호 수동검증",
    "REGION_COMPONENT_MISSING": "소속 병원 결측 해소 후 beds → full 순서로 재실행",
}


def _non_empty(values: pd.Series) -> pd.Series:
    text = values.astype("string").str.strip()
    return text.notna() & text.ne("")


def _region_code(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["시도"].astype("string").fillna("").str.strip()
        + "|"
        + frame["시군구"].astype("string").fillna("").str.strip()
    )


def classify_bed_issue(row: pd.Series) -> tuple[str, str] | None:
    if not bool(row["source_observed"]):
        return "BED_API_NO_RESPONSE", "가용병상|전체병상|포화율"
    if not bool(row["source_fresh"]):
        return "BED_SOURCE_STALE", "가용병상|전체병상|포화율"
    if pd.isna(row["total"]) or float(row["total"]) <= 0:
        return "BED_TOTAL_MISSING", "전체병상|포화율"
    if pd.isna(row["available"]):
        return "BED_AVAILABLE_MISSING", "가용병상|포화율"
    if float(row["available"]) < 0:
        return "BED_AVAILABLE_NEGATIVE", "가용병상|포화율"
    if pd.isna(row["saturation"]):
        return "BED_SATURATION_MISSING", "포화율"
    return None


def _checked_at_max(values: pd.Series) -> str:
    parsed = pd.to_datetime(values, errors="coerce", utc=True).dropna()
    if parsed.empty:
        return ""
    return parsed.max().isoformat()


def build_missingness_report(data_dir: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    data_dir = Path(data_dir)
    beds = read_csv(data_dir / "bed_status.csv").copy()
    risks = read_csv(data_dir / "region_risk_final.csv").copy()
    hira = read_csv(data_dir / "hira_doctor_matches.csv").copy()

    beds["region_code"] = _region_code(beds)
    beds["available"] = pd.to_numeric(beds["가용병상"], errors="coerce")
    beds["total"] = pd.to_numeric(beds["전체병상"], errors="coerce")
    beds["saturation"] = pd.to_numeric(beds["포화율"], errors="coerce")
    beds["source_observed"] = _non_empty(beds["API기준시각"])
    beds["source_fresh"] = fresh_bed_source_at_collection_mask(
        beds["API기준시각"], beds["수집시각"]
    )

    rows: list[dict[str, str]] = []
    hospital_reasons_by_region: dict[str, Counter[str]] = defaultdict(Counter)
    for _, bed in beds.iterrows():
        issue = classify_bed_issue(bed)
        if issue is None:
            continue
        reason_code, missing_fields = issue
        region_code = str(bed["region_code"])
        hospital_reasons_by_region[region_code][reason_code] += 1
        rows.append(
            {
                "entity_type": "hospital_bed",
                "entity_id": str(bed["기관코드"]),
                "region_code": region_code,
                "name": str(bed["병원명"]),
                "missing_fields": missing_fields,
                "reason_code": reason_code,
                "reason": REASON_LABELS[reason_code],
                "priority": "P1",
                "status": "open",
                "next_action": NEXT_ACTIONS[reason_code],
                "source_checked_at": "" if pd.isna(bed["수집시각"]) else str(bed["수집시각"]),
            }
        )

    risk_checked_at = beds.groupby("region_code")["수집시각"].apply(_checked_at_max).to_dict()
    component_counts: Counter[str] = Counter()
    for _, risk in risks.iterrows():
        missing = [column for column in SCORE_COLUMNS if pd.isna(risk.get(column))]
        if not missing and not pd.isna(risk.get("regionRisk")):
            continue
        missing_fields = [*missing, "regionRisk", "위험등급"]
        component_counts.update(missing)
        region_code = str(risk["시군구코드"])
        reason_counts = hospital_reasons_by_region.get(region_code, Counter())
        cause = ", ".join(
            f"{code} {count}건" for code, count in sorted(reason_counts.items())
        )
        reason = f"{', '.join(missing)} 결측"
        if cause:
            reason += f"; 소속 병원 원인: {cause}"
        rows.append(
            {
                "entity_type": "region",
                "entity_id": region_code,
                "region_code": region_code,
                "name": str(risk.get("시군구명", region_code.split("|", 1)[-1])),
                "missing_fields": "|".join(missing_fields),
                "reason_code": "REGION_COMPONENT_MISSING",
                "reason": reason,
                "priority": "P0",
                "status": "open",
                "next_action": NEXT_ACTIONS["REGION_COMPONENT_MISSING"],
                "source_checked_at": risk_checked_at.get(region_code, ""),
            }
        )

    manifest_path = data_dir / "hira_catalog_manifest.json"
    hira_checked_at = ""
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        hira_checked_at = str(manifest.get("collected_at_utc", ""))
    for _, match in hira.iterrows():
        match_state = str(match.get("매칭상태", "")).strip()
        if match_state in MATCHED_HIRA_STATES:
            continue
        region_code = f"{str(match.get('시도', '')).strip()}|{str(match.get('시군구', '')).strip()}"
        rows.append(
            {
                "entity_type": "hospital_hira",
                "entity_id": str(match["기관코드"]),
                "region_code": region_code,
                "name": str(match["병원명"]),
                "missing_fields": "HIRA식별자|응급의학과전문의수",
                "reason_code": "HIRA_MATCH_DEFERRED",
                "reason": f"{REASON_LABELS['HIRA_MATCH_DEFERRED']} ({match_state or '미매칭'})",
                "priority": "P2",
                "status": "open",
                "next_action": NEXT_ACTIONS["HIRA_MATCH_DEFERRED"],
                "source_checked_at": hira_checked_at,
            }
        )

    report = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    report = report.sort_values(
        ["priority", "entity_type", "region_code", "entity_id"], kind="stable"
    ).reset_index(drop=True)
    entity_counts = report["entity_type"].value_counts().sort_index().to_dict()
    reason_counts = (
        report.loc[report["entity_type"] != "region", "reason_code"]
        .value_counts()
        .sort_index()
        .to_dict()
    )
    checked_values = pd.to_datetime(report["source_checked_at"], errors="coerce", utc=True).dropna()
    source_checked_at = checked_values.max().isoformat() if not checked_values.empty else ""
    summary: dict[str, object] = {
        "schema_version": 1,
        "source_checked_at": source_checked_at,
        "total_open_items": int(len(report)),
        "entity_counts": {key: int(value) for key, value in entity_counts.items()},
        "reason_counts": {key: int(value) for key, value in reason_counts.items()},
        "missing_region_components": {
            key: int(value) for key, value in sorted(component_counts.items())
        },
        "policy": {
            "bed_source_max_age_hours": 12,
            "risk_imputation": False,
            "hira_region_minimum_match_rate": 0.8,
        },
    }
    return report, summary


def write_missingness_report(data_dir: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    report, summary = build_missingness_report(data_dir)
    save_csv(report, Path(data_dir) / "missingness_followup.csv")
    save_json(summary, Path(data_dir) / "missingness_followup_summary.json")
    return report, summary


def main() -> None:
    report, summary = write_missingness_report(DATA_DIR)
    print(
        "Saved missingness follow-up: "
        f"items={len(report):,}, entities={summary['entity_counts']}, "
        f"reasons={summary['reason_counts']}"
    )


if __name__ == "__main__":
    main()
