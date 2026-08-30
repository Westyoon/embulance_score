import json
import math
import os
import re
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from common import DATA_DIR, ROOT, read_csv
from part3_calculate_region_risk import RISK_BINS, RISK_GRADES, RISK_GRADE_NAMES

EXPECTED_INCHEON = {
    "28125": "제물포구",
    "28155": "영종구",
    "28177": "미추홀구",
    "28185": "연수구",
    "28200": "남동구",
    "28237": "부평구",
    "28245": "계양구",
    "28275": "서해구",
    "28290": "검단구",
    "28710": "강화군",
    "28720": "옹진군",
}
EXPECTED_NO_NEMC_REGIONS = {
    "강원특별자치도|고성군",
    "강원특별자치도|양양군",
    "강원특별자치도|인제군",
    "경기도|과천시",
    "경기도|의왕시",
    "경기도|하남시",
    "대구광역시|군위군",
    "부산광역시|강서구",
    "전북특별자치도|완주군",
    "충청남도|계룡시",
    "충청북도|증평군",
}
EXPECTED_BOUNDARY_COUNT = 256
EXPECTED_BOUNDARY_VERSION = "20260701"
EXPECTED_NEMC_HOSPITALS = 534
EXPECTED_NEMC_REGIONS = 219
MIN_LIVE_MATCHES = 373
MIN_HIRA_MATCHES = 400
EXPECTED_AGGREGATE_MAPPINGS = {
    "41111": ("경기도|수원시장안구", "경기도|수원시"),
    "41113": ("경기도|수원시권선구", "경기도|수원시"),
    "41115": ("경기도|수원시팔달구", "경기도|수원시"),
    "41117": ("경기도|수원시영통구", "경기도|수원시"),
    "41131": ("경기도|성남시수정구", "경기도|성남시"),
    "41133": ("경기도|성남시중원구", "경기도|성남시"),
    "41135": ("경기도|성남시분당구", "경기도|성남시"),
    "41171": ("경기도|안양시만안구", "경기도|안양시"),
    "41173": ("경기도|안양시동안구", "경기도|안양시"),
    "41192": ("경기도|부천시원미구", "경기도|부천시"),
    "41194": ("경기도|부천시소사구", "경기도|부천시"),
    "41196": ("경기도|부천시오정구", "경기도|부천시"),
    "41271": ("경기도|안산시상록구", "경기도|안산시"),
    "41273": ("경기도|안산시단원구", "경기도|안산시"),
    "41281": ("경기도|고양시덕양구", "경기도|고양시"),
    "41285": ("경기도|고양시일산동구", "경기도|고양시"),
    "41287": ("경기도|고양시일산서구", "경기도|고양시"),
    "41461": ("경기도|용인시처인구", "경기도|용인시"),
    "41463": ("경기도|용인시기흥구", "경기도|용인시"),
    "41465": ("경기도|용인시수지구", "경기도|용인시"),
    "41591": ("경기도|화성시만세구", "경기도|화성시"),
    "41593": ("경기도|화성시효행구", "경기도|화성시"),
    "41595": ("경기도|화성시병점구", "경기도|화성시"),
    "41597": ("경기도|화성시동탄구", "경기도|화성시"),
    "43111": ("충청북도|청주시상당구", "충청북도|청주시"),
    "43112": ("충청북도|청주시서원구", "충청북도|청주시"),
    "43113": ("충청북도|청주시흥덕구", "충청북도|청주시"),
    "43114": ("충청북도|청주시청원구", "충청북도|청주시"),
    "44131": ("충청남도|천안시동남구", "충청남도|천안시"),
    "44133": ("충청남도|천안시서북구", "충청남도|천안시"),
    "47111": ("경상북도|포항시남구", "경상북도|포항시"),
    "47113": ("경상북도|포항시북구", "경상북도|포항시"),
    "48121": ("경상남도|창원시의창구", "경상남도|창원시"),
    "48123": ("경상남도|창원시성산구", "경상남도|창원시"),
    "48125": ("경상남도|창원시마산합포구", "경상남도|창원시"),
    "48127": ("경상남도|창원시마산회원구", "경상남도|창원시"),
    "48129": ("경상남도|창원시진해구", "경상남도|창원시"),
    "52111": ("전북특별자치도|전주시완산구", "전북특별자치도|전주시"),
    "52113": ("전북특별자치도|전주시덕진구", "전북특별자치도|전주시"),
}


def fail(message: str) -> None:
    raise RuntimeError(message)


def require_unique(frame: pd.DataFrame, columns: list[str], name: str) -> None:
    if frame[columns].isna().any().any() or frame.duplicated(columns).any():
        fail(f"{name} 키가 비어 있거나 중복됩니다: {columns}")


def region_keys(frame: pd.DataFrame) -> set[str]:
    return set(frame["시도"].astype("string") + "|" + frame["시군구"].astype("string"))


def coordinate_pairs(value: object) -> Iterable[tuple[float, float]]:
    if (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        yield float(value[0]), float(value[1])
        return
    if isinstance(value, list):
        for child in value:
            yield from coordinate_pairs(child)


def validate_frontend_risk_scale() -> None:
    source = (ROOT / "src" / "lib" / "riskScale.js").read_text(encoding="utf-8")
    matches = re.findall(
        r'\{\s*label:\s*"([^"]+)".*?max:\s*(Infinity|\d+(?:\.\d+)?)\s*\}',
        source,
        flags=re.DOTALL,
    )
    if len(matches) != len(RISK_GRADE_NAMES):
        fail("프론트 위험등급 정의를 해석하지 못했습니다.")
    frontend_names = [label for label, _ in matches]
    frontend_maxima = [math.inf if maximum == "Infinity" else float(maximum) for _, maximum in matches]
    backend_maxima = [float(value) for value in RISK_BINS[1:]]
    if frontend_names != RISK_GRADE_NAMES or frontend_maxima != backend_maxima:
        fail(
            f"프론트·백엔드 위험등급 기준이 다릅니다: front={list(zip(frontend_names, frontend_maxima))}, "
            f"back={list(zip(RISK_GRADE_NAMES, backend_maxima))}"
        )


def validate_boundaries(risk_keys: set[str]) -> tuple[dict, int, int, list[str]]:
    geo_path = Path(os.getenv("BOUNDARY_FILE", ROOT / "src" / "data" / "koreaGeo.json")).resolve()
    geo = json.loads(geo_path.read_text(encoding="utf-8"))
    metadata = geo.get("metadata", {})
    if (
        geo.get("type") != "FeatureCollection"
        or metadata.get("level") != "sgg"
        or metadata.get("crs") != "EPSG:4326"
        or metadata.get("resolution") != "light"
        or metadata.get("version") != EXPECTED_BOUNDARY_VERSION
        or "CC-BY-4.0" not in str(metadata.get("license", ""))
        or "SGIS" not in str(metadata.get("attribution", ""))
    ):
        fail("경계 메타데이터·좌표계·라이선스 계약이 올바르지 않습니다.")

    features = geo.get("features", [])
    if len(features) != EXPECTED_BOUNDARY_COUNT:
        fail(f"시군구 경계 개수가 검토 기준과 다릅니다: expected={EXPECTED_BOUNDARY_COUNT}, actual={len(features)}")
    codes: list[str] = []
    incheon: dict[str, str] = {}
    all_points: list[tuple[float, float]] = []
    for feature in features:
        properties = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        code = str(properties.get("code", ""))
        sido_code = str(properties.get("sidoCode", ""))
        name = str(properties.get("name", ""))
        sido = str(properties.get("sido", ""))
        if (
            feature.get("type") != "Feature"
            or geometry.get("type") not in {"Polygon", "MultiPolygon"}
            or not re.fullmatch(r"\d{5}", code)
            or not re.fullmatch(r"\d{2}", sido_code)
            or not code.startswith(sido_code)
            or not name
            or not sido
        ):
            fail(f"경계 feature 구조가 올바르지 않습니다: {properties}")
        points = list(coordinate_pairs(geometry.get("coordinates")))
        if len(points) < 4 or not all(math.isfinite(value) for point in points for value in point):
            fail(f"경계 좌표가 비어 있거나 숫자가 아닙니다: {code} {name}")
        codes.append(code)
        all_points.extend(points)
        if sido == "인천광역시":
            incheon[code] = name
    if len(set(codes)) != len(codes):
        fail("시군구 경계 코드가 중복됩니다.")
    if incheon != EXPECTED_INCHEON or set(codes).intersection({"28110", "28140", "28260"}):
        fail(f"최신 인천 경계 코드·명칭 검증에 실패했습니다: {incheon}")
    longitudes = [point[0] for point in all_points]
    latitudes = [point[1] for point in all_points]
    if not (124 <= min(longitudes) <= max(longitudes) <= 132 and 33 <= min(latitudes) <= max(latitudes) <= 39):
        fail("경계 좌표가 대한민국 EPSG:4326 범위를 벗어납니다.")

    matched_data: set[str] = set()
    no_nemc_regions: list[str] = []
    aggregate_mappings: dict[str, tuple[str, str]] = {}
    for feature in features:
        properties = feature["properties"]
        geo_key = f"{properties['sido']}|{properties['name']}"
        if geo_key in risk_keys:
            matched_data.add(geo_key)
            continue
        match = re.match(r"^(.+?시)(.+구)$", properties["name"])
        parent_key = f"{properties['sido']}|{match.group(1)}" if match else None
        if parent_key and parent_key in risk_keys:
            matched_data.add(parent_key)
            aggregate_mappings[properties["code"]] = (geo_key, parent_key)
        else:
            no_nemc_regions.append(geo_key)

    unmatched_data = sorted(risk_keys - matched_data)
    if unmatched_data:
        fail(f"최신 경계에 연결되지 않는 분석 지역: {unmatched_data}")
    if set(no_nemc_regions) != EXPECTED_NO_NEMC_REGIONS:
        fail(
            "검토되지 않은 경계/NEMC 모집단 차이가 생겼습니다: "
            f"expected={sorted(EXPECTED_NO_NEMC_REGIONS)}, actual={sorted(no_nemc_regions)}"
        )
    if aggregate_mappings != EXPECTED_AGGREGATE_MAPPINGS:
        fail(
            "검토되지 않은 일반구/부모 시 집계 매핑 변경이 생겼습니다: "
            f"expected={EXPECTED_AGGREGATE_MAPPINGS}, actual={aggregate_mappings}"
        )
    direct_matches = len(features) - len(aggregate_mappings) - len(no_nemc_regions)
    return metadata, direct_matches, len(aggregate_mappings), sorted(no_nemc_regions)


def validate_kakao_routes(master: pd.DataFrame, master_region_keys: set[str]) -> tuple[int, int]:
    origins = read_csv(DATA_DIR / "region_route_origins.csv")
    candidates = read_csv(DATA_DIR / "kakao_route_candidates.csv")
    routes = read_csv(DATA_DIR / "kakao_route_accessibility.csv")
    hospital_routes = read_csv(DATA_DIR / "kakao_hospital_routes.csv")
    accessibility = read_csv(DATA_DIR / "accessibility_score.csv")

    require_unique(origins, ["시군구코드"], "카카오 지역 대표점")
    if set(origins["시군구코드"]) != master_region_keys:
        fail("카카오 지역 대표점이 NEMC 지역 모집단을 정확히 포함하지 않습니다.")
    required_origin_columns = {
        "기준위도", "기준경도", "출발위도", "출발경도", "중심점방법", "경계버전",
        "도로보정_km", "도로탐색상태", "기준점키", "수집시각",
    }
    if not required_origin_columns.issubset(origins.columns):
        fail(f"카카오 지역 대표점 필수 컬럼 누락: {sorted(required_origin_columns - set(origins.columns))}")
    origin_coordinates = origins[["기준위도", "기준경도", "출발위도", "출발경도", "도로보정_km"]].apply(
        pd.to_numeric, errors="coerce"
    )
    if (
        origin_coordinates.isna().any().any()
        or not origin_coordinates["기준위도"].between(33, 39).all()
        or not origin_coordinates["출발위도"].between(33, 39).all()
        or not origin_coordinates["기준경도"].between(124, 132).all()
        or not origin_coordinates["출발경도"].between(124, 132).all()
        or not origin_coordinates["도로보정_km"].ge(0).all()
        or not origins["도로탐색상태"].eq("성공").all()
        or not origins["경계버전"].astype("string").eq(EXPECTED_BOUNDARY_VERSION).all()
        or origins["중심점방법"].astype("string").str.contains("병원좌표평균").any()
        or not origins["기준점키"].astype("string").str.fullmatch(r"[0-9a-f]{64}").all()
    ):
        fail("카카오 지역 대표점의 좌표·경계버전·도로탐색 계약이 올바르지 않습니다.")

    required_route_columns = {
        "시군구코드", "기관코드", "병원명", "직선거리_km", "도로거리_km", "예상시간_분",
        "출발위도", "출발경도", "도착위도", "도착경도", "중심점방법", "경계버전",
        "경로우선순위", "경로결과코드", "경로상태", "경로요청키", "수집시각",
    }
    for name, frame in {
        "카카오 후보 경로": candidates,
        "카카오 최종 접근 경로": routes,
        "카카오 병원 경로": hospital_routes,
    }.items():
        if not required_route_columns.issubset(frame.columns):
            fail(f"{name} 필수 컬럼 누락: {sorted(required_route_columns - set(frame.columns))}")
        if (
            not frame["경계버전"].astype("string").eq(EXPECTED_BOUNDARY_VERSION).all()
            or not frame["경로우선순위"].eq("DISTANCE").all()
            or not frame["경로요청키"].astype("string").str.fullmatch(r"[0-9a-f]{64}").all()
        ):
            fail(f"{name}의 경계버전·우선순위·요청키 계약이 올바르지 않습니다.")
        straight = pd.to_numeric(frame["직선거리_km"], errors="coerce")
        if straight.isna().any() or not straight.ge(0).all():
            fail(f"{name}의 직선거리 감사값에 결측 또는 음수가 있습니다.")
        success = frame["경로상태"].isin(["성공", "성공:출도착5m이내"])
        road = pd.to_numeric(frame["도로거리_km"], errors="coerce")
        duration = pd.to_numeric(frame["예상시간_분"], errors="coerce")
        if road[success].isna().any() or duration[success].isna().any() or not road[success].ge(0).all() or not duration[success].ge(0).all():
            fail(f"{name} 성공 행의 도로거리 또는 예상시간이 올바르지 않습니다.")
        if frame.loc[~success, ["도로거리_km", "예상시간_분"]].notna().any().any():
            fail(f"{name} 실패 행에 카카오 도로거리처럼 보이는 값이 들어 있습니다.")
        codes = pd.to_numeric(frame.loc[success, "경로결과코드"], errors="coerce")
        if codes.isna().any() or not codes.isin([0, 104]).all():
            fail(f"{name} 성공 행의 Kakao result_code가 0/104가 아닙니다.")
        zero = road[success].eq(0) | duration[success].eq(0)
        if zero.any() and not codes[zero].eq(104).all():
            fail(f"{name}에서 result_code=104가 아닌 0 거리·시간이 발견됐습니다.")

    require_unique(candidates, ["시군구코드", "후보순위"], "카카오 후보 순위")
    candidate_counts = candidates.groupby("시군구코드").size()
    eligible = master.loc[
        master["등급"].astype(str).str.contains("권역|지역응급의료센터", regex=True)
    ].copy()
    eligible[["위도", "경도"]] = eligible[["위도", "경도"]].apply(pd.to_numeric, errors="coerce")
    if (
        eligible.empty
        or eligible[["위도", "경도"]].isna().any().any()
        or set(candidate_counts.index) != master_region_keys
        or not candidate_counts.between(1, len(eligible)).all()
    ):
        fail("카카오 후보 경로의 지역 포함 범위 또는 후보 개수가 잘못됐습니다.")

    master_codes = set(master["기관코드"].astype("string"))
    eligible_codes = set(eligible["기관코드"].astype("string"))
    if not set(candidates["기관코드"].astype("string")).issubset(eligible_codes):
        fail("카카오 접근성 후보에 권역·지역응급의료센터가 아닌 기관이 있습니다.")

    require_unique(routes, ["시군구코드"], "카카오 최종 접근 경로")
    if (
        set(routes["시군구코드"]) != master_region_keys
        or not routes["경로상태"].isin(["성공", "성공:출도착5m이내"]).all()
        or not set(routes["기관코드"].astype("string")).issubset(eligible_codes)
        or not set(routes["경로요청키"]).issubset(set(candidates["경로요청키"]))
    ):
        fail("카카오 최종 접근 경로의 모집단·상태·선정기관 계약이 올바르지 않습니다.")

    # 실제 호출 후보는 전체 센터를 직선거리로 정렬한 prefix여야 한다. 평가한
    # prefix 다음 센터의 직선거리 하한이 현재 최적 도로거리 이상일 때만 전국
    # 권역·지역응급의료센터 중 전역 최단임을 증명할 수 있다.
    origins_by_region = origins.set_index("시군구코드")
    routes_by_region = routes.set_index("시군구코드")
    terminal_no_route_codes = {1, 101, 102, 103, 105, 106, 107}
    earth_radius_km = 6371.0
    for region, region_candidates in candidates.groupby("시군구코드", sort=False):
        ordered = region_candidates.sort_values("후보순위").reset_index(drop=True)
        ranks = pd.to_numeric(ordered["후보순위"], errors="coerce")
        expected_ranks = np.arange(1, len(ordered) + 1)
        if ranks.isna().any() or not np.array_equal(ranks.to_numpy(dtype=float), expected_ranks):
            fail(f"카카오 후보 순위가 1부터 연속되지 않습니다: {region}")

        origin = origins_by_region.loc[region]
        origin_lat_value = float(origin["출발위도"])
        origin_lon_value = float(origin["출발경도"])
        actual_origins = ordered[["출발위도", "출발경도"]].apply(pd.to_numeric, errors="coerce")
        if actual_origins.isna().any().any() or not (
            np.allclose(actual_origins["출발위도"], origin_lat_value, rtol=0, atol=1e-9)
            and np.allclose(actual_origins["출발경도"], origin_lon_value, rtol=0, atol=1e-9)
        ):
            fail(f"카카오 후보의 출발점이 지역 대표점과 다릅니다: {region}")
        origin_lat = math.radians(origin_lat_value)
        origin_lon = math.radians(origin_lon_value)
        destination_lat = np.radians(eligible["위도"].to_numpy(dtype=float))
        destination_lon = np.radians(eligible["경도"].to_numpy(dtype=float))
        dlat = destination_lat - origin_lat
        dlon = destination_lon - origin_lon
        haversine_value = (
            np.sin(dlat / 2) ** 2
            + math.cos(origin_lat) * np.cos(destination_lat) * np.sin(dlon / 2) ** 2
        )
        straight_distances = earth_radius_km * 2 * np.arctan2(
            np.sqrt(np.clip(haversine_value, 0, 1)),
            np.sqrt(1 - np.clip(haversine_value, 0, 1)),
        )
        ranked = eligible[["기관코드", "위도", "경도"]].copy()
        ranked["직선거리_km"] = straight_distances
        ranked["기관코드"] = ranked["기관코드"].astype("string")
        ranked = ranked.sort_values(["직선거리_km", "기관코드"], kind="mergesort").reset_index(drop=True)
        expected_prefix = ranked.iloc[: len(ordered)]
        actual_codes = ordered["기관코드"].astype("string").tolist()
        if actual_codes != expected_prefix["기관코드"].tolist():
            fail(f"카카오 후보가 전체 센터 직선거리 정렬의 prefix와 다릅니다: {region}")
        actual_destinations = ordered[["도착위도", "도착경도"]].apply(pd.to_numeric, errors="coerce")
        if actual_destinations.isna().any().any() or not (
            np.allclose(actual_destinations["도착위도"], expected_prefix["위도"], rtol=0, atol=1e-9)
            and np.allclose(actual_destinations["도착경도"], expected_prefix["경도"], rtol=0, atol=1e-9)
        ):
            fail(f"카카오 후보의 목적지 좌표가 NEMC 센터 좌표와 다릅니다: {region}")
        actual_straight = pd.to_numeric(ordered["직선거리_km"], errors="coerce")
        if actual_straight.isna().any() or not np.allclose(
            actual_straight,
            expected_prefix["직선거리_km"],
            rtol=0,
            atol=1e-9,
        ):
            fail(f"카카오 후보 직선거리 감사값이 원천 좌표와 다릅니다: {region}")

        best_road_distance = float(pd.to_numeric(routes_by_region.loc[region, "도로거리_km"], errors="raise"))
        if len(ordered) < len(ranked):
            next_straight_lower_bound = float(ranked.iloc[len(ordered)]["직선거리_km"])
            if next_straight_lower_bound < best_road_distance:
                fail(
                    f"카카오 전역 최단거리 증명이 완료되지 않았습니다: {region} "
                    f"(다음 직선거리={next_straight_lower_bound:.6f}, 선택 도로거리={best_road_distance:.6f})"
                )
        success = ordered["경로상태"].isin(["성공", "성공:출도착5m이내"])
        result_codes = pd.to_numeric(ordered["경로결과코드"], errors="coerce")
        unresolved = (~success) & actual_straight.lt(best_road_distance) & ~result_codes.isin(terminal_no_route_codes)
        if unresolved.any():
            fail(f"전역 최단 후보 안에 확정되지 않은 카카오 경로 오류가 있습니다: {region}")

    successful_candidates = candidates[candidates["경로상태"].isin(["성공", "성공:출도착5m이내"])].copy()
    successful_candidates["도로거리_km"] = pd.to_numeric(successful_candidates["도로거리_km"], errors="coerce")
    expected_best_keys = set(
        successful_candidates.loc[
            successful_candidates.groupby("시군구코드")["도로거리_km"].idxmin(), "경로요청키"
        ]
    )
    if set(routes["경로요청키"]) != expected_best_keys:
        fail("카카오 최종 접근 경로가 후보 중 최단 도로거리를 선택하지 않았습니다.")

    require_unique(hospital_routes, ["기관코드"], "카카오 병원 경로")
    if len(hospital_routes) != EXPECTED_NEMC_HOSPITALS or set(hospital_routes["기관코드"].astype("string")) != master_codes:
        fail("카카오 병원 경로가 NEMC 534기관을 정확히 포함하지 않습니다.")
    master_region_series = master["시도"].astype("string") + "|" + master["시군구"].astype("string")
    master_region_by_code = dict(zip(master["기관코드"].astype("string"), master_region_series))
    actual_region_by_code = dict(zip(hospital_routes["기관코드"].astype("string"), hospital_routes["시군구코드"].astype("string")))
    if actual_region_by_code != master_region_by_code:
        fail("카카오 병원 경로의 기관별 소속 지역이 NEMC 마스터와 다릅니다.")
    hospital_success = hospital_routes["경로상태"].isin(["성공", "성공:출도착5m이내"])
    if int(hospital_success.sum()) < math.ceil(EXPECTED_NEMC_HOSPITALS * 0.95):
        fail(f"카카오 병원 경로 성공률이 95% 미만입니다: {int(hospital_success.sum())}/{len(hospital_routes)}")

    require_unique(accessibility, ["시군구코드"], "접근성 점수")
    required_accessibility_columns = {
        "최근접기관코드", "최근접병원", "직선거리_km", "도로거리_km", "예상시간_분", "접근거리_km",
        "중심점방법", "경계버전", "경로상태", "경로수집시각", "거리기준", "접근성점수",
    }
    if not required_accessibility_columns.issubset(accessibility.columns) or set(accessibility["시군구코드"]) != master_region_keys:
        fail("접근성 점수 파일의 카카오 스키마 또는 지역 모집단이 올바르지 않습니다.")
    joined = accessibility.merge(
        routes[["시군구코드", "기관코드", "도로거리_km", "예상시간_분"]],
        on="시군구코드",
        how="left",
        suffixes=("", "_route"),
    )
    if (
        not joined["최근접기관코드"].astype("string").equals(joined["기관코드"].astype("string"))
        or not np.allclose(pd.to_numeric(joined["도로거리_km"]), pd.to_numeric(joined["도로거리_km_route"]), rtol=0, atol=1e-9)
        or not np.allclose(pd.to_numeric(joined["예상시간_분"]), pd.to_numeric(joined["예상시간_분_route"]), rtol=0, atol=1e-9)
        or not accessibility["거리기준"].eq("카카오자동차최단거리경로").all()
    ):
        fail("접근성 점수가 카카오 최종 경로와 일치하지 않습니다.")
    distances = pd.to_numeric(accessibility["도로거리_km"], errors="coerce")
    low, high = distances.quantile([0.05, 0.95])
    expected_scores = pd.Series(50.0, index=distances.index) if high <= low else (distances.clip(low, high) - low) / (high - low) * 100
    actual_scores = pd.to_numeric(accessibility["접근성점수"], errors="coerce")
    if actual_scores.isna().any() or not np.allclose(actual_scores, expected_scores, rtol=0, atol=1e-8):
        fail("접근성점수가 카카오 도로거리 P5~P95 산식과 일치하지 않습니다.")

    regression = read_csv(DATA_DIR / "regression_result.csv")
    if not regression.empty:
        variables = set(regression["변수명"])
        if "도로거리_km" not in variables or "직선거리_km" in variables:
            fail("회귀분석이 카카오 도로거리 대신 직선거리를 사용하고 있습니다.")
    return int(hospital_success.sum()), len(hospital_routes)


def main() -> None:
    master = read_csv(DATA_DIR / "hospital_master.csv")
    beds = read_csv(DATA_DIR / "bed_status.csv")
    population = read_csv(DATA_DIR / "population_source.csv")
    hira_detail = read_csv(DATA_DIR / "hira_doctor_matches.csv")
    doctors = read_csv(DATA_DIR / "doctor_source.csv")
    risk = read_csv(DATA_DIR / "region_risk_final.csv")

    require_unique(master, ["기관코드"], "NEMC 병원 마스터")
    if master[["시도", "시군구"]].isna().any().any():
        fail("NEMC 병원 마스터에 지역 결측이 있습니다.")
    region_override_path = DATA_DIR / "hospital_region_overrides.csv"
    if region_override_path.exists():
        region_overrides = read_csv(region_override_path)
        required_override_columns = {
            "기관코드",
            "병원명",
            "원본시도",
            "원본시군구",
            "시도",
            "시군구",
            "근거URL",
            "확인일",
        }
        if not required_override_columns.issubset(region_overrides.columns):
            fail(f"병원 지역 보정표 필수 컬럼 누락: {sorted(required_override_columns - set(region_overrides.columns))}")
        require_unique(region_overrides, ["기관코드"], "병원 지역 보정")
        for column in required_override_columns:
            region_overrides[column] = region_overrides[column].astype("string").str.strip()
        if region_overrides[list(required_override_columns)].isna().any().any() or (
            region_overrides[list(required_override_columns)] == ""
        ).any().any():
            fail("병원 지역 보정표의 보정 전·후 지역 또는 근거가 비어 있습니다.")
        if (
            region_overrides["원본시도"].eq(region_overrides["시도"])
            & region_overrides["원본시군구"].eq(region_overrides["시군구"])
        ).any():
            fail("병원 지역 보정표에 보정 전후 지역이 같은 행이 있습니다.")
        if (
            not region_overrides["근거URL"].str.fullmatch(r"https?://\S+").all()
            or pd.to_datetime(region_overrides["확인일"], format="%Y-%m-%d", errors="coerce").isna().any()
        ):
            fail("병원 지역 보정표의 근거 URL 또는 확인일 형식이 올바르지 않습니다.")
        joined_overrides = region_overrides.merge(
            master[["기관코드", "병원명", "주소", "시도", "시군구"]],
            on="기관코드",
            how="left",
            suffixes=("_override", "_master"),
            validate="one_to_one",
        )
        address_parts = joined_overrides["주소"].fillna("").astype("string").str.strip().str.split()
        address_province = address_parts.str[0].fillna("")
        address_district = address_parts.str[1].fillna("")
        address_district = address_district.mask(address_province.eq("세종특별자치시"), "세종시")
        if (
            joined_overrides[["병원명_master", "시도_master", "시군구_master"]].isna().any().any()
            or not joined_overrides["병원명_override"].eq(joined_overrides["병원명_master"].astype("string").str.strip()).all()
            or not joined_overrides["시도_override"].eq(joined_overrides["시도_master"]).all()
            or not joined_overrides["시군구_override"].eq(joined_overrides["시군구_master"]).all()
            or not joined_overrides["원본시도"].eq(address_province).all()
            or not joined_overrides["원본시군구"].eq(address_district).all()
        ):
            fail("병원 지역 보정표가 현재 NEMC 마스터의 기관명·원본 주소·보정 후 지역과 일치하지 않습니다.")
    coordinates = master[["위도", "경도"]].apply(pd.to_numeric, errors="coerce")
    if (
        coordinates.isna().any().any()
        or not coordinates["위도"].between(33, 39).all()
        or not coordinates["경도"].between(124, 132).all()
    ):
        fail("NEMC 병원 좌표가 비어 있거나 대한민국 범위를 벗어납니다.")
    require_unique(beds, ["기관코드"], "NEMC 병상")
    if set(beds["기관코드"]) != set(master["기관코드"]):
        fail("병상 데이터가 NEMC 기준 모집단과 일치하지 않습니다.")

    master_region_keys = region_keys(master)
    if len(master) != EXPECTED_NEMC_HOSPITALS or len(master_region_keys) != EXPECTED_NEMC_REGIONS:
        fail(
            "NEMC 모집단이 검토 기준과 달라졌습니다: "
            f"hospitals={len(master)}, regions={len(master_region_keys)}"
        )
    kakao_hospital_success, kakao_hospital_total = validate_kakao_routes(master, master_region_keys)
    live_matches = int(beds[["가용병상", "전체병상", "API기준시각"]].notna().any(axis=1).sum())
    if live_matches < MIN_LIVE_MATCHES:
        fail(f"실시간 병상 응답 기관 수가 검토 기준보다 적습니다: {live_matches} < {MIN_LIVE_MATCHES}")
    require_unique(population, ["시도", "시군구"], "인구 지역")
    population_keys = region_keys(population)
    population_values = pd.to_numeric(population["인구"], errors="coerce")
    population_codes = population["행정코드"].astype("string")
    population_periods = population["기준연월"].astype("string").dropna().unique().tolist()
    expected_population_period = os.getenv("PIPELINE_POPULATION_PERIOD", "").strip()
    local_mois_periods = sorted(
        match.group(1)
        for path in DATA_DIR.glob("mois_population_*.csv")
        if (match := re.search(r"(\d{6})", path.stem))
    )
    if (
        population_keys != master_region_keys
        or population_values.isna().any()
        or not population_values.gt(0).all()
        or not population_codes.str.fullmatch(r"\d{5}").all()
        or population_codes.duplicated().any()
        or len(population_periods) != 1
        or (
            expected_population_period
            and population_periods[0] != expected_population_period
        )
        or (
            not expected_population_period
            and local_mois_periods
            and population_periods[0] != local_mois_periods[-1]
        )
        or not (DATA_DIR / f"mois_population_{population_periods[0]}.csv").exists()
    ):
        fail("인구 데이터의 모집단·값·행정코드·기준연월 계약이 올바르지 않습니다.")

    require_unique(hira_detail, ["기관코드"], "HIRA 기관 연결")
    if set(hira_detail["기관코드"]) != set(master["기관코드"]):
        fail("HIRA 기관 연결 데이터가 NEMC 기관 모집단을 보존하지 못합니다.")
    matched_hira = hira_detail["매칭상태"].isin(["자동매칭", "수동검증"])
    matched_identifiers = hira_detail.loc[matched_hira, "암호화요양기호"]
    if matched_identifiers.isna().any() or matched_identifiers.duplicated().any():
        fail("매칭된 HIRA 요양기관 식별자가 비어 있거나 중복됩니다.")
    if int(matched_hira.sum()) > len(master):
        fail("HIRA 보강 모집단이 NEMC 기준 모집단보다 커졌습니다. 기준 모집단을 재검토하세요.")
    if int(matched_hira.sum()) < MIN_HIRA_MATCHES:
        fail(f"HIRA 매칭 수가 검토 기준보다 적습니다: {int(matched_hira.sum())} < {MIN_HIRA_MATCHES}")
    no_search = read_csv(DATA_DIR / "hira_no_search_results.csv")
    other_review = read_csv(DATA_DIR / "hira_low_similarity.csv")
    require_unique(no_search, ["기관코드"], "HIRA 검색결과 없음 검토목록")
    require_unique(other_review, ["기관코드"], "HIRA 기타 수동검토 목록")
    no_search_codes = set(no_search["기관코드"].astype("string"))
    other_review_codes = set(other_review["기관코드"].astype("string"))
    unmatched_codes = set(hira_detail.loc[~matched_hira, "기관코드"].astype("string"))
    if (
        no_search_codes & other_review_codes
        or (no_search_codes | other_review_codes) != unmatched_codes
        or not no_search["매칭상태"].eq("미매칭").all()
        or other_review["매칭상태"].isin(["자동매칭", "수동검증", "미매칭"]).any()
    ):
        fail("HIRA 수동검토 큐가 최신 기관 매칭 결과의 미매칭 집합과 일치하지 않습니다.")
    require_unique(doctors, ["시도", "시군구"], "HIRA 지역 집계")

    require_unique(risk, ["시군구코드"], "지역 위험도")
    doctor_keys = region_keys(doctors)
    risk_keys = set(risk["시군구코드"])
    if doctor_keys != master_region_keys or risk_keys != master_region_keys:
        fail("HIRA 보강 또는 위험도 산출 과정에서 NEMC 지역 모집단이 줄었습니다.")
    validate_frontend_risk_scale()
    complete = risk["regionRisk"].notna()
    if not risk.loc[complete, "산출상태"].eq("완료").all() or not risk.loc[~complete, "산출상태"].eq("원천데이터부족").all():
        fail("regionRisk 결측 여부와 산출상태가 일치하지 않습니다.")
    numeric_risk = pd.to_numeric(risk.loc[complete, "regionRisk"], errors="coerce")
    if numeric_risk.isna().any() or not numeric_risk.between(0, 100).all():
        fail("완료된 regionRisk가 0~100 숫자가 아닙니다.")
    expected_grades = pd.cut(
        numeric_risk, RISK_BINS, labels=RISK_GRADES, right=True, include_lowest=True
    ).astype("int64")
    expected_names = pd.cut(
        numeric_risk, RISK_BINS, labels=RISK_GRADE_NAMES, right=True, include_lowest=True
    ).astype("string")
    actual_grades = pd.to_numeric(risk.loc[complete, "위험등급"]).astype("int64")
    actual_names = risk.loc[complete, "위험등급명"].astype("string")
    if (
        not expected_grades.reset_index(drop=True).equals(actual_grades.reset_index(drop=True))
        or not expected_names.reset_index(drop=True).equals(actual_names.reset_index(drop=True))
        or risk.loc[~complete, ["위험등급", "위험등급명"]].notna().any().any()
    ):
        fail("백엔드 위험등급 산출물이 공통 기준과 일치하지 않습니다.")

    metadata, direct_matches, aggregate_count, no_nemc_regions = validate_boundaries(risk_keys)
    valid_beds = int(pd.to_numeric(beds["포화율"], errors="coerce").notna().sum())
    print(f"NEMC base population: hospitals={len(master):,}, regions={len(master_region_keys):,}")
    print(f"HIRA enrichment: matched={int(matched_hira.sum()):,}, unmatched={int((~matched_hira).sum()):,}")
    print(f"Usable data: beds={valid_beds:,}, population={len(population):,}, risk={int(complete.sum()):,}")
    print(f"Kakao road routes: regions={EXPECTED_NEMC_REGIONS:,}, hospitals={kakao_hospital_success:,}/{kakao_hospital_total:,}")
    print(f"Boundary version={metadata.get('version')}, polygons={direct_matches + aggregate_count + len(no_nemc_regions):,}")
    print(f"Boundary matches: direct={direct_matches:,}, aggregate={aggregate_count:,}")
    print(f"Latest boundaries without NEMC hospitals ({len(no_nemc_regions)}): {', '.join(no_nemc_regions)}")


if __name__ == "__main__":
    main()
