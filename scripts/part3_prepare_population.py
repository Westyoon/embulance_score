from pathlib import Path

import pandas as pd

from common import DATA_DIR, read_csv, save_csv

SOURCE = DATA_DIR / "101_DT_1B04006_Y_2024.csv"
OUTPUT = DATA_DIR / "population_source.csv"
MASTER = DATA_DIR / "hospital_master.csv"


def main() -> None:
    raw = pd.read_csv(SOURCE, encoding="cp949", skiprows=2)
    geo_code = raw.iloc[:, 0].astype(str).str.replace("'", "", regex=False).str.strip()
    age_code = raw.iloc[:, 2].astype(str).str.replace("'", "", regex=False).str.strip()
    rows = raw.loc[age_code.eq("000")].copy()
    rows["행정코드"] = geo_code.loc[rows.index]
    rows["지역명"] = rows.iloc[:, 1].astype(str).str.strip().str.replace(r"\(통합\)$", "", regex=True)
    rows["인구"] = pd.to_numeric(rows.iloc[:, 5], errors="coerce")

    province_rows = rows[rows["행정코드"].str.len().eq(2)]
    province_map = dict(zip(province_rows["행정코드"], province_rows["지역명"]))
    districts = rows[rows["행정코드"].str.len().gt(2)].copy()
    districts["시도"] = districts["행정코드"].str[:2].map(province_map)
    districts["시군구"] = districts["지역명"]

    # 제주 행정시는 KOSIS에서 하위 코드로 제공되는 경우 함께 사용한다.
    districts = districts[["시도", "시군구", "인구", "행정코드"]].dropna(subset=["시도", "시군구", "인구"])
    master_regions = read_csv(MASTER)[["시도", "시군구"]].dropna().drop_duplicates()
    master_regions["원본시도"] = master_regions["시도"]
    merged_districts = {"동구", "서구", "남구", "북구", "광산구"}
    integrated = master_regions["시도"].eq("전남광주통합특별시")
    master_regions.loc[integrated & master_regions["시군구"].isin(merged_districts), "원본시도"] = "광주광역시"
    master_regions.loc[integrated & ~master_regions["시군구"].isin(merged_districts), "원본시도"] = "전라남도"
    districts = master_regions.merge(
        districts,
        left_on=["원본시도", "시군구"],
        right_on=["시도", "시군구"],
        how="left",
        suffixes=("", "_KOSIS"),
    )
    districts["시도"] = districts["시도"].fillna(districts["원본시도"])
    # 세종은 단층제라 KOSIS에 시군구 하위 행이 없다.
    sejong = districts["시도"].eq("세종특별자치시") & districts["인구"].isna()
    sejong_population = province_rows.loc[province_rows["지역명"].eq("세종특별자치시"), "인구"]
    if not sejong_population.empty:
        districts.loc[sejong, "인구"] = sejong_population.iloc[0]
        districts.loc[sejong, "행정코드"] = "36"
    districts = districts[["시도", "시군구", "인구", "행정코드"]]
    if districts["인구"].isna().any():
        missing = districts.loc[districts["인구"].isna(), ["시도", "시군구"]]
        print(f"Warning: {len(missing)} hospital regions did not match KOSIS")
    save_csv(districts, OUTPUT)
    print(f"Saved {len(districts):,} population regions: {OUTPUT}")


if __name__ == "__main__":
    main()
