import re

import pandas as pd

from common import DATA_DIR, read_csv, request_xml, save_csv, xml_items

OUTPUT = DATA_DIR / "hospital_master.csv"
COORDINATE_OVERRIDES = DATA_DIR / "hospital_coordinate_overrides.csv"
EXPECTED_HOSPITALS = 534
EXPECTED_REGIONS = 219

GRADE_MAP = {
    "G001": "권역응급의료센터",
    "G006": "지역응급의료센터",
    "G007": "지역응급의료기관",
    "G008": "기타",
    "G009": "응급실운영신고기관",
}


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
    if frame["기관코드"].duplicated().any():
        raise RuntimeError("병원 마스터에 중복 기관코드가 있습니다.")
    region_count = len(frame[["시도", "시군구"]].drop_duplicates())
    if len(frame) != EXPECTED_HOSPITALS or region_count != EXPECTED_REGIONS:
        raise RuntimeError(
            "NEMC 모집단이 검토 기준과 달라 승격하지 않습니다: "
            f"hospitals={len(frame)} (expected={EXPECTED_HOSPITALS}), "
            f"regions={region_count} (expected={EXPECTED_REGIONS})"
        )
    frame = apply_coordinate_overrides(frame)
    save_csv(frame, OUTPUT)
    print(f"Saved {len(frame):,} hospitals: {OUTPUT}")


if __name__ == "__main__":
    main()

