import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import json
from math import cos, radians
import os
from pathlib import Path
import re
import threading
import time

import numpy as np
import pandas as pd
import requests

from common import DATA_DIR, ROOT, read_csv, save_csv


MASTER = DATA_DIR / "hospital_master.csv"
CENTROIDS = DATA_DIR / "region_centroids.csv"
ORIGIN_OUTPUT = DATA_DIR / "region_route_origins.csv"
CANDIDATE_OUTPUT = DATA_DIR / "kakao_route_candidates.csv"
ACCESSIBILITY_OUTPUT = DATA_DIR / "kakao_route_accessibility.csv"
HOSPITAL_OUTPUT = DATA_DIR / "kakao_hospital_routes.csv"
KAKAO_DIRECTIONS_URL = "https://apis-navi.kakaomobility.com/v1/directions"
ROUTE_PRIORITY = "DISTANCE"
ROUTE_SCHEMA_VERSION = "kakao-directions-v1-distance"
DEFAULT_MAX_CANDIDATES = 10
DEFAULT_ROUTE_CACHE_TTL_DAYS = 30
MAX_ROUTE_API_CALLS = 8_500
SUCCESS_STATUSES = {"성공", "성공:출도착5m이내"}
CONFIRMED_NO_ROUTE_RESULT_CODES = {1, 101, 102, 103, 105, 106, 107}
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
ROUTE_FIELDS = [
    "도로거리_km",
    "예상시간_분",
    "경로결과코드",
    "경로상태",
    "수집시각",
]

_thread_local = threading.local()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def is_cache_fresh(
    collected_at: object,
    ttl_days: int,
    reference_time: datetime | None = None,
) -> bool:
    try:
        value = str(collected_at).strip()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return False
    current = reference_time or datetime.now().astimezone()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("캐시 기준 시각은 timezone-aware datetime이어야 합니다.")
    age = current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)
    return timedelta(0) <= age <= timedelta(days=ttl_days)


def region_code(frame: pd.DataFrame) -> pd.Series:
    return frame["시도"].fillna("").str.strip() + "|" + frame["시군구"].fillna("").str.strip()


def haversine_distances(lat: float, lon: float, destinations: pd.DataFrame) -> np.ndarray:
    radius = 6371.0
    lat1, lon1 = np.radians([lat, lon])
    lat2 = np.radians(pd.to_numeric(destinations["위도"], errors="coerce").to_numpy(dtype=float))
    lon2 = np.radians(pd.to_numeric(destinations["경도"], errors="coerce").to_numpy(dtype=float))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    value = np.clip(value, 0, 1)
    return radius * 2 * np.arctan2(np.sqrt(value), np.sqrt(1 - value))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    one = pd.DataFrame({"위도": [lat2], "경도": [lon2]})
    return float(haversine_distances(lat1, lon1, one)[0])


def route_request_key(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float) -> str:
    payload = (
        f"{ROUTE_SCHEMA_VERSION}|{origin_lon:.7f},{origin_lat:.7f}|"
        f"{dest_lon:.7f},{dest_lat:.7f}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def origin_input_key(row: pd.Series | dict) -> str:
    payload = (
        f"{row['시군구코드']}|{float(row['기준경도']):.7f},{float(row['기준위도']):.7f}|"
        f"{row['중심점방법']}|{row.get('경계버전', '')}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ring_area_centroid(ring: list[list[float]]) -> tuple[float, float, float]:
    points = [(float(point[0]), float(point[1])) for point in ring if len(point) >= 2]
    if len(points) < 3:
        return 0.0, 0.0, 0.0
    cross_sum = 0.0
    cx_sum = 0.0
    cy_sum = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        cross = x1 * y2 - x2 * y1
        cross_sum += cross
        cx_sum += (x1 + x2) * cross
        cy_sum += (y1 + y2) * cross
    signed_area = cross_sum / 2
    if abs(signed_area) < 1e-12:
        return 0.0, float(np.mean([point[0] for point in points])), float(np.mean([point[1] for point in points]))
    return abs(signed_area), cx_sum / (6 * signed_area), cy_sum / (6 * signed_area)


def _polygon_area_centroid(polygon: list[list[list[float]]]) -> tuple[float, float, float]:
    if not polygon:
        return 0.0, 0.0, 0.0
    outer_area, outer_x, outer_y = _ring_area_centroid(polygon[0])
    weighted_x = outer_area * outer_x
    weighted_y = outer_area * outer_y
    net_area = outer_area
    for hole in polygon[1:]:
        area, cx, cy = _ring_area_centroid(hole)
        net_area -= area
        weighted_x -= area * cx
        weighted_y -= area * cy
    if net_area <= 1e-12:
        return outer_area, outer_x, outer_y
    return net_area, weighted_x / net_area, weighted_y / net_area


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    points = [(float(point[0]), float(point[1])) for point in ring if len(point) >= 2]
    if len(points) < 3:
        return False
    previous_x, previous_y = points[-1]
    for current_x, current_y in points:
        crosses = (current_y > lat) != (previous_y > lat)
        if crosses:
            intersect_x = (previous_x - current_x) * (lat - current_y) / (previous_y - current_y) + current_x
            if lon < intersect_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


def _point_in_polygon(lon: float, lat: float, polygon: list[list[list[float]]]) -> bool:
    return bool(polygon) and _point_in_ring(lon, lat, polygon[0]) and not any(
        _point_in_ring(lon, lat, hole) for hole in polygon[1:]
    )


def _geometry_polygons(geometry: dict) -> list[list[list[list[float]]]]:
    if geometry.get("type") == "Polygon":
        return [geometry.get("coordinates", [])]
    if geometry.get("type") == "MultiPolygon":
        return geometry.get("coordinates", [])
    raise ValueError(f"지원하지 않는 경계 geometry: {geometry.get('type')}")


def representative_point(polygons: list[list[list[list[float]]]]) -> tuple[float, float]:
    measured = [(*_polygon_area_centroid(polygon), polygon) for polygon in polygons]
    measured = [item for item in measured if item[0] > 0]
    if not measured:
        raise ValueError("면적을 계산할 수 없는 경계입니다.")
    total_area = sum(item[0] for item in measured)
    target_lon = sum(item[0] * item[1] for item in measured) / total_area
    target_lat = sum(item[0] * item[2] for item in measured) / total_area
    if any(_point_in_polygon(target_lon, target_lat, item[3]) for item in measured):
        return target_lon, target_lat

    # 도서·오목 다각형은 전체 무게중심이 바다나 경계 밖일 수 있다. 가장 큰
    # 육지 다각형 안에서 기하중심과 가장 가까운 격자점을 대표점으로 사용한다.
    _, polygon_lon, polygon_lat, polygon = max(measured, key=lambda item: item[0])
    if _point_in_polygon(polygon_lon, polygon_lat, polygon):
        return polygon_lon, polygon_lat
    outer = polygon[0]
    longitudes = [float(point[0]) for point in outer]
    latitudes = [float(point[1]) for point in outer]
    best: tuple[float, float, float] | None = None
    for lon in np.linspace(min(longitudes), max(longitudes), 41):
        for lat in np.linspace(min(latitudes), max(latitudes), 41):
            if not _point_in_polygon(float(lon), float(lat), polygon):
                continue
            distance = (float(lon) - polygon_lon) ** 2 + (float(lat) - polygon_lat) ** 2
            if best is None or distance < best[0]:
                best = (distance, float(lon), float(lat))
    if best is None:
        raise ValueError("경계 내부 대표점을 찾지 못했습니다.")
    return best[1], best[2]


def boundary_file() -> Path:
    configured = os.getenv("BOUNDARY_FILE", "").strip()
    if configured and Path(configured).exists():
        return Path(configured).resolve()
    return ROOT / "src" / "data" / "koreaGeo.json"


def build_boundary_origins(hospitals: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list]]:
    geo = json.loads(boundary_file().read_text(encoding="utf-8"))
    boundary_version = str(geo.get("metadata", {}).get("version", ""))
    master_keys = set(region_code(hospitals))
    polygons_by_key: dict[str, list] = {key: [] for key in master_keys}
    feature_counts = {key: 0 for key in master_keys}

    for feature in geo.get("features", []):
        properties = feature.get("properties", {})
        sido = str(properties.get("sido", "")).strip()
        name = str(properties.get("name", "")).strip()
        key = f"{sido}|{name}"
        if key not in master_keys:
            match = re.match(r"^(.+?시)(.+구)$", name)
            parent_key = f"{sido}|{match.group(1)}" if match else None
            key = parent_key if parent_key in master_keys else ""
        if not key:
            continue
        polygons_by_key[key].extend(_geometry_polygons(feature.get("geometry", {})))
        feature_counts[key] += 1

    missing = sorted(key for key, polygons in polygons_by_key.items() if not polygons)
    if missing:
        raise RuntimeError(f"최신 경계에서 대표점을 만들 수 없는 NEMC 지역: {missing}")

    rows = []
    for key in sorted(master_keys):
        lon, lat = representative_point(polygons_by_key[key])
        sido, sigungu = key.split("|", 1)
        rows.append(
            {
                "시군구코드": key,
                "시도": sido,
                "시군구": sigungu,
                "기준위도": lat,
                "기준경도": lon,
                "중심점방법": "최신경계기하대표점",
                "경계버전": boundary_version,
                "원본경계수": feature_counts[key],
            }
        )
    return pd.DataFrame(rows), polygons_by_key


def load_base_origins(hospitals: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list]]:
    boundary_origins, polygons_by_key = build_boundary_origins(hospitals)
    if not CENTROIDS.exists():
        return boundary_origins, polygons_by_key

    centroids = read_csv(CENTROIDS).copy()
    required = {"시도", "시군구", "위도", "경도"}
    if not required.issubset(centroids.columns):
        raise ValueError(f"{CENTROIDS} 필수 컬럼: {sorted(required)}")
    centroids["시군구코드"] = region_code(centroids)
    expected_keys = set(region_code(hospitals))
    if centroids["시군구코드"].duplicated().any() or set(centroids["시군구코드"]) != expected_keys:
        raise ValueError("region_centroids.csv는 NEMC 219개 지역을 중복 없이 정확히 포함해야 합니다.")
    centroids["기준위도"] = pd.to_numeric(centroids["위도"], errors="coerce")
    centroids["기준경도"] = pd.to_numeric(centroids["경도"], errors="coerce")
    if centroids[["기준위도", "기준경도"]].isna().any().any():
        raise ValueError("region_centroids.csv에 숫자가 아닌 좌표가 있습니다.")
    centroids["중심점방법"] = centroids.get("중심점방법", "공식중심점입력")
    centroids["중심점방법"] = centroids["중심점방법"].fillna("공식중심점입력")
    centroids["경계버전"] = centroids.get("경계버전", "사용자입력")
    centroids["원본경계수"] = pd.NA
    return centroids[
        ["시군구코드", "시도", "시군구", "기준위도", "기준경도", "중심점방법", "경계버전", "원본경계수"]
    ], polygons_by_key


def _http_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session
    return session


def parse_route_payload(payload: dict) -> dict:
    routes = payload.get("routes", []) if isinstance(payload, dict) else []
    if not routes:
        return {
            "도로거리_km": pd.NA,
            "예상시간_분": pd.NA,
            "경로결과코드": pd.NA,
            "경로상태": "경로없음",
        }
    route = routes[0]
    result_code = route.get("result_code")
    if result_code == 104:
        return {
            "도로거리_km": 0.0,
            "예상시간_분": 0.0,
            "경로결과코드": 104,
            "경로상태": "성공:출도착5m이내",
        }
    if result_code != 0:
        return {
            "도로거리_km": pd.NA,
            "예상시간_분": pd.NA,
            "경로결과코드": result_code if result_code is not None else pd.NA,
            "경로상태": f"경로오류:{result_code}",
        }
    summary = route.get("summary", {})
    try:
        distance_km = float(summary["distance"]) / 1000
        duration_min = float(summary["duration"]) / 60
    except (KeyError, TypeError, ValueError):
        return {
            "도로거리_km": pd.NA,
            "예상시간_분": pd.NA,
            "경로결과코드": 0,
            "경로상태": "응답형식오류",
        }
    if not np.isfinite(distance_km) or not np.isfinite(duration_min) or distance_km < 0 or duration_min < 0:
        return {
            "도로거리_km": pd.NA,
            "예상시간_분": pd.NA,
            "경로결과코드": 0,
            "경로상태": "응답값오류",
        }
    return {
        "도로거리_km": distance_km,
        "예상시간_분": duration_min,
        "경로결과코드": 0,
        "경로상태": "성공",
    }


def fetch_route(task: dict, api_key: str, attempts: int = 3) -> dict:
    headers = {"Authorization": f"KakaoAK {api_key}", "Content-Type": "application/json"}
    params = {
        "origin": f"{float(task['출발경도']):.7f},{float(task['출발위도']):.7f}",
        "destination": f"{float(task['도착경도']):.7f},{float(task['도착위도']):.7f}",
        "priority": ROUTE_PRIORITY,
        "summary": "true",
        "alternatives": "false",
        "road_details": "false",
    }
    last_status = "API오류:Unknown"
    for attempt in range(attempts):
        try:
            response = _http_session().get(
                KAKAO_DIRECTIONS_URL,
                headers=headers,
                params=params,
                timeout=(10, 35),
            )
            if response.status_code in RETRYABLE_HTTP_STATUSES and attempt + 1 < attempts:
                retry_after = response.headers.get("Retry-After", "")
                delay = min(float(retry_after), 10.0) if retry_after.replace(".", "", 1).isdigit() else 0.6 * (2**attempt)
                time.sleep(delay)
                continue
            if response.status_code != 200:
                last_status = f"HTTP오류:{response.status_code}"
                break
            parsed = parse_route_payload(response.json())
            return {**parsed, "수집시각": now_iso()}
        except (requests.RequestException, ValueError) as exc:
            last_status = f"API오류:{type(exc).__name__}"
            if attempt + 1 < attempts:
                time.sleep(0.6 * (2**attempt))
    return {
        "도로거리_km": pd.NA,
        "예상시간_분": pd.NA,
        "경로결과코드": pd.NA,
        "경로상태": last_status,
        "수집시각": now_iso(),
    }


def is_success(result: dict | pd.Series) -> bool:
    return str(result.get("경로상태", "")) in SUCCESS_STATUSES


def origin_candidates(
    base_lat: float,
    base_lon: float,
    polygons: list,
    local_hospitals: pd.DataFrame,
) -> list[tuple[float, float]]:
    candidates = [(base_lat, base_lon)]
    one_km_lat = 1 / 111.32
    one_km_lon = 1 / (111.32 * max(cos(radians(base_lat)), 0.2))
    for dx, dy in [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]:
        lat = base_lat + dy * one_km_lat
        lon = base_lon + dx * one_km_lon
        if not polygons or any(_point_in_polygon(lon, lat, polygon) for polygon in polygons):
            candidates.append((lat, lon))

    if not local_hospitals.empty:
        distances = haversine_distances(base_lat, base_lon, local_hospitals)
        nearest = local_hospitals.iloc[int(np.argmin(distances))]
        hospital_lat = float(nearest["위도"])
        hospital_lon = float(nearest["경도"])
        for fraction in (0.25, 0.5, 0.75):
            lat = base_lat + (hospital_lat - base_lat) * fraction
            lon = base_lon + (hospital_lon - base_lon) * fraction
            if not polygons or any(_point_in_polygon(lon, lat, polygon) for polygon in polygons):
                candidates.append((lat, lon))

    unique: list[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()
    for lat, lon in candidates:
        token = (round(lat, 7), round(lon, 7))
        if token not in seen:
            seen.add(token)
            unique.append((lat, lon))
    return unique


def select_route_origin(
    base: dict,
    eligible: pd.DataFrame,
    local_hospitals: pd.DataFrame,
    polygons: list,
    api_key: str,
) -> tuple[dict, dict[str, dict]]:
    base_lat = float(base["기준위도"])
    base_lon = float(base["기준경도"])
    distances = haversine_distances(base_lat, base_lon, eligible)
    destinations = eligible.iloc[np.argsort(distances)[:2]]
    probe_results: dict[str, dict] = {}
    last_status = "도로탐색실패"

    for origin_lat, origin_lon in origin_candidates(base_lat, base_lon, polygons, local_hospitals):
        for destination in destinations.to_dict("records"):
            task = {
                "출발위도": origin_lat,
                "출발경도": origin_lon,
                "도착위도": float(destination["위도"]),
                "도착경도": float(destination["경도"]),
            }
            key = route_request_key(
                task["출발위도"], task["출발경도"], task["도착위도"], task["도착경도"]
            )
            result = fetch_route(task, api_key)
            probe_results[key] = result
            last_status = str(result["경로상태"])
            if is_success(result):
                adjusted = haversine_km(base_lat, base_lon, origin_lat, origin_lon)
                method = str(base["중심점방법"])
                if adjusted > 0.001:
                    method += ":도로탐색보정"
                return (
                    {
                        **base,
                        "출발위도": origin_lat,
                        "출발경도": origin_lon,
                        "중심점방법": method,
                        "도로보정_km": adjusted,
                        "도로탐색상태": "성공",
                        "기준점키": origin_input_key(base),
                        "수집시각": now_iso(),
                    },
                    probe_results,
                )
            if result.get("경로결과코드") == 102:
                break
            if last_status.startswith(("HTTP오류", "API오류")):
                return (
                    {
                        **base,
                        "출발위도": base_lat,
                        "출발경도": base_lon,
                        "도로보정_km": 0.0,
                        "도로탐색상태": last_status,
                        "기준점키": origin_input_key(base),
                        "수집시각": now_iso(),
                    },
                    probe_results,
                )

    return (
        {
            **base,
            "출발위도": base_lat,
            "출발경도": base_lon,
            "도로보정_km": 0.0,
            "도로탐색상태": last_status,
            "기준점키": origin_input_key(base),
            "수집시각": now_iso(),
        },
        probe_results,
    )


def load_cached_origins(
    base_origins: pd.DataFrame,
    refresh: bool,
    cache_ttl_days: int = DEFAULT_ROUTE_CACHE_TTL_DAYS,
    reference_time: datetime | None = None,
) -> tuple[dict[str, dict], list[dict]]:
    if refresh or not ORIGIN_OUTPUT.exists():
        return {}, base_origins.to_dict("records")
    cached = read_csv(ORIGIN_OUTPUT)
    if "기준점키" not in cached.columns:
        return {}, base_origins.to_dict("records")
    cached_by_key = {str(row["시군구코드"]): row for row in cached.to_dict("records")}
    reused: dict[str, dict] = {}
    pending: list[dict] = []
    for base in base_origins.to_dict("records"):
        row = cached_by_key.get(str(base["시군구코드"]))
        if (
            row
            and row.get("기준점키") == origin_input_key(base)
            and row.get("도로탐색상태") == "성공"
            and is_cache_fresh(row.get("수집시각"), cache_ttl_days, reference_time)
        ):
            reused[str(base["시군구코드"])] = row
        else:
            pending.append(base)
    return reused, pending


def resolve_origins(
    base_origins: pd.DataFrame,
    polygons_by_key: dict[str, list],
    hospitals: pd.DataFrame,
    eligible: pd.DataFrame,
    api_key: str,
    refresh: bool,
    workers: int,
    cache_ttl_days: int = DEFAULT_ROUTE_CACHE_TTL_DAYS,
) -> tuple[pd.DataFrame, dict[str, dict]]:
    reused, pending = load_cached_origins(base_origins, refresh, cache_ttl_days)
    if pending and not api_key:
        raise RuntimeError("도로 탐색 가능한 지역 대표점을 만들려면 .env의 KAKAO_REST_API_KEY가 필요합니다.")

    probes: dict[str, dict] = {}
    resolved = dict(reused)
    if pending:
        hospitals_with_key = hospitals.assign(시군구코드=region_code(hospitals))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for base in pending:
                key = str(base["시군구코드"])
                local = hospitals_with_key[hospitals_with_key["시군구코드"] == key]
                future = executor.submit(
                    select_route_origin,
                    base,
                    eligible,
                    local,
                    polygons_by_key.get(key, []),
                    api_key,
                )
                futures[future] = key
            completed = 0
            for future in as_completed(futures):
                key = futures[future]
                origin, route_results = future.result()
                resolved[key] = origin
                probes.update(route_results)
                completed += 1
                if completed % 50 == 0 or completed == len(pending):
                    print(f"Kakao origin probe: {completed:,}/{len(pending):,}", flush=True)

    origins = pd.DataFrame(resolved.values()).sort_values("시군구코드").reset_index(drop=True)
    failures = origins.loc[origins["도로탐색상태"] != "성공", ["시군구코드", "도로탐색상태"]]
    if not failures.empty:
        details = ", ".join(f"{row.시군구코드}({row.도로탐색상태})" for row in failures.itertuples(index=False))
        raise RuntimeError(f"카카오 도로에 연결되는 지역 대표점을 찾지 못했습니다: {details}")
    save_csv(origins, ORIGIN_OUTPUT)
    return origins, probes


def build_task_frames(
    hospitals: pd.DataFrame,
    eligible: pd.DataFrame,
    origins: pd.DataFrame,
    max_candidates: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    hospitals_with_key = hospitals.assign(시군구코드=region_code(hospitals))
    candidate_rows = []
    hospital_rows = []
    shared_fields = [
        "시군구코드",
        "시도",
        "시군구",
        "기준위도",
        "기준경도",
        "출발위도",
        "출발경도",
        "중심점방법",
        "경계버전",
        "도로보정_km",
    ]
    for origin in origins.to_dict("records"):
        origin_lat = float(origin["출발위도"])
        origin_lon = float(origin["출발경도"])
        distances = haversine_distances(origin_lat, origin_lon, eligible)
        ranking = pd.DataFrame(
            {
                "eligible_position": np.arange(len(eligible), dtype=int),
                "직선거리_km": distances,
                "기관코드": eligible["기관코드"].astype("string").fillna("").to_numpy(),
            }
        )
        nearest_indices = ranking.sort_values(
            ["직선거리_km", "기관코드"],
            kind="mergesort",
        )["eligible_position"].to_numpy(dtype=int)
        if max_candidates is not None:
            nearest_indices = nearest_indices[: min(max_candidates, len(eligible))]
        common = {field: origin.get(field) for field in shared_fields}
        for rank, index in enumerate(nearest_indices, start=1):
            hospital = eligible.iloc[int(index)]
            destination_lat = float(hospital["위도"])
            destination_lon = float(hospital["경도"])
            candidate_rows.append(
                {
                    **common,
                    "후보순위": rank,
                    "기관코드": hospital["기관코드"],
                    "병원명": hospital["병원명"],
                    "도착위도": destination_lat,
                    "도착경도": destination_lon,
                    "직선거리_km": float(distances[int(index)]),
                    "경로우선순위": ROUTE_PRIORITY,
                    "경로요청키": route_request_key(origin_lat, origin_lon, destination_lat, destination_lon),
                }
            )

        local = hospitals_with_key[hospitals_with_key["시군구코드"] == origin["시군구코드"]]
        local_distances = haversine_distances(origin_lat, origin_lon, local)
        for position, (_, hospital) in enumerate(local.iterrows()):
            destination_lat = float(hospital["위도"])
            destination_lon = float(hospital["경도"])
            hospital_rows.append(
                {
                    **common,
                    "기관코드": hospital["기관코드"],
                    "병원명": hospital["병원명"],
                    "도착위도": destination_lat,
                    "도착경도": destination_lon,
                    "직선거리_km": float(local_distances[position]),
                    "경로우선순위": ROUTE_PRIORITY,
                    "경로요청키": route_request_key(origin_lat, origin_lon, destination_lat, destination_lon),
                }
            )
    return pd.DataFrame(candidate_rows), pd.DataFrame(hospital_rows)


def load_route_cache(
    refresh: bool,
    cache_ttl_days: int = DEFAULT_ROUTE_CACHE_TTL_DAYS,
    reference_time: datetime | None = None,
) -> dict[str, dict]:
    if refresh:
        return {}
    cache: dict[str, dict] = {}
    for path in (CANDIDATE_OUTPUT, HOSPITAL_OUTPUT):
        if not path.exists():
            continue
        frame = read_csv(path)
        if not {"경로요청키", *ROUTE_FIELDS}.issubset(frame.columns):
            continue
        for row in frame.to_dict("records"):
            if (
                is_success(row)
                and pd.notna(row.get("도로거리_km"))
                and pd.notna(row.get("예상시간_분"))
                and is_cache_fresh(row.get("수집시각"), cache_ttl_days, reference_time)
            ):
                cache[str(row["경로요청키"])] = {field: row.get(field) for field in ROUTE_FIELDS}
    return cache


def attach_route_results(frame: pd.DataFrame, cache: dict[str, dict]) -> pd.DataFrame:
    result = frame.copy()
    for field in ROUTE_FIELDS:
        result[field] = result["경로요청키"].map(lambda key: cache.get(str(key), {}).get(field, pd.NA))
    return result


def collect_route_batch(
    tasks: pd.DataFrame,
    cache: dict[str, dict],
    api_key: str,
    workers: int,
    api_calls_so_far: int = 0,
) -> tuple[int, int]:
    unique_tasks = tasks.drop_duplicates("경로요청키")
    cached_keys = unique_tasks["경로요청키"].astype(str).isin(cache)
    pending = unique_tasks.loc[~cached_keys].to_dict("records")
    if pending and not api_key:
        raise RuntimeError("캐시에 없는 카카오 경로를 수집하려면 .env의 KAKAO_REST_API_KEY가 필요합니다.")
    if api_calls_so_far + len(pending) > MAX_ROUTE_API_CALLS:
        raise RuntimeError(
            f"예상 카카오 호출 {api_calls_so_far + len(pending):,}건이 1회 안전 한도를 초과합니다."
        )

    if pending:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch_route, task, api_key): task for task in pending}
            completed = 0
            for future in as_completed(futures):
                task = futures[future]
                cache[str(task["경로요청키"])] = future.result()
                completed += 1
                if completed % 100 == 0 or completed == len(pending):
                    print(f"Kakao routes: {completed:,}/{len(pending):,}", flush=True)

    return int(cached_keys.sum()), len(pending)


def collect_routes(
    candidates: pd.DataFrame,
    hospital_routes: pd.DataFrame,
    probe_results: dict[str, dict],
    api_key: str,
    refresh: bool,
    workers: int,
    cache_ttl_days: int = DEFAULT_ROUTE_CACHE_TTL_DAYS,
) -> tuple[pd.DataFrame, pd.DataFrame, int, int]:
    cache = load_route_cache(refresh, cache_ttl_days)
    cache.update({key: value for key, value in probe_results.items() if is_success(value)})
    tasks = pd.concat([candidates, hospital_routes], ignore_index=True)
    cache_hits, api_calls = collect_route_batch(tasks, cache, api_key, workers)
    return (
        attach_route_results(candidates, cache),
        attach_route_results(hospital_routes, cache),
        cache_hits,
        api_calls,
    )


def _adaptive_expansion_indices(
    all_candidates: pd.DataFrame,
    selected_indices: set[int],
    cache: dict[str, dict],
) -> list[int]:
    evaluated = attach_route_results(all_candidates.loc[sorted(selected_indices)], cache)
    expansion: list[int] = []
    for region, region_candidates in all_candidates.groupby("시군구코드", sort=False):
        remaining = region_candidates.loc[~region_candidates.index.isin(selected_indices)]
        if remaining.empty:
            continue

        region_evaluated = evaluated[evaluated["시군구코드"] == region]
        successful = region_evaluated[region_evaluated.apply(is_success, axis=1)].copy()
        successful["도로거리_km"] = pd.to_numeric(successful["도로거리_km"], errors="coerce")
        successful = successful[
            successful["도로거리_km"].notna()
            & successful["도로거리_km"].ge(0)
            & np.isfinite(successful["도로거리_km"])
        ]
        best_road_distance = float(successful["도로거리_km"].min()) if not successful.empty else float("inf")

        for index, next_candidate in remaining.iterrows():
            next_straight_distance = float(pd.to_numeric(next_candidate["직선거리_km"], errors="raise"))
            # 도로 경로 길이는 두 좌표의 직선거리보다 짧을 수 없다. 따라서 이 하한이
            # 현재 최적 도로거리 이상인 이후 후보는 전역 최적을 갱신할 수 없다.
            if next_straight_distance >= best_road_distance:
                break

            expansion.append(int(index))
            request_key = str(next_candidate["경로요청키"])
            if request_key not in cache:
                # 첫 실행에서는 미캐시 후보를 한 번에 하나만 호출해 불필요한 호출을 막는다.
                break

            cached_result = cache[request_key]
            if is_success(cached_result):
                cached_distance = pd.to_numeric(cached_result.get("도로거리_km"), errors="coerce")
                if pd.notna(cached_distance) and np.isfinite(cached_distance) and float(cached_distance) >= 0:
                    best_road_distance = min(best_road_distance, float(cached_distance))
    return expansion


def assert_global_road_minimum(
    evaluated_candidates: pd.DataFrame,
    all_candidates: pd.DataFrame,
) -> None:
    evaluated_indices = set(int(index) for index in evaluated_candidates.index)
    for region, region_candidates in all_candidates.groupby("시군구코드", sort=False):
        evaluated = evaluated_candidates[evaluated_candidates["시군구코드"] == region].copy()
        road_distances = pd.to_numeric(evaluated["도로거리_km"], errors="coerce")
        status_success = evaluated.apply(is_success, axis=1)
        valid_success = status_success & road_distances.notna() & road_distances.ge(0) & np.isfinite(road_distances)
        successful = evaluated.loc[valid_success].copy()
        successful["도로거리_km"] = road_distances.loc[valid_success]
        if successful.empty:
            raise RuntimeError(f"카카오 도로거리로 산출되지 않은 지역: {region}")
        best_road_distance = float(successful["도로거리_km"].min())

        evaluated_straight = pd.to_numeric(evaluated["직선거리_km"], errors="raise")
        result_codes = pd.to_numeric(evaluated["경로결과코드"], errors="coerce")
        confirmed_no_route = (~status_success) & result_codes.isin(CONFIRMED_NO_ROUTE_RESULT_CODES)
        unresolved_better = evaluated_straight.lt(best_road_distance) & ~valid_success & ~confirmed_no_route
        if unresolved_better.any():
            unresolved = evaluated.loc[unresolved_better, ["기관코드", "경로상태"]]
            details = ", ".join(
                f"{row.기관코드}({row.경로상태})" for row in unresolved.itertuples(index=False)
            )
            raise RuntimeError(
                f"카카오 전역 최단거리를 확정할 수 없는 기술 실패가 있습니다: {region} - {details}"
            )

        remaining = region_candidates.loc[~region_candidates.index.isin(evaluated_indices)]
        if not remaining.empty:
            next_straight_lower_bound = float(pd.to_numeric(remaining["직선거리_km"], errors="raise").min())
            if next_straight_lower_bound < best_road_distance:
                raise RuntimeError(
                    f"카카오 전역 최단거리 증명이 완료되지 않은 지역: {region} "
                    f"(미평가 직선거리 하한={next_straight_lower_bound:.6f}, 현재 도로거리={best_road_distance:.6f})"
                )


def collect_adaptive_routes(
    all_candidates: pd.DataFrame,
    hospital_routes: pd.DataFrame,
    probe_results: dict[str, dict],
    api_key: str,
    refresh: bool,
    workers: int,
    initial_candidates: int,
    cache_ttl_days: int = DEFAULT_ROUTE_CACHE_TTL_DAYS,
) -> tuple[pd.DataFrame, pd.DataFrame, int, int]:
    if all_candidates.empty:
        raise RuntimeError("카카오 접근성 후보가 없습니다.")
    all_candidates = all_candidates.sort_values(["시군구코드", "후보순위"]).reset_index(drop=True)
    cache = load_route_cache(refresh, cache_ttl_days)
    cache.update({key: value for key, value in probe_results.items() if is_success(value)})

    selected_indices = set(
        int(index)
        for index in all_candidates.index[
            pd.to_numeric(all_candidates["후보순위"], errors="raise").le(initial_candidates)
        ]
    )
    initial_tasks = pd.concat(
        [all_candidates.loc[sorted(selected_indices)], hospital_routes],
        ignore_index=True,
    )
    cache_hits, api_calls = collect_route_batch(initial_tasks, cache, api_key, workers)

    while True:
        expansion_indices = _adaptive_expansion_indices(all_candidates, selected_indices, cache)
        if not expansion_indices:
            break
        selected_indices.update(expansion_indices)
        batch_hits, batch_calls = collect_route_batch(
            all_candidates.loc[expansion_indices],
            cache,
            api_key,
            workers,
            api_calls_so_far=api_calls,
        )
        cache_hits += batch_hits
        api_calls += batch_calls

    evaluated = attach_route_results(all_candidates.loc[sorted(selected_indices)], cache)
    assert_global_road_minimum(evaluated, all_candidates)
    return evaluated, attach_route_results(hospital_routes, cache), cache_hits, api_calls


def select_best_accessibility(candidates: pd.DataFrame, expected_regions: set[str]) -> pd.DataFrame:
    successful = candidates[candidates.apply(is_success, axis=1)].copy()
    successful["도로거리_km"] = pd.to_numeric(successful["도로거리_km"], errors="coerce")
    successful["예상시간_분"] = pd.to_numeric(successful["예상시간_분"], errors="coerce")
    successful = successful.dropna(subset=["도로거리_km", "예상시간_분"])
    covered = set(successful["시군구코드"])
    if covered != expected_regions:
        raise RuntimeError(f"카카오 도로거리로 산출되지 않은 지역: {sorted(expected_regions - covered)}")
    best_indices = successful.groupby("시군구코드")["도로거리_km"].idxmin()
    return successful.loc[best_indices].sort_values("시군구코드").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="카카오 자동차 경로 기반 지역·병원 접근거리를 수집합니다.")
    parser.add_argument("--refresh", action="store_true", help="좌표가 같아도 성공 경로 캐시를 무시하고 다시 수집")
    parser.add_argument("--workers", type=int, default=int(os.getenv("KAKAO_ROUTE_WORKERS", "4")))
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=DEFAULT_MAX_CANDIDATES,
        help="지역별 최초 경로 호출 후보 수(이후 직선거리 하한으로 필요한 후보만 자동 확장)",
    )
    parser.add_argument(
        "--cache-ttl-days",
        type=int,
        default=int(os.getenv("KAKAO_ROUTE_CACHE_TTL_DAYS", str(DEFAULT_ROUTE_CACHE_TTL_DAYS))),
        help="성공 경로 및 대표점 캐시 유효기간(일)",
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        raise ValueError("--workers는 1~8이어야 합니다.")
    if not 1 <= args.max_candidates <= 30:
        raise ValueError("--max-candidates는 1~30이어야 합니다.")
    if not 1 <= args.cache_ttl_days <= 3_650:
        raise ValueError("--cache-ttl-days는 1~3650이어야 합니다.")

    api_key = os.getenv("KAKAO_REST_API_KEY", "").strip()
    master = read_csv(MASTER)
    hospitals = master.dropna(subset=["위도", "경도"]).copy()
    hospitals["위도"] = pd.to_numeric(hospitals["위도"], errors="coerce")
    hospitals["경도"] = pd.to_numeric(hospitals["경도"], errors="coerce")
    if len(hospitals) != len(master) or hospitals[["위도", "경도"]].isna().any().any():
        raise RuntimeError("카카오 경로 수집 전에 병원 좌표 결측을 해소해야 합니다.")
    eligible = hospitals[hospitals["등급"].astype(str).str.contains("권역|지역응급의료센터", regex=True)].copy()
    if eligible.empty:
        raise RuntimeError("길찾기 목적지로 사용할 권역/지역응급의료센터가 없습니다.")

    base_origins, polygons_by_key = load_base_origins(hospitals)
    origins, probe_results = resolve_origins(
        base_origins,
        polygons_by_key,
        hospitals,
        eligible,
        api_key,
        args.refresh,
        args.workers,
        args.cache_ttl_days,
    )
    all_candidates, hospital_routes = build_task_frames(hospitals, eligible, origins)
    candidates, hospital_routes, cache_hits, api_calls = collect_adaptive_routes(
        all_candidates,
        hospital_routes,
        probe_results,
        api_key,
        args.refresh,
        args.workers,
        args.max_candidates,
        args.cache_ttl_days,
    )
    candidates = candidates.sort_values(["시군구코드", "후보순위"]).reset_index(drop=True)
    hospital_routes = hospital_routes.sort_values(["시군구코드", "기관코드"]).reset_index(drop=True)
    best = select_best_accessibility(candidates, set(region_code(hospitals)))

    save_csv(candidates, CANDIDATE_OUTPUT)
    save_csv(best, ACCESSIBILITY_OUTPUT)
    save_csv(hospital_routes, HOSPITAL_OUTPUT)
    hospital_successes = int(hospital_routes.apply(is_success, axis=1).sum())
    adjusted_origins = int(pd.to_numeric(origins["도로보정_km"], errors="coerce").fillna(0).gt(0.001).sum())
    print(
        "Saved Kakao routes: "
        f"regions={len(best):,}, hospitals={hospital_successes:,}/{len(hospital_routes):,}, "
        f"route_cache_hits={cache_hits:,}, route_api_calls={api_calls:,}, adjusted_origins={adjusted_origins:,}"
    )


if __name__ == "__main__":
    main()
