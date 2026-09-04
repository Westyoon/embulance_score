import os
from pathlib import Path
import re

import pandas as pd

from common import DATA_DIR, read_csv, request_xml, save_csv, save_json, xml_items
from hospital_population import load_population_audit, plan_hospital_population

OUTPUT = DATA_DIR / "hospital_master.csv"
COORDINATE_OVERRIDES = DATA_DIR / "hospital_coordinate_overrides.csv"
REGION_OVERRIDES = DATA_DIR / "hospital_region_overrides.csv"
EXPECTED_HOSPITALS = 534
EXPECTED_REGIONS = 219
MAX_CARRY_FORWARD_HOSPITALS = 3
MAX_CONSECUTIVE_POPULATION_MISSES = 3
MAX_POPULATION_MISSING_AGE_HOURS = 72.0

GRADE_MAP = {
    "G001": "권역응급의료센터",
    "G006": "지역응급의료센터",
    "G007": "지역응급의료기관",
    "G008": "기타",
    "G009": "응급실운영신고기관",
}


def population_audit_path() -> Path:
    # Full-refresh staging is disposable on any downstream failure. Keep the
    # omission clock in the persistent scheduler state directory so repeated
    # failed runs cannot reset the safety window.
    state_dir = Path(os.getenv("PIPELINE_STATE_DIR", DATA_DIR)).resolve()
    return state_dir / "hospital_population_audit.json"


def split_region(address: str) -> tuple[str, str]:
    parts = str(address or "").strip().split()
    province = parts[0] if parts else ""
    district = parts[1] if len(parts) > 1 else ""
    # 세종시는 시군구가 별도로 없는 단층제 지역이다.
    if province == "세종특별자치시":
        district = "세종시"
    return province, district


def apply_coordinate_overrides(frame: pd.DataFrame) -> pd.DataFrame:
    if not COORDINATE_OVERRIDES.exists():
        return frame
    overrides = read_csv(COORDINATE_OVERRIDES)
    if overrides["기관코드"].duplicated().any():
        raise ValueError("병원 좌표 수동 검증 파일의 기관코드가 중복됩니다.")
    for override in overrides.to_dict("records"):
        mask = frame["기관코드"].eq(override["기관코드"])
        if not mask.any():
            raise ValueError(f"좌표 수동 검증 대상이 NEMC 모집단에 없습니다: {override['기관코드']}")
        latitude = pd.to_numeric(override["위도"], errors="coerce")
        longitude = pd.to_numeric(override["경도"], errors="coerce")
        if pd.isna(latitude) or pd.isna(longitude) or not (33 <= latitude <= 39 and 124 <= longitude <= 132):
            raise ValueError(f"수동 검증 좌표가 대한민국 범위를 벗어납니다: {override['기관코드']}")
        frame.loc[mask, ["위도", "경도", "좌표결측"]] = [latitude, longitude, False]
    return frame


def apply_region_overrides(
    frame: pd.DataFrame,
    allowed_missing_codes: set[str] | None = None,
) -> pd.DataFrame:
    if not REGION_OVERRIDES.exists():
        return frame
    overrides = read_csv(REGION_OVERRIDES)
    required = {
        "기관코드",
        "병원명",
        "원본시도",
        "원본시군구",
        "시도",
        "시군구",
        "근거URL",
        "확인일",
    }
    if not required.issubset(overrides.columns):
        raise ValueError(f"{REGION_OVERRIDES} 필수 컬럼: {sorted(required)}")
    if overrides["기관코드"].duplicated().any():
        raise ValueError("병원 지역 수동 검증 파일의 기관코드가 중복됩니다.")

    allowed_missing = allowed_missing_codes or set()
    pending_updates = []
    for override in overrides.to_dict("records"):
        mask = frame["기관코드"].eq(override["기관코드"])
        if not mask.any():
            if str(override["기관코드"]).strip() in allowed_missing:
                continue
            raise ValueError(f"지역 수동 검증 대상이 NEMC 모집단에 없습니다: {override['기관코드']}")
        evidence_values = [override[column] for column in required - {"기관코드"}]
        if any(pd.isna(value) or not str(value).strip() for value in evidence_values):
            raise ValueError(f"지역 수동 검증 근거가 불완전합니다: {override['기관코드']}")

        expected_name = str(override["병원명"]).strip()
        expected_source = (
            str(override["원본시도"]).strip(),
            str(override["원본시군구"]).strip(),
        )
        target = (str(override["시도"]).strip(), str(override["시군구"]).strip())
        observed = frame.loc[mask, ["병원명", "시도", "시군구"]].iloc[0]
        observed_name = str(observed["병원명"]).strip()
        observed_source = (str(observed["시도"]).strip(), str(observed["시군구"]).strip())
        if observed_name != expected_name:
            raise ValueError(
                "병원 지역 수동 검증의 기관명이 NEMC 원천과 달라 재검토가 필요합니다: "
                f"{override['기관코드']} expected={expected_name}, actual={observed_name}"
            )
        if observed_source != expected_source:
            raise ValueError(
                "병원 지역 수동 검증의 NEMC 원천 지역이 기대값과 달라 재검토가 필요합니다: "
                f"{override['기관코드']} "
                f"expected={expected_source[0]}|{expected_source[1]}, "
                f"actual={observed_source[0]}|{observed_source[1]}"
            )
        if target == expected_source:
            raise ValueError(f"병원 지역 수동 검증의 보정 전후 지역이 같습니다: {override['기관코드']}")

        province = str(override["시도"]).strip()
        district = str(override["시군구"]).strip()
        pending_updates.append((mask, province, district))

    for mask, province, district in pending_updates:
        frame.loc[mask, ["시도", "시군구"]] = [province, district]
    return frame


def main() -> None:
    root = request_xml(
        "getEgytListInfoInqire",
        {"pageNo": 1, "numOfRows": 1000},
    )
    records = xml_items(root)
    reported_total = pd.to_numeric(root.findtext(".//totalCount"), errors="coerce")
    if pd.isna(reported_total) or int(reported_total) != len(records):
        raise RuntimeError(
            f"병원 기본정보 API 응답이 일부만 반환됐습니다: totalCount={reported_total}, rows={len(records)}"
        )

    rows = []
    for item in records:
        address = item.get("dutyAddr", "")
        province, district = split_region(address)
        code = item.get("hpid") or item.get("phpid")
        if not code:
            continue
        grade_code = item.get("dutyEmcls", "")
        rows.append(
            {
                "기관코드": code,
                "병원명": item.get("dutyName", ""),
                "등급": item.get("dutyEmclsName") or GRADE_MAP.get(grade_code, grade_code),
                "위도": pd.to_numeric(item.get("wgs84Lat"), errors="coerce"),
                "경도": pd.to_numeric(item.get("wgs84Lon"), errors="coerce"),
                "주소": address,
                "전화": item.get("dutyTel3") or item.get("dutyTel1", ""),
                "시도": province,
                "시군구": district,
                "좌표결측": not bool(item.get("wgs84Lat") and item.get("wgs84Lon")),
            }
        )

    frame = pd.DataFrame(rows).drop_duplicates("기관코드").sort_values("기관코드")
    previous = read_csv(OUTPUT) if OUTPUT.exists() else None
    audit_path = population_audit_path()
    previous_audit = load_population_audit(audit_path)
    population_plan = plan_hospital_population(
        frame,
        previous,
        previous_audit,
        expected_hospitals=EXPECTED_HOSPITALS,
        expected_regions=EXPECTED_REGIONS,
        max_carry_forward_hospitals=MAX_CARRY_FORWARD_HOSPITALS,
        max_consecutive_misses=MAX_CONSECUTIVE_POPULATION_MISSES,
        max_missing_age_hours=MAX_POPULATION_MISSING_AGE_HOURS,
    )
    frame = apply_region_overrides(frame, population_plan.carried_codes)
    if not population_plan.carried.empty:
        frame = pd.concat([frame, population_plan.carried], ignore_index=True)
    frame = frame.sort_values("기관코드").reset_index(drop=True)
    if frame["기관코드"].duplicated().any():
        raise RuntimeError("병원 마스터에 중복 기관코드가 있습니다.")
    region_count = len(frame[["시도", "시군구"]].drop_duplicates())
    if len(frame) != EXPECTED_HOSPITALS or region_count != EXPECTED_REGIONS:
        change_details = ""
        if OUTPUT.exists():
            previous = read_csv(OUTPUT)
            previous_regions = set(previous["시도"].astype(str) + "|" + previous["시군구"].astype(str))
            current_regions = set(frame["시도"].astype(str) + "|" + frame["시군구"].astype(str))
            previous_by_code = previous.set_index("기관코드")
            current_by_code = frame.set_index("기관코드")
            common_codes = previous_by_code.index.intersection(current_by_code.index)
            moved = []
            for code in common_codes:
                before = f"{previous_by_code.at[code, '시도']}|{previous_by_code.at[code, '시군구']}"
                after = f"{current_by_code.at[code, '시도']}|{current_by_code.at[code, '시군구']}"
                if before != after:
                    moved.append(f"{code}:{before}->{after}")
            change_details = (
                f", added_regions={sorted(current_regions - previous_regions)}, "
                f"removed_regions={sorted(previous_regions - current_regions)}, "
                f"added_hospitals={sorted(set(current_by_code.index) - set(previous_by_code.index))}, "
                f"removed_hospitals={sorted(set(previous_by_code.index) - set(current_by_code.index))}, "
                f"moved_hospitals={moved}"
            )
        raise RuntimeError(
            "NEMC 모집단이 검토 기준과 달라 승격하지 않습니다: "
            f"hospitals={len(frame)} (expected={EXPECTED_HOSPITALS}), "
            f"regions={region_count} (expected={EXPECTED_REGIONS}){change_details}"
        )
    frame = apply_coordinate_overrides(frame)
    save_csv(frame, OUTPUT)
    population_audit = {
        **population_plan.audit,
        "reconciledHospitals": len(frame),
        "reconciledRegions": region_count,
    }
    save_json(population_audit, audit_path)
    print(f"Saved {len(frame):,} hospitals: {OUTPUT}")


if __name__ == "__main__":
    main()

