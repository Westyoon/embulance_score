import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import numpy as np
import pandas as pd
import requests

from common import DATA_DIR, read_csv, save_csv

MASTER = DATA_DIR / "hospital_master.csv"
CENTROIDS = DATA_DIR / "region_centroids.csv"
OUTPUT = DATA_DIR / "kakao_route_accessibility.csv"
DETAIL_OUTPUT = DATA_DIR / "kakao_route_candidates.csv"
KAKAO_DIRECTIONS_URL = "https://apis-navi.kakaomobility.com/v1/directions"
MAX_CANDIDATES = 3


def region_code(frame: pd.DataFrame) -> pd.Series:
    return frame["시도"].fillna("").str.strip() + "|" + frame["시군구"].fillna("").str.strip()


def haversine_distances(lat: float, lon: float, destinations: pd.DataFrame) -> np.ndarray:
    radius = 6371.0
    lat1, lon1 = np.radians([lat, lon])
    lat2 = np.radians(destinations["위도"].to_numpy(dtype=float))
    lon2 = np.radians(destinations["경도"].to_numpy(dtype=float))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return radius * 2 * np.arctan2(np.sqrt(value), np.sqrt(1 - value))


def load_centroids(hospitals: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    if CENTROIDS.exists():
        centroids = read_csv(CENTROIDS)
        required = {"시도", "시군구", "위도", "경도"}
        if not required.issubset(centroids.columns):
            raise ValueError(f"{CENTROIDS} 필수 컬럼: {sorted(required)}")
        return centroids, "공식중심점입력"
    centroids = hospitals.groupby(["시도", "시군구"], as_index=False)[["위도", "경도"]].mean()
    return centroids, "병원좌표평균대체"


def route_request(task: dict, api_key: str) -> dict:
    headers = {
        "Authorization": f"KakaoAK {api_key}",
        "Content-Type": "application/json",
    }
    params = {
        "origin": f"{task['출발경도']},{task['출발위도']}",
        "destination": f"{task['도착경도']},{task['도착위도']},name={task['병원명']}",
        "priority": "RECOMMEND",
        "summary": "true",
        "alternatives": "false",
        "road_details": "false",
    }
    result = {**task, "도로거리_km": pd.NA, "예상시간_분": pd.NA, "통행료_원": pd.NA, "택시비_원": pd.NA, "경로상태": "실패"}
    try:
        response = requests.get(KAKAO_DIRECTIONS_URL, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        routes = response.json().get("routes", [])
        if not routes:
            result["경로상태"] = "경로없음"
            return result
        route = routes[0]
        if route.get("result_code") == 104:
            result.update(
                {
                    "도로거리_km": 0.0,
                    "예상시간_분": 0.0,
                    "통행료_원": 0,
                    "택시비_원": 0,
                    "경로상태": "성공:출도착5m이내",
                }
            )
            return result
        if route.get("result_code") != 0:
            result["경로상태"] = f"경로오류:{route.get('result_code')}:{route.get('result_msg', '')}"
            return result
        summary = route["summary"]
        fare = summary.get("fare", {})
        result.update(
            {
                "도로거리_km": summary["distance"] / 1000,
                "예상시간_분": summary["duration"] / 60,
                "통행료_원": fare.get("toll"),
                "택시비_원": fare.get("taxi"),
                "경로상태": "성공",
            }
        )
    except Exception as exc:
        result["경로상태"] = f"API오류:{type(exc).__name__}"
    return result


def main() -> None:
    api_key = os.getenv("KAKAO_REST_API_KEY", "").strip()
    if not api_key:
        print("Skipped Kakao routes: add KAKAO_REST_API_KEY to .env")
        return

    master = read_csv(MASTER)
    hospitals = master.dropna(subset=["위도", "경도"]).copy()
    eligible = hospitals[hospitals["등급"].astype(str).str.contains("권역|지역응급의료센터", regex=True)].copy()
    if eligible.empty:
        raise RuntimeError("길찾기 목적지로 사용할 권역/지역응급의료센터가 없습니다.")

    centroids, centroid_method = load_centroids(hospitals)
    tasks = []
    for centroid in centroids.itertuples(index=False):
        distances = haversine_distances(float(centroid.위도), float(centroid.경도), eligible)
        nearest_indices = np.argsort(distances)[:MAX_CANDIDATES]
        code = f"{centroid.시도}|{centroid.시군구}"
        for rank, index in enumerate(nearest_indices, start=1):
            hospital = eligible.iloc[int(index)]
            tasks.append(
                {
                    "시군구코드": code,
                    "시도": centroid.시도,
                    "시군구": centroid.시군구,
                    "출발위도": float(centroid.위도),
                    "출발경도": float(centroid.경도),
                    "중심점방법": centroid_method,
                    "후보순위": rank,
                    "기관코드": hospital["기관코드"],
                    "병원명": hospital["병원명"],
                    "도착위도": float(hospital["위도"]),
                    "도착경도": float(hospital["경도"]),
                    "직선거리_km": float(distances[int(index)]),
                }
            )

    results = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(route_request, task, api_key) for task in tasks]
        for future in as_completed(futures):
            results.append(future.result())

    detail = pd.DataFrame(results)
    detail["수집시각"] = datetime.now().astimezone().isoformat(timespec="seconds")

    # 병원 좌표 평균 대체 중심점이 도로에서 벗어나 모든 후보가 실패한 지역은
    # 1순위 후보의 직선거리를 보수적인 대체값으로 남기고 상태를 명확히 표시한다.
    for _, group in detail.groupby("시군구코드"):
        if not group["경로상태"].astype(str).str.startswith("성공").any():
            fallback_index = group["후보순위"].idxmin()
            detail.loc[fallback_index, "도로거리_km"] = detail.loc[fallback_index, "직선거리_km"]
            detail.loc[fallback_index, "경로상태"] = "직선거리대체:출발지도로탐색실패"

    save_csv(detail.sort_values(["시군구코드", "후보순위"]), DETAIL_OUTPUT)

    successful = detail[
        detail["경로상태"].astype(str).str.startswith("성공")
        | detail["경로상태"].astype(str).str.startswith("직선거리대체")
    ].copy()
    if successful.empty:
        raise RuntimeError("카카오 자동차 길찾기에서 성공한 경로가 없습니다. REST API 키와 권한을 확인하세요.")
    successful["도로거리_km"] = pd.to_numeric(successful["도로거리_km"], errors="coerce")
    best_indices = successful.groupby("시군구코드")["도로거리_km"].idxmin()
    best = successful.loc[best_indices].sort_values("시군구코드")
    save_csv(best, OUTPUT)
    print(f"Saved Kakao routes: regions={len(best):,}, requests={len(tasks):,}, output={OUTPUT}")


if __name__ == "__main__":
    main()
