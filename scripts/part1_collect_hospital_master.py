import re

import pandas as pd

from common import DATA_DIR, request_xml, save_csv, xml_items

OUTPUT = DATA_DIR / "hospital_master.csv"

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


def main() -> None:
    root = request_xml(
        "getEgytListInfoInqire",
        {"pageNo": 1, "numOfRows": 1000},
    )
    records = xml_items(root)
    if not records:
        raise RuntimeError("병원 기본정보 API가 빈 결과를 반환했습니다.")

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
    save_csv(frame, OUTPUT)
    print(f"Saved {len(frame):,} hospitals: {OUTPUT}")


if __name__ == "__main__":
    main()

