import numpy as np
import pandas as pd

from common import DATA_DIR, read_csv, save_csv

MASTER = DATA_DIR / "hospital_master.csv"
BED_STATUS = DATA_DIR / "bed_status.csv"
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


def build_accessibility(master: pd.DataFrame) -> pd.DataFrame:
    if not KAKAO_ROUTES.exists():
        raise FileNotFoundError(
            f"{KAKAO_ROUTES}가 없습니다. scripts/part3_collect_kakao_routes.py를 먼저 실행하세요."
        )
    routes = read_csv(KAKAO_ROUTES)
    required = {
        "시군구코드",
        "기관코드",
        "병원명",
        "직선거리_km",
        "도로거리_km",
        "예상시간_분",
        "중심점방법",
        "경계버전",
        "경로상태",
        "수집시각",
    }
    if not required.issubset(routes.columns):
        raise ValueError(f"{KAKAO_ROUTES} 필수 컬럼: {sorted(required)}")
    expected_keys = set(region_code(master))
    if routes["시군구코드"].duplicated().any() or set(routes["시군구코드"]) != expected_keys:
        raise ValueError("카카오 접근성 경로가 NEMC 지역 모집단을 중복 없이 정확히 포함하지 않습니다.")
    if not routes["경로상태"].isin(["성공", "성공:출도착5m이내"]).all():
        raise ValueError("카카오 접근성 최종 경로에 성공하지 않은 행이 있습니다.")
    for column in ["직선거리_km", "도로거리_km", "예상시간_분"]:
        routes[column] = pd.to_numeric(routes[column], errors="coerce")
        if routes[column].isna().any() or not routes[column].ge(0).all():
            raise ValueError(f"카카오 접근성 {column}에 결측 또는 음수가 있습니다.")

    result = routes[
        [
            "시군구코드",
            "기관코드",
            "병원명",
            "직선거리_km",
            "도로거리_km",
            "예상시간_분",
            "중심점방법",
            "경계버전",
            "경로상태",
            "수집시각",
        ]
    ].rename(
        columns={
            "기관코드": "최근접기관코드",
            "병원명": "최근접병원",
            "수집시각": "경로수집시각",
        }
    )
    result["접근거리_km"] = result["도로거리_km"]
    result["거리기준"] = "카카오자동차최단거리경로"
    result["접근성점수"] = percentile_score(result["도로거리_km"], higher_is_risk=True)
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
