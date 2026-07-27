from math import atan2, cos, radians, sin, sqrt

import numpy as np
import pandas as pd

from common import DATA_DIR, read_csv, save_csv

MASTER = DATA_DIR / "hospital_master.csv"
BED_STATUS = DATA_DIR / "bed_status.csv"
CENTROIDS = DATA_DIR / "region_centroids.csv"
POPULATION_SOURCE = DATA_DIR / "population_source.csv"
DOCTOR_SOURCE = DATA_DIR / "doctor_source.csv"
KAKAO_ROUTES = DATA_DIR / "kakao_route_accessibility.csv"


def region_code(frame: pd.DataFrame) -> pd.Series:
    return frame["시도"].fillna("").str.strip() + "|" + frame["시군구"].fillna("").str.strip()


def percentile_score(values: pd.Series, higher_is_risk: bool = True) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=values.index)
    low, high = valid.quantile([0.05, 0.95])
    if high <= low:
        score = pd.Series(50.0, index=values.index)
    else:
        score = ((numeric.clip(low, high) - low) / (high - low) * 100)
    return score if higher_is_risk else 100 - score


def haversine_vector(lat, lon, hospital_lat, hospital_lon):
    radius = 6371.0
    lat1, lon1 = radians(lat), radians(lon)
    lat2 = np.radians(hospital_lat.to_numpy(dtype=float))
    lon2 = np.radians(hospital_lon.to_numpy(dtype=float))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return radius * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def build_accessibility(master: pd.DataFrame) -> pd.DataFrame:
    if KAKAO_ROUTES.exists():
        routes = read_csv(KAKAO_ROUTES)
        required = {"시군구코드", "병원명", "직선거리_km", "도로거리_km", "예상시간_분", "중심점방법"}
        if not required.issubset(routes.columns):
            raise ValueError(f"{KAKAO_ROUTES} 필수 컬럼: {sorted(required)}")
        result = routes[
            ["시군구코드", "병원명", "직선거리_km", "도로거리_km", "예상시간_분", "중심점방법", "수집시각"]
        ].rename(columns={"병원명": "최근접병원", "수집시각": "경로수집시각"})
        result["접근성점수"] = percentile_score(result["도로거리_km"], higher_is_risk=True)
        result["거리기준"] = "카카오자동차추천경로"
        save_csv(result, DATA_DIR / "accessibility_score.csv")
        return result

    hospitals = master.dropna(subset=["위도", "경도"]).copy()
    eligible = hospitals[hospitals["등급"].astype(str).str.contains("권역|지역응급의료센터", regex=True)]
    if eligible.empty:
        raise RuntimeError("권역/지역응급의료센터 좌표가 없습니다.")

    if CENTROIDS.exists():
        centroids = read_csv(CENTROIDS)
        required = {"시도", "시군구", "위도", "경도"}
        if not required.issubset(centroids.columns):
            raise ValueError(f"{CENTROIDS} 필수 컬럼: {sorted(required)}")
        method = "공식중심점입력"
    else:
        # 공식 중심점이 없을 때만 해당 지역 내 응급기관 좌표 평균을 명시적 대체값으로 사용한다.
        centroids = hospitals.groupby(["시도", "시군구"], as_index=False)[["위도", "경도"]].mean()
        method = "병원좌표평균대체"

    rows = []
    for item in centroids.itertuples(index=False):
        distances = haversine_vector(float(item.위도), float(item.경도), eligible["위도"], eligible["경도"])
        nearest_idx = int(np.argmin(distances))
        nearest = eligible.iloc[nearest_idx]
        rows.append({
            "시군구코드": f"{item.시도}|{item.시군구}",
            "최근접병원": nearest["병원명"],
            "직선거리_km": float(distances[nearest_idx]),
            "중심점방법": method,
        })
    result = pd.DataFrame(rows)
    result["접근성점수"] = percentile_score(result["직선거리_km"], higher_is_risk=True)
    result["거리기준"] = "하버사인직선거리"
    save_csv(result, DATA_DIR / "accessibility_score.csv")
    return result


def build_population_bed(beds: pd.DataFrame) -> pd.DataFrame | None:
    if not POPULATION_SOURCE.exists():
        return None
    population = read_csv(POPULATION_SOURCE)
    required = {"시도", "시군구", "인구"}
    if not required.issubset(population.columns):
        raise ValueError(f"{POPULATION_SOURCE} 필수 컬럼: {sorted(required)}")
    population["시군구코드"] = region_code(population)
    totals = beds.assign(시군구코드=region_code(beds)).groupby("시군구코드", as_index=False)["전체병상"].sum(min_count=1)
    result = population[["시군구코드", "인구"]].merge(totals, on="시군구코드", how="left")
    result = result.rename(columns={"전체병상": "총병상수"})
    result["인구대비병상비율"] = result["인구"] / result["총병상수"].replace(0, np.nan)
    result["인구대비병상점수"] = percentile_score(result["인구대비병상비율"], higher_is_risk=True)
    save_csv(result, DATA_DIR / "population_bed_score.csv")
    return result


def build_doctor(beds: pd.DataFrame) -> pd.DataFrame | None:
    if not DOCTOR_SOURCE.exists():
        return None
    doctors = read_csv(DOCTOR_SOURCE)
    required = {"시도", "시군구", "응급의학과전문의수"}
    if not required.issubset(doctors.columns):
        raise ValueError(f"{DOCTOR_SOURCE} 필수 컬럼: {sorted(required)}")
    doctors["시군구코드"] = region_code(doctors)
    doctors = doctors.groupby("시군구코드", as_index=False)["응급의학과전문의수"].sum(min_count=1)
    totals = beds.assign(시군구코드=region_code(beds)).groupby("시군구코드", as_index=False)["전체병상"].sum(min_count=1)
    result = doctors.merge(totals, on="시군구코드", how="left").rename(columns={"전체병상": "병상수"})
    result["전문의0명"] = result["응급의학과전문의수"].eq(0)
    result["병상대비전문의부족비율"] = result["병상수"] / result["응급의학과전문의수"].replace(0, np.nan)
    result["의료진부족점수"] = percentile_score(result["병상대비전문의부족비율"], higher_is_risk=True)
    result.loc[result["전문의0명"], "의료진부족점수"] = 100.0
    save_csv(result, DATA_DIR / "doctor_score.csv")
    return result


def main() -> None:
    master, beds = read_csv(MASTER), read_csv(BED_STATUS)
    access = build_accessibility(master)
    population = build_population_bed(beds)
    doctors = build_doctor(beds)
    print(f"Saved accessibility scores: {len(access):,}")
    if population is None:
        print(f"Skipped population score: add {POPULATION_SOURCE.name}")
    if doctors is None:
        print(f"Skipped doctor score: add {DOCTOR_SOURCE.name}")


if __name__ == "__main__":
    main()
