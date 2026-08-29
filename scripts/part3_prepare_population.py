import argparse
import re
from pathlib import Path

import pandas as pd

from common import DATA_DIR, read_csv, save_csv

OUTPUT = DATA_DIR / "population_source.csv"
MASTER = DATA_DIR / "hospital_master.csv"


def latest_source(pattern: str) -> Path | None:
    candidates = sorted(DATA_DIR.glob(pattern))
    return candidates[-1] if candidates else None


def master_regions() -> pd.DataFrame:
    master = read_csv(MASTER)
    if master[["시도", "시군구"]].isna().any().any():
        raise ValueError("NEMC 마스터에 시도·시군구 결측이 있습니다.")
    return master[["시도", "시군구"]].drop_duplicates().copy()


def prepare_mois(source: Path, regions: pd.DataFrame) -> pd.DataFrame:
    raw = pd.read_csv(source, encoding="cp949", dtype=str)
    region_column = raw.columns[0]
    population_columns = [column for column in raw.columns if column.endswith("_계_총인구수")]
    if len(population_columns) != 1:
        raise ValueError(f"{source}에서 총인구수 컬럼을 하나만 찾지 못했습니다.")

    raw["지역경로"] = (
        raw[region_column]
        .str.replace(r"\s*\((\d{10})\)\s*$", "", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )
    raw["행정코드10"] = raw[region_column].str.extract(r"\((\d{10})\)\s*$", expand=False)
    raw["인구"] = pd.to_numeric(raw[population_columns[0]].str.replace(",", "", regex=False), errors="coerce")
    # 세종은 시도(3600000000)와 시군구(3611000000)의 명칭이 같으므로
    # 시군구 행을 우선해 하나의 지역으로 정규화한다.
    duplicate_paths = set(raw.loc[raw["지역경로"].duplicated(keep=False), "지역경로"])
    unexpected_duplicates = duplicate_paths - {"세종특별자치시"}
    if unexpected_duplicates:
        raise ValueError(f"행정안전부 인구 원천에 중복 지역이 있습니다: {sorted(unexpected_duplicates)[:10]}")
    raw["시군구행"] = raw["행정코드10"].str[2:5].ne("000")
    raw = raw.sort_values("시군구행", ascending=False).drop_duplicates("지역경로", keep="first")

    prepared = regions.copy()
    prepared["지역경로"] = prepared["시도"] + " " + prepared["시군구"]
    sejong = prepared["시도"].eq("세종특별자치시") & prepared["시군구"].eq("세종시")
    prepared.loc[sejong, "지역경로"] = "세종특별자치시"
    prepared = prepared.merge(
        raw[["지역경로", "인구", "행정코드10"]],
        on="지역경로",
        how="left",
        validate="one_to_one",
    )
    prepared["행정코드"] = prepared["행정코드10"].str[:5]
    sejong_after_merge = prepared["시도"].eq("세종특별자치시") & prepared["시군구"].eq("세종시")
    prepared.loc[sejong_after_merge, "행정코드"] = "36110"
    header_period = re.match(r"^(\d{4})년(\d{2})월_", population_columns[0])
    filename_period = re.search(r"(\d{6})", source.stem)
    if not header_period or not filename_period:
        raise ValueError(f"행정안전부 인구 원천의 기준연월을 확인할 수 없습니다: {source}")
    period = f"{header_period.group(1)}{header_period.group(2)}"
    if period != filename_period.group(1):
        raise ValueError(f"행정안전부 인구 파일명과 헤더 연월이 다릅니다: file={filename_period.group(1)}, header={period}")
    prepared["기준연월"] = period
    prepared["원천"] = "행정안전부 주민등록 인구통계"
    return prepared[["시도", "시군구", "인구", "행정코드", "기준연월", "원천"]]


def prepare_kosis(source: Path, regions: pd.DataFrame) -> pd.DataFrame:
    raw = pd.read_csv(source, encoding="cp949", skiprows=2)
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
    districts = districts[["시도", "시군구", "인구", "행정코드"]].dropna(subset=["시도", "시군구", "인구"])

    prepared = regions.copy()
    prepared["원본시도"] = prepared["시도"]
    integrated_districts = {"동구", "서구", "남구", "북구", "광산구"}
    integrated = prepared["시도"].eq("전남광주통합특별시")
    prepared.loc[integrated & prepared["시군구"].isin(integrated_districts), "원본시도"] = "광주광역시"
    prepared.loc[integrated & ~prepared["시군구"].isin(integrated_districts), "원본시도"] = "전라남도"
    prepared = prepared.merge(
        districts,
        left_on=["원본시도", "시군구"],
        right_on=["시도", "시군구"],
        how="left",
        suffixes=("", "_KOSIS"),
    )
    sejong = prepared["시도"].eq("세종특별자치시") & prepared["인구"].isna()
    sejong_population = province_rows.loc[province_rows["지역명"].eq("세종특별자치시"), "인구"]
    if not sejong_population.empty:
        prepared.loc[sejong, "인구"] = sejong_population.iloc[0]
        prepared.loc[sejong, "행정코드"] = "36110"
    year = re.search(r"Y_(\d{4})", source.name)
    prepared["기준연월"] = f"{year.group(1)}12" if year else ""
    prepared["원천"] = "KOSIS 주민등록 인구"
    return prepared[["시도", "시군구", "인구", "행정코드", "기준연월", "원천"]]


def main() -> None:
    parser = argparse.ArgumentParser(description="NEMC 지역 모집단에 공식 인구를 연결")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--period", help="사용할 행정안전부 원천 연월(YYYYMM)")
    source_group.add_argument("--source", type=Path, help="사용할 행정안전부 인구 원천 CSV 경로")
    args = parser.parse_args()
    regions = master_regions()
    if args.period and (len(args.period) != 6 or not args.period.isdigit()):
        raise ValueError("--period는 YYYYMM 형식이어야 합니다.")
    explicit_source = args.source or (DATA_DIR / f"mois_population_{args.period}.csv" if args.period else None)
    if explicit_source and not explicit_source.exists():
        raise FileNotFoundError(explicit_source)
    mois_source = explicit_source or latest_source("mois_population_*.csv")
    if mois_source:
        result = prepare_mois(mois_source, regions)
        source = mois_source
    else:
        kosis_source = latest_source("101_DT_1B04006_Y_*.csv")
        if not kosis_source:
            raise FileNotFoundError("행정안전부 또는 KOSIS 주민등록 인구 원천이 없습니다.")
        result = prepare_kosis(kosis_source, regions)
        source = kosis_source

    missing = result[result[["인구", "행정코드"]].isna().any(axis=1)]
    if not missing.empty:
        keys = (missing["시도"] + "|" + missing["시군구"]).tolist()
        raise RuntimeError(f"인구·행정코드 미매칭 {len(keys)}개: {keys}")
    if len(result) != len(regions):
        raise RuntimeError(f"NEMC 지역 모집단이 보존되지 않았습니다: expected={len(regions)}, actual={len(result)}")
    if not result["인구"].gt(0).all():
        raise RuntimeError("인구가 0 이하인 지역이 있습니다.")
    codes = result["행정코드"].astype("string")
    if not codes.str.fullmatch(r"\d{5}").all() or codes.duplicated().any():
        raise RuntimeError("행정코드는 중복 없는 5자리 숫자여야 합니다.")

    save_csv(result.sort_values(["시도", "시군구"]), OUTPUT)
    print(f"Saved {len(result):,} population regions from {source.name}: {OUTPUT}")


if __name__ == "__main__":
    main()
