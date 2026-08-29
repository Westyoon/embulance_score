import json
import math
import os
import re
from collections.abc import Iterable
from pathlib import Path

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
    print(f"Boundary version={metadata.get('version')}, polygons={direct_matches + aggregate_count + len(no_nemc_regions):,}")
    print(f"Boundary matches: direct={direct_matches:,}, aggregate={aggregate_count:,}")
    print(f"Latest boundaries without NEMC hospitals ({len(no_nemc_regions)}): {', '.join(no_nemc_regions)}")


if __name__ == "__main__":
    main()
