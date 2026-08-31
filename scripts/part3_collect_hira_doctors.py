import argparse
import json
import math
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
import requests
from scipy.optimize import linear_sum_assignment

from common import DATA_DIR, read_csv, save_csv, save_json

MASTER = DATA_DIR / "hospital_master.csv"
DETAIL_OUTPUT = DATA_DIR / "hira_doctor_matches.csv"
OUTPUT = DATA_DIR / "doctor_source.csv"
OVERRIDES = DATA_DIR / "hira_match_overrides.csv"
EXCLUSIONS = DATA_DIR / "hira_match_exclusions.csv"
NO_SEARCH_OUTPUT = DATA_DIR / "hira_no_search_results.csv"
REVIEW_OUTPUT = DATA_DIR / "hira_low_similarity.csv"
CANDIDATE_OUTPUT = DATA_DIR / "hira_match_candidates.csv"
CATALOG_MANIFEST_OUTPUT = DATA_DIR / "hira_catalog_manifest.json"
HOSPITAL_URL = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
SPECIALIST_URL = "https://apis.data.go.kr/B551182/MadmDtlInfoService2.8/getSpcSbjtSdrInfo2.8"
MATCHED_STATES = {"자동매칭", "수동검증"}
REVIEW_STATES = {
    "미매칭",
    "낮은유사도",
    "후보모호",
    "식별자충돌검토필요",
    "중복식별자검토필요",
    "HIRA원천불일치",
}
MIN_HIRA_MATCHES = 517
MIN_USABLE_REGIONS = 218
TARGET_USABLE_REGIONS = 219
DEFAULT_WORKERS = 8
MAX_WORKERS = 16
DEFAULT_CATALOG_PAGE_SIZE = 1000
MAX_CATALOG_PAGE_SIZE = 1000
AUTO_MATCH_THRESHOLD = 0.84
AUTO_MATCH_MARGIN = 0.05
MAX_ASSIGNMENT_CANDIDATES = 20
MAX_REVIEW_CANDIDATES = 5
MATCH_LOGIC_VERSION = "hira-catalog-v3"
MAX_EXCLUSION_AGE_DAYS = 30
REQUEST_ATTEMPTS = 4
REQUEST_TIMEOUT = (10, 60)
RETRYABLE_HTTP_STATUSES = {429, *range(500, 600)}
MAX_RETRY_DELAY_SECONDS = 60.0
_thread_local = threading.local()

PROVINCE_ALIASES = {
    "강원특별자치도": {"강원특별자치도", "강원도"},
    "전북특별자치도": {"전북특별자치도", "전라북도"},
    "전남광주통합특별시": {"전남광주통합특별시", "광주광역시", "전라남도"},
}

REQUIRED_OVERRIDE_COLUMNS = [
    "기관코드",
    "HIRA병원명",
    "HIRA주소",
    "암호화요양기호",
    "응급의학과전문의수",
    "근거URL",
    "확인일",
]
REQUIRED_EXCLUSION_COLUMNS = [
    "기관코드",
    "병원명",
    "사유코드",
    "사유",
    "확인요양기호",
    "근거URL",
    "확인일",
]
ALLOWED_EXCLUSION_REASONS = {"HIRA_SOURCE_NOT_FOUND"}
HIRA_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9_-]{80}")


class HiraRequestError(RuntimeError):
    """A deliberately redacted HIRA transport error."""


def _http_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session
    return session


def _retry_delay(response: requests.Response | None, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After", "") if response is not None else ""
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), MAX_RETRY_DELAY_SECONDS)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
                return min(max(seconds, 0.0), MAX_RETRY_DELAY_SECONDS)
            except (TypeError, ValueError, OverflowError):
                pass
    return min(0.75 * (2**attempt), MAX_RETRY_DELAY_SECONDS)


def hira_get(url: str, *, params: dict, attempts: int = REQUEST_ATTEMPTS) -> requests.Response:
    """GET a HIRA endpoint with bounded retries and redacted failures."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    last_failure = "Unknown"
    for attempt in range(attempts):
        response = None
        try:
            response = _http_session().get(url, params=params, timeout=REQUEST_TIMEOUT)
        except (requests.ReadTimeout, requests.ConnectionError) as exc:
            last_failure = type(exc).__name__
            if attempt + 1 >= attempts:
                break
            time.sleep(_retry_delay(None, attempt))
            continue
        except requests.RequestException as exc:
            raise HiraRequestError(f"HIRA API 요청 실패({type(exc).__name__})") from None

        if 200 <= response.status_code < 300:
            return response

        last_failure = f"HTTP {response.status_code}"
        if response.status_code not in RETRYABLE_HTTP_STATUSES or attempt + 1 >= attempts:
            response.close()
            break
        delay = _retry_delay(response, attempt)
        response.close()
        time.sleep(delay)

    raise HiraRequestError(f"HIRA API 요청 실패({last_failure}, attempts={attempts})") from None


def worker_count(value: str) -> int:
    try:
        workers = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("workers must be an integer from 1 to 16") from exc
    if not 1 <= workers <= MAX_WORKERS:
        raise argparse.ArgumentTypeError("workers must be from 1 to 16")
    return workers


def hira_key() -> str:
    value = os.getenv("HIRA_API_KEY", "").strip()
    if not value:
        raise RuntimeError("환경변수 또는 .env에 HIRA_API_KEY를 설정하세요.")
    return unquote(value)


def items(content: bytes) -> list[dict]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise RuntimeError("HIRA API 응답 형식을 해석할 수 없습니다.") from None
    result_code = root.findtext(".//resultCode")
    if result_code != "00":
        safe_code = re.sub(r"[^0-9A-Za-z_-]", "", str(result_code or "unknown"))[:20]
        raise RuntimeError(f"HIRA API 응답 오류(resultCode={safe_code})")
    return [{child.tag: child.text for child in item} for item in root.findall(".//item")]


def response_total_count(content: bytes) -> int:
    try:
        root = ET.fromstring(content)
        total_count = int(root.findtext(".//totalCount") or 0)
    except (ET.ParseError, TypeError, ValueError):
        raise RuntimeError("HIRA API 전체 건수를 해석할 수 없습니다.") from None
    if total_count < 0:
        raise RuntimeError("HIRA API 전체 건수가 올바르지 않습니다.")
    return total_count


def catalog_page_size(value: str) -> int:
    try:
        page_size = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("catalog page size must be an integer") from exc
    if not 1 <= page_size <= MAX_CATALOG_PAGE_SIZE:
        raise argparse.ArgumentTypeError(
            f"catalog page size must be from 1 to {MAX_CATALOG_PAGE_SIZE}"
        )
    return page_size


def fetch_catalog_page(page_no: int, page_size: int, key: str) -> tuple[list[dict], int]:
    response = hira_get(
        HOSPITAL_URL,
        params={
            "serviceKey": key,
            "pageNo": page_no,
            "numOfRows": page_size,
        },
    )
    try:
        content = response.content
    finally:
        response.close()
    return items(content), response_total_count(content)


def fetch_hira_catalog(key: str, *, workers: int, page_size: int) -> list[dict]:
    first_page = []
    total_count = 0
    for attempt in range(REQUEST_ATTEMPTS):
        first_page, total_count = fetch_catalog_page(1, page_size, key)
        if total_count > 0:
            break
        if attempt + 1 < REQUEST_ATTEMPTS:
            time.sleep(_retry_delay(None, attempt))
    if total_count == 0:
        raise RuntimeError(
            f"HIRA 병원 전체 목록이 {REQUEST_ATTEMPTS}회 연속 비어 있습니다."
        )
    page_count = math.ceil(total_count / page_size)
    pages = {1: first_page}
    failures = []
    if page_count > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(fetch_catalog_page, page_no, page_size, key): page_no
                for page_no in range(2, page_count + 1)
            }
            completed = 1
            for future in as_completed(futures):
                page_no = futures[future]
                try:
                    rows, reported_total = future.result()
                    if reported_total != total_count:
                        raise RuntimeError(
                            f"전체 건수가 수집 중 변경됐습니다({reported_total} != {total_count})"
                        )
                    pages[page_no] = rows
                except Exception as exc:
                    failures.append(f"page={page_no}: {exc}")
                completed += 1
                if completed % 10 == 0 or completed == page_count:
                    print(
                        f"HIRA catalog pages: {completed:,}/{page_count:,}; "
                        f"failures={len(failures):,}",
                        flush=True,
                    )

    if failures:
        preview = "\n".join(f"  {message}" for message in failures[:10])
        raise RuntimeError(
            f"HIRA 병원 전체 목록 {len(failures)}개 페이지 수집에 실패했습니다.\n{preview}"
        )

    catalog = [row for page_no in sorted(pages) for row in pages[page_no]]
    if len(catalog) != total_count:
        raise RuntimeError(
            "HIRA 병원 전체 목록이 일부만 수집됐습니다: "
            f"rows={len(catalog)}, expected={total_count}"
        )
    identifiers = [str(row.get("ykiho") or "").strip() for row in catalog]
    if any(not identifier for identifier in identifiers) or len(identifiers) != len(set(identifiers)):
        raise RuntimeError("HIRA 병원 전체 목록의 요양기호가 비어 있거나 중복됩니다.")
    return catalog


@lru_cache(maxsize=200_000)
def normalize_name(value: str) -> str:
    value = re.sub(r"\([^)]*\)|（[^）]*）", "", str(value or ""))
    value = re.sub(
        r"의료법인|재단법인|학교법인|사회복지법인|사단법인|"
        r"의료재단|교육재단|사회복지재단|학원|병원|의원|대학교",
        "",
        value,
    )
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value).lower()


@lru_cache(maxsize=200_000)
def normalize_address(value: str) -> str:
    value = re.sub(r"\([^)]*\)|（[^）]*）", "", str(value or ""))
    replacements = {
        "강원도": "강원특별자치도",
        "전라북도": "전북특별자치도",
        "전라남도": "전남광주통합특별시",
        "광주광역시": "전남광주통합특별시",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value).lower()


@lru_cache(maxsize=200_000)
def road_address_key(value: str) -> str:
    """Return the road name and primary building number for address identity checks."""
    value = re.sub(r"\([^)]*\)|（[^）]*）", "", str(value or ""))
    for old, new in {
        "강원도": "강원특별자치도",
        "전라북도": "전북특별자치도",
        "전라남도": "전남광주통합특별시",
        "광주광역시": "전남광주통합특별시",
    }.items():
        value = value.replace(old, new)
    value = re.sub(r"(?<=\d)-0(?=\D|$)", "", value)
    match = re.search(r"(.+?(?:대로|로|길))\s*(\d+(?:-\d+)?)", value)
    if not match:
        return ""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", "".join(match.groups())).lower()


@lru_cache(maxsize=200_000)
def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("82") and len(digits) > 10:
        digits = "0" + digits[2:]
    return digits[-8:] if len(digits) >= 8 else digits


@lru_cache(maxsize=200_000)
def facility_type(name: str, class_name: str = "") -> str:
    text = re.sub(r"\s+", "", f"{name or ''}{class_name or ''}")
    if "치과병원" in text:
        return "치과병원"
    if "한방병원" in text:
        return "한방병원"
    if "요양병원" in text:
        return "요양병원"
    if "치과의원" in text:
        return "치과의원"
    if "한의원" in text:
        return "한의원"
    if "보건소" in text or "보건지소" in text:
        return "보건기관"
    if "의원" in text:
        return "의원"
    return "병원"


def _facility_matches(target: dict, candidate: dict) -> bool:
    target_type = facility_type(str(target.get("병원명") or ""))
    candidate_type = facility_type(
        str(candidate.get("yadmNm") or ""),
        str(candidate.get("clCdNm") or ""),
    )
    return target_type == candidate_type


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _distance_km(target: dict, candidate: dict) -> float | None:
    latitude = _number(target.get("위도"))
    longitude = _number(target.get("경도"))
    candidate_latitude = _number(candidate.get("YPos"))
    candidate_longitude = _number(candidate.get("XPos"))
    if None in {latitude, longitude, candidate_latitude, candidate_longitude}:
        return None
    latitude_1, latitude_2 = math.radians(latitude), math.radians(candidate_latitude)
    delta_latitude = latitude_2 - latitude_1
    delta_longitude = math.radians(candidate_longitude - longitude)
    haversine = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_1) * math.cos(latitude_2) * math.sin(delta_longitude / 2) ** 2
    )
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(haversine)))


def _province_aliases(province: str) -> set[str]:
    province = str(province or "").strip()
    return PROVINCE_ALIASES.get(province, {province}) - {""}


def _province_matches(target: dict, candidate: dict) -> bool:
    candidate_region = " ".join(
        str(candidate.get(column) or "")
        for column in ["sidoCdNm", "addr"]
    )
    return any(alias in candidate_region for alias in _province_aliases(target.get("시도", "")))


def _district_matches(target: dict, candidate: dict) -> bool:
    district = str(target.get("시군구") or "").strip()
    if not district:
        return False
    candidate_region = " ".join(
        str(candidate.get(column) or "")
        for column in ["sgguCdNm", "addr"]
    )
    region_tokens = {
        token
        for token in re.split(r"[\s,()]+", candidate_region)
        if token
    }
    if district in region_tokens:
        return True
    # HIRA 주소가 행정구역 개편 전 명칭인 경우에도 병원 정체성의 다른 신호로
    # 판단할 수 있게 하되, 지역 일치 가점은 주지 않는다.
    district_aliases = {
        "서해구": {"서구"},
        "영종구": {"중구"},
        "세종시": {"세종특별자치시"},
    }
    return bool(region_tokens & district_aliases.get(district, set()))


def candidate_evidence(target: dict, candidate: dict) -> dict:
    """Return auditable identity signals without mutating either input."""
    target_name = normalize_name(target.get("병원명", ""))
    candidate_name = normalize_name(candidate.get("yadmNm", ""))
    name_similarity = (
        SequenceMatcher(None, target_name, candidate_name).ratio()
        if target_name and candidate_name
        else 0.0
    )
    target_address = normalize_address(target.get("주소", ""))
    candidate_address = normalize_address(candidate.get("addr", ""))
    address_similarity = 0.0
    if target_address and candidate_address:
        address_similarity = SequenceMatcher(None, target_address, candidate_address).ratio()
    target_phone = normalize_phone(target.get("전화", ""))
    candidate_phone = normalize_phone(candidate.get("telno", ""))
    shorter_name = min((target_name, candidate_name), key=len, default="")
    name_contains = bool(
        len(shorter_name) >= 2
        and (target_name in candidate_name or candidate_name in target_name)
    )
    return {
        "name_similarity": name_similarity,
        "name_exact": bool(target_name and target_name == candidate_name),
        "name_contains": name_contains,
        "address_similarity": address_similarity,
        "address_exact": bool(target_address and target_address == candidate_address),
        "road_address_matches": bool(
            road_address_key(target.get("주소", ""))
            and road_address_key(target.get("주소", ""))
            == road_address_key(candidate.get("addr", ""))
        ),
        "phone_matches": bool(target_phone and target_phone == candidate_phone),
        "distance_km": _distance_km(target, candidate),
        "district_matches": _district_matches(target, candidate),
        "province_matches": _province_matches(target, candidate),
        "facility_matches": _facility_matches(target, candidate),
        "target_facility_type": facility_type(str(target.get("병원명") or "")),
        "candidate_facility_type": facility_type(
            str(candidate.get("yadmNm") or ""),
            str(candidate.get("clCdNm") or ""),
        ),
    }


def score_candidate(target: dict, candidate: dict) -> float:
    """Score one NEMC-HIRA identity pair without mutating either input."""
    evidence = candidate_evidence(target, candidate)
    score = 0.40 * evidence["name_similarity"]
    if evidence["name_exact"]:
        score += 0.17
    elif evidence["name_contains"]:
        score += 0.12

    score += 0.14 * evidence["address_similarity"]
    if evidence["road_address_matches"]:
        score += 0.16
    if evidence["address_exact"]:
        score += 0.12
    if evidence["phone_matches"]:
        score += 0.10

    distance = evidence["distance_km"]
    if distance is not None:
        if distance <= 0.25:
            score += 0.18
        elif distance <= 0.75:
            score += 0.15
        elif distance <= 2.0:
            score += 0.10
        elif distance <= 5.0:
            score += 0.05

    if evidence["district_matches"]:
        score += 0.05
    elif evidence["province_matches"]:
        score += 0.02

    # 시설 종별이 다른 동명 기관(예: 왜관병원/왜관한의원)은 이름 점수가
    # 높아도 자동 확정하지 않는다.
    if not evidence["facility_matches"]:
        score *= 0.20
    if (
        not evidence["province_matches"]
        and not evidence["phone_matches"]
        and (distance is None or distance > 5.0)
    ):
        score *= 0.45
    return min(max(float(score), 0.0), 1.0)


def candidate_gate(
    target: dict,
    candidate: dict,
    score: float,
    *,
    threshold: float = AUTO_MATCH_THRESHOLD,
) -> tuple[bool, str]:
    evidence = candidate_evidence(target, candidate)
    if not evidence["facility_matches"]:
        return False, "시설종별불일치"
    distance = evidence["distance_km"]
    if (
        not evidence["province_matches"]
        and not evidence["phone_matches"]
        and (distance is None or distance > 5.0)
    ):
        return False, "지역불일치"
    if score < threshold:
        return False, "점수미달"
    has_name_identity = bool(
        evidence["name_exact"]
        or evidence["name_contains"]
        or evidence["name_similarity"] >= 0.72
    )
    has_corroboration = bool(
        evidence["address_exact"]
        or evidence["road_address_matches"]
        or evidence["phone_matches"]
        or (distance is not None and distance <= 0.75)
    )
    if not has_name_identity:
        return False, "이름근거부족"
    if not has_corroboration:
        return False, "교차근거부족"
    return True, "자동확정기준통과"


def _candidate_pool(target: dict, candidates: list[dict]) -> list[dict]:
    province_candidates = [
        candidate for candidate in candidates if _province_matches(target, candidate)
    ]
    district_candidates = [
        candidate
        for candidate in province_candidates
        if _district_matches(target, candidate)
    ]
    return district_candidates or province_candidates or candidates


def rank_match_candidates(
    targets: list[dict],
    candidates: list[dict],
    *,
    limit: int = MAX_ASSIGNMENT_CANDIDATES,
) -> dict[str, list[tuple[float, dict]]]:
    unique_candidates = {}
    for candidate in candidates:
        identifier = str(candidate.get("ykiho") or "").strip()
        if identifier:
            unique_candidates.setdefault(identifier, candidate)

    rankings = {}
    catalog = list(unique_candidates.values())
    for target in targets:
        scored = [
            (score_candidate(target, candidate), candidate)
            for candidate in _candidate_pool(target, catalog)
        ]
        scored.sort(
            key=lambda pair: (
                -pair[0],
                str(pair[1].get("ykiho") or ""),
            )
        )
        rankings[str(target["기관코드"])] = scored[:limit]
    return rankings


def _assign_from_rankings(
    rankings: dict[str, list[tuple[float, dict]]],
    *,
    threshold: float,
    margin: float,
    targets_by_code: dict[str, dict] | None = None,
) -> dict[str, tuple[dict, float, str]]:
    eligible_by_code: dict[str, list[tuple[float, dict]]] = {}
    candidates_by_identifier: dict[str, dict] = {}
    for institution_code, ranked in rankings.items():
        eligible = []
        target = (targets_by_code or {}).get(institution_code)
        for score, candidate in ranked:
            identifier = str(candidate.get("ykiho") or "").strip()
            if not identifier or score < threshold:
                continue
            if target is not None and not candidate_gate(
                target,
                candidate,
                score,
                threshold=threshold,
            )[0]:
                continue
            eligible.append((score, candidate))
            candidates_by_identifier.setdefault(identifier, candidate)
        if not eligible:
            continue
        runner_up = eligible[1][0] if len(eligible) > 1 else 0.0
        if eligible[0][0] - runner_up < margin:
            continue
        eligible_by_code[institution_code] = eligible

    institution_codes = sorted(eligible_by_code)
    identifiers = sorted(candidates_by_identifier)
    if not institution_codes or not identifiers:
        return {}

    identifier_index = {identifier: index for index, identifier in enumerate(identifiers)}
    # Every target gets a zero-utility dummy column. The cardinality bonus makes
    # one additional valid pair worth more than any possible total-score loss,
    # so the solver first maximizes match count and then the summed evidence.
    cardinality_bonus = len(institution_codes) + 1.0
    invalid_utility = -cardinality_bonus * 2
    utility = [
        [invalid_utility] * len(identifiers) + [0.0] * len(institution_codes)
        for _ in institution_codes
    ]
    score_lookup: dict[tuple[str, str], float] = {}
    for row_index, institution_code in enumerate(institution_codes):
        for score, candidate in eligible_by_code[institution_code]:
            identifier = str(candidate.get("ykiho") or "").strip()
            column_index = identifier_index[identifier]
            utility[row_index][column_index] = cardinality_bonus + score
            score_lookup[(institution_code, identifier)] = score

    row_indices, column_indices = linear_sum_assignment(utility, maximize=True)
    assignments = {}
    for row_index, column_index in zip(row_indices, column_indices, strict=True):
        if column_index >= len(identifiers) or utility[row_index][column_index] <= 0:
            continue
        institution_code = institution_codes[row_index]
        identifier = identifiers[column_index]
        assignments[institution_code] = (
            candidates_by_identifier[identifier],
            score_lookup[(institution_code, identifier)],
            "자동매칭",
        )
    return assignments


def assign_unique_matches(
    targets: list[dict],
    candidates: list[dict],
    *,
    threshold: float = AUTO_MATCH_THRESHOLD,
    margin: float = AUTO_MATCH_MARGIN,
) -> dict[str, tuple[dict, float, str]]:
    rankings = rank_match_candidates(targets, candidates)
    return _assign_from_rankings(
        rankings,
        threshold=threshold,
        margin=margin,
        targets_by_code={str(target["기관코드"]): target for target in targets},
    )


def choose_match(name: str, district: str, candidates: list[dict]) -> tuple[dict | None, float, str]:
    target = normalize_name(name)
    if district:
        local_candidates = [candidate for candidate in candidates if district in (candidate.get("addr", "") or "")]
        if not local_candidates:
            best_name_score = max(
                (SequenceMatcher(None, target, normalize_name(candidate.get("yadmNm", ""))).ratio() for candidate in candidates),
                default=0.0,
            )
            return None, best_name_score, "지역불일치" if candidates else "미매칭"
        candidates = local_candidates
    scored = []
    for candidate in candidates:
        candidate_name = normalize_name(candidate.get("yadmNm", ""))
        score = SequenceMatcher(None, target, candidate_name).ratio()
        address = candidate.get("addr", "") or ""
        if district and district in address:
            score += 0.25
        if target == candidate_name:
            score += 0.25
        scored.append((score, candidate))
    if not scored:
        return None, 0.0, "미매칭"
    score, best = max(scored, key=lambda pair: pair[0])
    if score < 0.72:
        return None, score, "낮은유사도"
    return best, min(score, 1.0), "자동매칭"


def fetch_specialist_count(ykiho: str, key: str) -> int:
    response = hira_get(
        SPECIALIST_URL,
        params={"serviceKey": key, "ykiho": ykiho, "pageNo": 1, "numOfRows": 100},
    )
    try:
        specialists = items(response.content)
    finally:
        response.close()
    emergency = [item for item in specialists if item.get("dgsbjtCd") == "24"]
    try:
        counts = [int(item.get("dtlSdrCnt") or 0) for item in emergency]
    except (TypeError, ValueError):
        raise RuntimeError("HIRA API 전문의 수 응답값이 올바르지 않습니다.") from None
    if any(count < 0 for count in counts):
        raise RuntimeError("HIRA API 전문의 수 응답값이 음수입니다.")
    return sum(counts)


def refresh_match(row: dict, key: str) -> dict:
    ykiho = str(row.get("암호화요양기호") or "").strip()
    if not ykiho:
        raise ValueError(f"매칭된 HIRA 기관의 요양기호가 없습니다: {row['기관코드']}")
    return {**row, "응급의학과전문의수": fetch_specialist_count(ykiho, key)}


def fetch_one(row: dict, key: str) -> dict:
    search_terms = [row["병원명"]]
    compact = re.sub(r"\([^)]*\)|병원$|의원$", "", row["병원명"]).strip()
    if len(compact) > 6:
        search_terms.append(compact[-6:])
    candidates = []
    for term in dict.fromkeys(search_terms):
        response = hira_get(
            HOSPITAL_URL,
            params={"serviceKey": key, "yadmNm": term, "pageNo": 1, "numOfRows": 20},
        )
        try:
            candidates.extend(items(response.content))
        finally:
            response.close()
        if candidates:
            break
    candidate, score, method = choose_match(row["병원명"], row["시군구"], candidates)
    result = {
        **row,
        "HIRA병원명": None,
        "HIRA주소": None,
        "암호화요양기호": None,
        "매칭점수": score,
        "매칭상태": method,
        "응급의학과전문의수": pd.NA,
    }
    if candidate is None:
        return result
    result["HIRA병원명"] = candidate.get("yadmNm")
    result["HIRA주소"] = candidate.get("addr")
    result["암호화요양기호"] = candidate.get("ykiho")
    result["응급의학과전문의수"] = fetch_specialist_count(candidate.get("ykiho"), key)
    return result


def unmatched_result(row: dict, *, score: float = 0.0, status: str = "미매칭") -> dict:
    return {
        **row,
        "HIRA병원명": None,
        "HIRA주소": None,
        "암호화요양기호": None,
        "매칭점수": score,
        "매칭상태": status,
        "응급의학과전문의수": pd.NA,
    }


def refresh_catalog_match(
    row: dict,
    candidate: dict,
    score: float,
    key: str,
) -> dict:
    identifier = str(candidate.get("ykiho") or "").strip()
    if not identifier:
        raise ValueError(f"HIRA 자동매칭 후보의 요양기호가 없습니다: {row['기관코드']}")
    return {
        **row,
        "HIRA병원명": candidate.get("yadmNm"),
        "HIRA주소": candidate.get("addr"),
        "암호화요양기호": identifier,
        "매칭점수": score,
        "매칭상태": "자동매칭",
        "응급의학과전문의수": fetch_specialist_count(identifier, key),
    }


def classify_unmatched_ranking(ranked: list[tuple[float, dict]]) -> tuple[float, str]:
    if not ranked:
        return 0.0, "미매칭"
    top_score = ranked[0][0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if top_score >= AUTO_MATCH_THRESHOLD and top_score - runner_up < AUTO_MATCH_MARGIN:
        return top_score, "후보모호"
    return top_score, "낮은유사도"


def classify_unassigned_ranking(
    target: dict,
    ranked: list[tuple[float, dict]],
    assigned_identifiers: set[str],
) -> tuple[float, str]:
    score, status = classify_unmatched_ranking(ranked)
    if not ranked:
        return score, status
    top_score, top_candidate = ranked[0]
    top_identifier = str(top_candidate.get("ykiho") or "").strip()
    if (
        candidate_gate(target, top_candidate, top_score)[0]
        and top_identifier in assigned_identifiers
    ):
        status = "식별자충돌검토필요"
    return score, status


def candidate_review_rows(
    targets: list[dict],
    rankings: dict[str, list[tuple[float, dict]]],
    assignments: dict[str, tuple[dict, float, str]],
    *,
    collected_at: str = "",
) -> pd.DataFrame:
    columns = [
        "기관코드", "병원명", "주소", "전화", "시도", "시군구", "후보순위",
        "HIRA병원명", "HIRA주소", "HIRA전화", "HIRAX좌표", "HIRAY좌표",
        "HIRA시설종별", "암호화요양기호", "매칭점수", "이름유사도",
        "주소유사도", "도로주소일치", "전화일치", "거리m", "시설종별일치",
        "시도일치", "시군구일치", "자동확정기준", "기준판정사유", "1위점수차",
        "자동확정", "수집시각UTC", "API근거", "매칭로직버전",
    ]
    rows = []
    for target in targets:
        institution_code = str(target["기관코드"])
        ranked = rankings.get(institution_code, [])
        top_margin = (
            ranked[0][0] - ranked[1][0]
            if len(ranked) > 1
            else (ranked[0][0] if ranked else 0.0)
        )
        assigned_identifier = (
            str(assignments[institution_code][0].get("ykiho") or "")
            if institution_code in assignments
            else ""
        )
        for rank, (score, candidate) in enumerate(
            ranked[:MAX_REVIEW_CANDIDATES],
            start=1,
        ):
            identifier = str(candidate.get("ykiho") or "")
            evidence = candidate_evidence(target, candidate)
            gate_passed, gate_reason = candidate_gate(target, candidate, score)
            distance = evidence["distance_km"]
            rows.append(
                {
                    "기관코드": institution_code,
                    "병원명": target.get("병원명"),
                    "주소": target.get("주소"),
                    "전화": target.get("전화"),
                    "시도": target.get("시도"),
                    "시군구": target.get("시군구"),
                    "후보순위": rank,
                    "HIRA병원명": candidate.get("yadmNm"),
                    "HIRA주소": candidate.get("addr"),
                    "HIRA전화": candidate.get("telno"),
                    "HIRAX좌표": candidate.get("XPos"),
                    "HIRAY좌표": candidate.get("YPos"),
                    "HIRA시설종별": candidate.get("clCdNm"),
                    "암호화요양기호": identifier,
                    "매칭점수": score,
                    "이름유사도": evidence["name_similarity"],
                    "주소유사도": evidence["address_similarity"],
                    "도로주소일치": evidence["road_address_matches"],
                    "전화일치": evidence["phone_matches"],
                    "거리m": round(distance * 1000, 1) if distance is not None else pd.NA,
                    "시설종별일치": evidence["facility_matches"],
                    "시도일치": evidence["province_matches"],
                    "시군구일치": evidence["district_matches"],
                    "자동확정기준": gate_passed,
                    "기준판정사유": gate_reason,
                    "1위점수차": top_margin,
                    "자동확정": bool(identifier and identifier == assigned_identifier),
                    "수집시각UTC": collected_at,
                    "API근거": HOSPITAL_URL,
                    "매칭로직버전": MATCH_LOGIC_VERSION,
                }
            )
    return pd.DataFrame(rows, columns=columns)


def validate_overrides(overrides: pd.DataFrame) -> None:
    missing_columns = [
        column for column in REQUIRED_OVERRIDE_COLUMNS if column not in overrides.columns
    ]
    if missing_columns:
        raise ValueError(
            "HIRA 수동 검증 파일의 필수 열이 없습니다: " + ", ".join(missing_columns)
        )

    text_columns = [
        "기관코드",
        "HIRA병원명",
        "HIRA주소",
        "암호화요양기호",
        "근거URL",
        "확인일",
    ]
    normalized = {}
    for column in text_columns:
        normalized[column] = overrides[column].astype("string").fillna("").str.strip()
        if normalized[column].eq("").any():
            raise ValueError(f"HIRA 수동 검증 파일의 {column} 값이 비어 있습니다.")

    if normalized["기관코드"].duplicated().any():
        raise ValueError("HIRA 수동 검증 파일의 기관코드가 중복됩니다.")
    if normalized["암호화요양기호"].duplicated().any():
        raise ValueError("HIRA 수동 검증 파일의 암호화요양기호가 중복됩니다.")

    specialists = pd.to_numeric(overrides["응급의학과전문의수"], errors="coerce")
    if (
        specialists.isna().any()
        or specialists.lt(0).any()
        or specialists.mod(1).ne(0).any()
    ):
        raise ValueError("HIRA 수동 검증 파일의 전문의 수는 0 이상의 정수여야 합니다.")

    for source_url in normalized["근거URL"]:
        parsed = urlparse(source_url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "hira.or.kr" or hostname.endswith(".hira.or.kr")
        ):
            raise ValueError("HIRA 수동 검증 근거URL은 공식 HIRA HTTPS 주소여야 합니다.")

    for confirmed_at in normalized["확인일"]:
        try:
            confirmed_date = datetime.strptime(confirmed_at, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("HIRA 수동 검증 확인일은 YYYY-MM-DD 형식이어야 합니다.") from None
        if confirmed_date > date.today():
            raise ValueError("HIRA 수동 검증 확인일은 미래일 수 없습니다.")


def validate_exclusions(exclusions: pd.DataFrame) -> None:
    missing_columns = [
        column for column in REQUIRED_EXCLUSION_COLUMNS if column not in exclusions.columns
    ]
    if missing_columns:
        raise ValueError(
            "HIRA 원천 불일치 파일의 필수 열이 없습니다: " + ", ".join(missing_columns)
        )

    normalized = {}
    for column in REQUIRED_EXCLUSION_COLUMNS:
        normalized[column] = exclusions[column].astype("string").fillna("").str.strip()
        if normalized[column].eq("").any():
            raise ValueError(f"HIRA 원천 불일치 파일의 {column} 값이 비어 있습니다.")

    if normalized["기관코드"].duplicated().any():
        raise ValueError("HIRA 원천 불일치 파일의 기관코드가 중복됩니다.")
    if normalized["확인요양기호"].duplicated().any():
        raise ValueError("HIRA 원천 불일치 파일의 확인요양기호가 중복됩니다.")
    if not normalized["확인요양기호"].str.fullmatch(HIRA_IDENTIFIER_PATTERN).all():
        raise ValueError("HIRA 원천 불일치 확인요양기호의 암호화 형식이 올바르지 않습니다.")
    if not normalized["사유코드"].isin(ALLOWED_EXCLUSION_REASONS).all():
        raise ValueError("HIRA 원천 불일치 파일에 허용되지 않은 사유코드가 있습니다.")

    for source_url, confirmed_identifier in zip(
        normalized["근거URL"],
        normalized["확인요양기호"],
        strict=True,
    ):
        parsed = urlparse(source_url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "hira.or.kr" or hostname.endswith(".hira.or.kr")
        ):
            raise ValueError("HIRA 원천 불일치 근거URL은 공식 HIRA HTTPS 주소여야 합니다.")
        query_identifier = parse_qs(parsed.query).get("ykiho", [""])[0]
        if query_identifier != confirmed_identifier:
            raise ValueError("HIRA 원천 불일치 근거URL의 요양기호가 확인요양기호와 다릅니다.")

    for confirmed_at in normalized["확인일"]:
        try:
            confirmed_date = datetime.strptime(confirmed_at, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("HIRA 원천 불일치 확인일은 YYYY-MM-DD 형식이어야 합니다.") from None
        if confirmed_date.strftime("%Y-%m-%d") != confirmed_at:
            raise ValueError("HIRA 원천 불일치 확인일은 YYYY-MM-DD 형식이어야 합니다.")
        age_days = (date.today() - confirmed_date).days
        if age_days < 0:
            raise ValueError("HIRA 원천 불일치 확인일은 미래일 수 없습니다.")
        if age_days > MAX_EXCLUSION_AGE_DAYS:
            raise ValueError(
                "HIRA 원천 불일치 확인이 30일을 초과했습니다. 공식 HIRA 근거를 다시 확인하세요."
            )


def validate_exclusions_against_master(
    exclusions: pd.DataFrame,
    master: pd.DataFrame,
) -> None:
    validate_exclusions(exclusions)
    master_codes = master["기관코드"].astype("string").str.strip()
    if master_codes.duplicated().any():
        raise ValueError("NEMC 모집단의 기관코드가 중복되어 원천 불일치 근거를 검증할 수 없습니다.")
    master_names = dict(
        zip(
            master_codes,
            master["병원명"].astype("string").fillna("").str.strip(),
            strict=True,
        )
    )
    for row in exclusions.to_dict("records"):
        institution_code = str(row["기관코드"]).strip()
        hospital_name = str(row["병원명"]).strip()
        if institution_code not in master_names:
            raise ValueError(
                f"HIRA 원천 불일치 대상이 NEMC 모집단에 없습니다: {institution_code}"
            )
        if master_names[institution_code] != hospital_name:
            raise ValueError(
                f"HIRA 원천 불일치 병원명이 최신 NEMC 모집단과 다릅니다: {institution_code}"
            )

    if OVERRIDES.exists():
        overrides = read_csv(OVERRIDES)
        validate_overrides(overrides)
        overlap = set(exclusions["기관코드"].astype("string").str.strip()) & set(
            overrides["기관코드"].astype("string").str.strip()
        )
        if overlap:
            raise ValueError(
                "HIRA 원천 불일치와 수동 매칭에 동시에 등록된 기관이 있습니다: "
                + ", ".join(sorted(overlap))
            )
        identifier_overlap = set(
            exclusions["확인요양기호"].astype("string").str.strip()
        ) & set(overrides["암호화요양기호"].astype("string").str.strip())
        if identifier_overlap:
            raise ValueError(
                "HIRA 원천 불일치의 이전 요양기호가 수동 매칭에도 사용됩니다."
            )


def validate_exclusions_against_catalog(
    exclusions: pd.DataFrame,
    catalog: list[dict],
    rankings: dict[str, list[tuple[float, dict]]],
    targets_by_code: dict[str, dict],
) -> None:
    catalog_identifiers = {
        str(candidate.get("ykiho") or "").strip() for candidate in catalog
    }
    for row in exclusions.to_dict("records"):
        institution_code = str(row["기관코드"]).strip()
        confirmed_identifier = str(row["확인요양기호"]).strip()
        if confirmed_identifier in catalog_identifiers:
            raise RuntimeError(
                "HIRA 원천 불일치로 보류한 요양기호가 최신 전체 목록에 다시 나타났습니다: "
                f"{institution_code}"
            )
        target = targets_by_code.get(institution_code)
        if target is None:
            raise RuntimeError(
                f"HIRA 원천 불일치 대상의 자동매칭 감사 행이 없습니다: {institution_code}"
            )
        eligible = [
            candidate
            for score, candidate in rankings.get(institution_code, [])
            if candidate_gate(target, candidate, score)[0]
        ]
        if eligible:
            raise RuntimeError(
                "HIRA 원천 불일치 대상에 새 자동확정 후보가 생겼습니다. 수동 재검토가 필요합니다: "
                f"{institution_code}"
            )


def exclusion_manifest_rows(exclusions: pd.DataFrame) -> list[dict]:
    rows = [
        {
            "institution_code": str(row["기관코드"]).strip(),
            "hospital_name": str(row["병원명"]).strip(),
            "reason_code": str(row["사유코드"]).strip(),
            "reason": str(row["사유"]).strip(),
            "confirmed_identifier": str(row["확인요양기호"]).strip(),
            "evidence_url": str(row["근거URL"]).strip(),
            "confirmed_at": str(row["확인일"]).strip(),
        }
        for row in exclusions.to_dict("records")
    ]
    return sorted(rows, key=lambda row: row["institution_code"])


def validate_overrides_against_catalog(
    overrides: pd.DataFrame,
    catalog: list[dict],
) -> None:
    validate_overrides(overrides)
    catalog_by_identifier = {
        str(candidate.get("ykiho") or "").strip(): candidate for candidate in catalog
    }
    for override in overrides.to_dict("records"):
        identifier = str(override["암호화요양기호"]).strip()
        query_identifier = parse_qs(urlparse(str(override["근거URL"])).query).get(
            "ykiho",
            [""],
        )[0]
        if query_identifier != identifier:
            raise ValueError(
                f"HIRA 수동 검증 근거URL의 요양기호가 행과 다릅니다: {override['기관코드']}"
            )
        candidate = catalog_by_identifier.get(identifier)
        if candidate is None:
            raise ValueError(
                f"HIRA 수동 검증 요양기호가 최신 전체 목록에 없습니다: {override['기관코드']}"
            )
        if normalize_name(override["HIRA병원명"]) != normalize_name(candidate.get("yadmNm", "")):
            raise ValueError(
                f"HIRA 수동 검증 병원명이 최신 전체 목록과 다릅니다: {override['기관코드']}"
            )
        override_address = normalize_address(override["HIRA주소"])
        candidate_address = normalize_address(candidate.get("addr", ""))
        address_similarity = SequenceMatcher(
            None,
            override_address,
            candidate_address,
        ).ratio()
        if not (
            override_address == candidate_address
            or (
                road_address_key(override["HIRA주소"])
                and road_address_key(override["HIRA주소"])
                == road_address_key(candidate.get("addr", ""))
            )
            or address_similarity >= 0.85
        ):
            raise ValueError(
                f"HIRA 수동 검증 주소가 최신 전체 목록과 다릅니다: {override['기관코드']}"
            )


def apply_overrides(detail: pd.DataFrame, *, overwrite_specialist: bool = True) -> pd.DataFrame:
    if not OVERRIDES.exists():
        return detail
    overrides = read_csv(OVERRIDES)
    validate_overrides(overrides)
    for override in overrides.to_dict("records"):
        mask = detail["기관코드"].eq(override["기관코드"])
        if not mask.any():
            raise ValueError(f"HIRA 수동 검증 대상이 NEMC 모집단에 없습니다: {override['기관코드']}")
        for column in ["HIRA병원명", "HIRA주소", "암호화요양기호"]:
            detail.loc[mask, column] = override[column]
        if overwrite_specialist:
            detail.loc[mask, "응급의학과전문의수"] = override["응급의학과전문의수"]
        detail.loc[mask, "매칭점수"] = 1.0
        detail.loc[mask, "매칭상태"] = "수동검증"
    return detail


def apply_exclusions(detail: pd.DataFrame, exclusions: pd.DataFrame) -> pd.DataFrame:
    validate_exclusions(exclusions)
    for exclusion in exclusions.to_dict("records"):
        institution_code = str(exclusion["기관코드"]).strip()
        mask = detail["기관코드"].astype("string").eq(institution_code)
        if int(mask.sum()) != 1:
            raise ValueError(
                f"HIRA 원천 불일치 대상이 연결 결과에 정확히 1건 존재하지 않습니다: {institution_code}"
            )
        if detail.loc[mask, "매칭상태"].isin(MATCHED_STATES).any():
            raise RuntimeError(
                "HIRA 원천 불일치 대상이 현재 매칭되어 있습니다. 근거를 재검토하세요: "
                f"{institution_code}"
            )
        for column in ["HIRA병원명", "HIRA주소", "암호화요양기호", "응급의학과전문의수"]:
            if column in detail:
                detail.loc[mask, column] = pd.NA
        detail.loc[mask, "매칭상태"] = "HIRA원천불일치"
    return detail


def manual_match_rows(master: pd.DataFrame) -> pd.DataFrame:
    columns = ["HIRA병원명", "HIRA주소", "암호화요양기호", "응급의학과전문의수"]
    seeded = master.copy()
    for column in columns:
        seeded[column] = pd.NA
    seeded["매칭점수"] = 0.0
    seeded["매칭상태"] = "미매칭"
    seeded = apply_overrides(seeded)
    return seeded[seeded["매칭상태"].eq("수동검증")]


def invalidate_duplicate_identifiers(detail: pd.DataFrame) -> pd.DataFrame:
    identifiers = detail["암호화요양기호"]
    duplicate = identifiers.notna() & identifiers.duplicated(keep=False)
    if duplicate.any():
        detail.loc[duplicate, "응급의학과전문의수"] = pd.NA
        detail.loc[duplicate, "매칭상태"] = "중복식별자검토필요"
    return detail


def build_regional_source(detail: pd.DataFrame) -> pd.DataFrame:
    detail["매칭성공"] = detail["매칭상태"].isin(MATCHED_STATES)
    detail["전문의수치확인"] = detail["응급의학과전문의수"].notna()
    grouped = detail.groupby(["시도", "시군구"], as_index=False).agg(
        응급의학과전문의수=("응급의학과전문의수", lambda values: values.sum(min_count=1)),
        전체기관수=("기관코드", "count"),
        매칭기관수=("매칭성공", "sum"),
        전문의수확인기관수=("전문의수치확인", "sum"),
    )
    grouped["매칭률"] = grouped["매칭기관수"] / grouped["전체기관수"].replace(0, pd.NA)
    grouped["데이터품질"] = grouped["매칭률"].ge(0.8).map({True: "사용가능", False: "검토필요"})
    grouped.loc[grouped["데이터품질"].eq("검토필요"), "응급의학과전문의수"] = pd.NA
    return grouped


def save_review_outputs(detail: pd.DataFrame) -> tuple[int, int]:
    columns = [
        "기관코드", "병원명", "주소", "시도", "시군구", "HIRA병원명", "HIRA주소",
        "암호화요양기호", "매칭점수", "매칭상태", "응급의학과전문의수",
    ]
    for column in columns:
        if column not in detail:
            detail[column] = pd.NA
    no_search = detail[detail["매칭상태"].eq("미매칭")][columns].copy()
    review = detail[
        ~detail["매칭상태"].isin([*MATCHED_STATES, "미매칭"])
    ][columns].copy()
    save_csv(no_search.sort_values(["시도", "시군구", "병원명"]), NO_SEARCH_OUTPUT)
    save_csv(review.sort_values(["매칭상태", "시도", "시군구", "병원명"]), REVIEW_OUTPUT)
    return len(no_search), len(review)


def main() -> None:
    parser = argparse.ArgumentParser(description="NEMC 기관에 HIRA 응급의학과 전문의 수를 연결")
    parser.add_argument(
        "--rebuild-only",
        action="store_true",
        help="API 호출 없이 기존 기관 매칭과 수동 검증 파일로 지역 집계만 재생성",
    )
    parser.add_argument(
        "--migrate-v2-exclusion-ledger",
        action="store_true",
        help="24시간 이내 v2 전체목록 감사본에 원천 불일치 이력을 최초 1회 연결",
    )
    parser.add_argument(
        "--workers",
        type=worker_count,
        default=worker_count(os.getenv("HIRA_WORKERS", str(DEFAULT_WORKERS))),
        help="HIRA 동시 요청 수(1~16, 기본값: 8, 환경변수: HIRA_WORKERS)",
    )
    parser.add_argument(
        "--catalog-page-size",
        type=catalog_page_size,
        default=catalog_page_size(
            os.getenv("HIRA_CATALOG_PAGE_SIZE", str(DEFAULT_CATALOG_PAGE_SIZE))
        ),
        help="HIRA 병원 전체 목록 페이지 크기(1~1000, 기본값: 1000)",
    )
    args = parser.parse_args()
    if args.migrate_v2_exclusion_ledger and not args.rebuild_only:
        parser.error("--migrate-v2-exclusion-ledger requires --rebuild-only")
    master_columns = ["기관코드", "병원명", "주소", "전화", "위도", "경도", "시도", "시군구"]
    master_source = read_csv(MASTER)
    for column in master_columns:
        if column not in master_source:
            master_source[column] = pd.NA
    master = master_source[master_columns].fillna("")
    exclusions = (
        read_csv(EXCLUSIONS)
        if EXCLUSIONS.exists()
        else pd.DataFrame(columns=REQUIRED_EXCLUSION_COLUMNS)
    )
    validate_exclusions_against_master(exclusions, master)
    exclusion_codes = set(exclusions["기관코드"].astype("string").str.strip())
    expected_source_exclusions = exclusion_manifest_rows(exclusions)
    previous_specialist_total = 0.0
    previous_matched_count = 0
    previous_matched_codes: set[str] = set()
    if DETAIL_OUTPUT.exists():
        previous_detail = read_csv(DETAIL_OUTPUT)
        previous_matched_mask = previous_detail.get(
            "매칭상태",
            pd.Series(dtype="string"),
        ).isin(MATCHED_STATES)
        previous_matched_count = int(previous_matched_mask.sum())
        previous_matched_codes = set(
            previous_detail.loc[previous_matched_mask, "기관코드"]
            .astype("string")
            .str.strip()
        )
        previous_specialist_total = float(
            pd.to_numeric(
                previous_detail.get("응급의학과전문의수", pd.Series(dtype=float)),
                errors="coerce",
            ).sum()
        )
    candidate_audit = None
    catalog_manifest = None
    if args.rebuild_only:
        if not DETAIL_OUTPUT.exists():
            raise FileNotFoundError(f"기존 HIRA 기관 매칭 파일이 없습니다: {DETAIL_OUTPUT}")
        if not CANDIDATE_OUTPUT.exists() or not CATALOG_MANIFEST_OUTPUT.exists():
            raise RuntimeError("HIRA 후보 감사 파일과 카탈로그 매니페스트가 없어 전체 재수집이 필요합니다.")
        detail = read_csv(DETAIL_OUTPUT)
        candidate_audit = read_csv(CANDIDATE_OUTPUT)
        if set(detail["기관코드"]) != set(master["기관코드"]):
            raise RuntimeError("NEMC 기관 모집단이 변경되어 HIRA 전체 재수집이 필요합니다.")
        identity_columns = ["병원명", "주소", "전화", "위도", "경도", "시도", "시군구"]
        if any(column not in detail for column in identity_columns):
            raise RuntimeError("기존 HIRA 상세에 최신 NEMC 식별 근거가 없어 전체 재수집이 필요합니다.")
        current_identity = master.set_index("기관코드")[identity_columns].sort_index()
        previous_identity = detail.set_index("기관코드")[identity_columns].sort_index()
        text_identity_columns = ["병원명", "주소", "전화", "시도", "시군구"]
        if not current_identity[text_identity_columns].astype("string").equals(
            previous_identity[text_identity_columns].astype("string")
        ):
            raise RuntimeError("NEMC 병원명·주소·전화·지역이 변경되어 HIRA 전체 재수집이 필요합니다.")
        coordinate_difference = (
            current_identity[["위도", "경도"]].apply(pd.to_numeric, errors="coerce")
            - previous_identity[["위도", "경도"]].apply(pd.to_numeric, errors="coerce")
        ).abs()
        if coordinate_difference.isna().any().any() or coordinate_difference.gt(1e-10).any().any():
            raise RuntimeError("NEMC 병원 좌표가 변경되어 HIRA 전체 재수집이 필요합니다.")

        manual = manual_match_rows(master)
        expected_manual_codes = set(manual["기관코드"].astype("string"))
        existing_manual_codes = set(
            detail.loc[detail["매칭상태"].eq("수동검증"), "기관코드"].astype("string")
        )
        catalog_manifest = json.loads(CATALOG_MANIFEST_OUTPUT.read_text(encoding="utf-8"))
        manifest_manual_codes = set(catalog_manifest.get("manual_match_codes", []))
        if expected_manual_codes != existing_manual_codes or expected_manual_codes != manifest_manual_codes:
            raise RuntimeError("HIRA 수동 검증 대상이 변경되어 전체 재수집이 필요합니다.")
        migrating_v2_ledger = False
        manifest_source_exclusions = catalog_manifest.get("source_exclusions")
        if manifest_source_exclusions != expected_source_exclusions:
            can_migrate_v2 = bool(
                args.migrate_v2_exclusion_ledger
                and expected_source_exclusions
                and manifest_source_exclusions is None
                and catalog_manifest.get("matching_logic_version") == "hira-catalog-v2"
                and set(candidate_audit["매칭로직버전"].astype("string"))
                == {"hira-catalog-v2"}
            )
            if not can_migrate_v2:
                raise RuntimeError("HIRA 원천 불일치 근거가 변경되어 전체 재수집이 필요합니다.")
            try:
                collected_at = datetime.fromisoformat(
                    str(catalog_manifest["collected_at_utc"])
                )
            except (KeyError, TypeError, ValueError):
                raise RuntimeError("v2 HIRA 카탈로그 수집시각을 검증할 수 없습니다.") from None
            if collected_at.tzinfo is None:
                collected_at = collected_at.replace(tzinfo=timezone.utc)
            snapshot_age = datetime.now(timezone.utc) - collected_at.astimezone(timezone.utc)
            if snapshot_age.total_seconds() < -300 or snapshot_age.total_seconds() > 86_400:
                raise RuntimeError("v2 HIRA 전체목록 감사본이 24시간을 초과해 재수집이 필요합니다.")
            migrating_detail = detail[
                detail["기관코드"].astype("string").isin(exclusion_codes)
            ]
            migrating_audit = candidate_audit[
                candidate_audit["기관코드"].astype("string").isin(exclusion_codes)
            ]
            confirmed_identifiers = set(
                exclusions["확인요양기호"].astype("string").str.strip()
            )
            audited_identifiers = set(
                migrating_audit["암호화요양기호"]
                .astype("string")
                .fillna("")
                .str.strip()
            ) - {""}
            if (
                set(migrating_detail["기관코드"].astype("string")) != exclusion_codes
                or migrating_detail["매칭상태"].isin(MATCHED_STATES).any()
                or set(migrating_audit["기관코드"].astype("string")) != exclusion_codes
                or migrating_audit["자동확정"].astype("string").str.lower().eq("true").any()
                or migrating_audit["자동확정기준"].astype("string").str.lower().eq("true").any()
                or bool(confirmed_identifiers & audited_identifiers)
            ):
                raise RuntimeError("v2 HIRA 감사본이 원천 불일치 최초 연결 조건을 충족하지 않습니다.")
            candidate_audit["매칭로직버전"] = MATCH_LOGIC_VERSION
            catalog_manifest["matching_logic_version"] = MATCH_LOGIC_VERSION
            migrating_v2_ledger = True
            catalog_manifest["exclusion_ledger_migration"] = {
                "from_logic_version": "hira-catalog-v2",
                "migrated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "catalog_snapshot_age_seconds": int(snapshot_age.total_seconds()),
            }
        existing_exclusion_codes = set(
            detail.loc[
                detail["매칭상태"].eq("HIRA원천불일치"),
                "기관코드",
            ].astype("string")
        )
        if (
            existing_exclusion_codes != exclusion_codes
            and not migrating_v2_ledger
        ):
            raise RuntimeError("HIRA 원천 불일치 근거와 기존 연결 상태가 달라 전체 재수집이 필요합니다.")
        existing_manual = (
            detail[detail["기관코드"].isin(expected_manual_codes)]
            .set_index("기관코드")
            .sort_index()
        )
        expected_manual = manual.set_index("기관코드").sort_index()
        manual_text_columns = ["HIRA병원명", "HIRA주소", "암호화요양기호"]
        existing_specialists = pd.to_numeric(
            existing_manual["응급의학과전문의수"],
            errors="coerce",
        )
        expected_specialists = pd.to_numeric(
            expected_manual["응급의학과전문의수"],
            errors="coerce",
        )
        manual_text_matches = (
            existing_manual[manual_text_columns]
            .astype("string")
            .fillna("")
            .to_numpy()
            == expected_manual[manual_text_columns]
            .astype("string")
            .fillna("")
            .to_numpy()
        ).all()
        manual_specialists_match = (
            existing_specialists.notna().all()
            and expected_specialists.notna().all()
            and (existing_specialists.to_numpy() == expected_specialists.to_numpy()).all()
        )
        if (
            not manual_text_matches
            or not manual_specialists_match
        ):
            raise RuntimeError("HIRA 수동 검증 내용이 변경되어 전체 재수집이 필요합니다.")
    else:
        key = hira_key()
        results = []
        manual = manual_match_rows(master)
        targets_frame = master[~master["기관코드"].isin(manual["기관코드"])].copy()
        print(
            f"Revalidating {len(targets_frame):,} automatic HIRA identities against the "
            f"latest catalog; fixed manual identities={len(manual):,}; workers={args.workers}",
            flush=True,
        )

        collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        catalog = fetch_hira_catalog(
            key,
            workers=args.workers,
            page_size=args.catalog_page_size,
        )
        if OVERRIDES.exists():
            validate_overrides_against_catalog(
                read_csv(OVERRIDES),
                catalog,
            )
        reserved_identifiers = set(
            manual["암호화요양기호"]
            .dropna()
            .astype("string")
            .str.strip()
        )
        available_catalog = [
            candidate
            for candidate in catalog
            if str(candidate.get("ykiho") or "").strip() not in reserved_identifiers
        ]
        targets = targets_frame.to_dict("records")
        target_by_code = {str(row["기관코드"]): row for row in targets}
        rankings = rank_match_candidates(targets, available_catalog)
        validate_exclusions_against_catalog(
            exclusions,
            catalog,
            rankings,
            target_by_code,
        )
        assignment_rankings = {
            institution_code: ranked
            for institution_code, ranked in rankings.items()
            if institution_code not in exclusion_codes
        }
        assignment_targets = {
            institution_code: row
            for institution_code, row in target_by_code.items()
            if institution_code not in exclusion_codes
        }
        assignments = _assign_from_rankings(
            assignment_rankings,
            threshold=AUTO_MATCH_THRESHOLD,
            margin=AUTO_MATCH_MARGIN,
            targets_by_code=assignment_targets,
        )
        candidate_audit = candidate_review_rows(
            targets,
            rankings,
            assignments,
            collected_at=collected_at,
        )
        assigned_identifiers = {
            str(candidate.get("ykiho") or "").strip()
            for candidate, _, _ in assignments.values()
        }
        for row in targets:
            institution_code = str(row["기관코드"])
            if institution_code in assignments:
                continue
            ranked = rankings.get(institution_code, [])
            score, status = classify_unassigned_ranking(
                row,
                ranked,
                assigned_identifiers,
            )
            results.append(unmatched_result(row, score=score, status=status))

        print(
            f"HIRA catalog rows={len(catalog):,}; "
            f"validated auto matches={len(assignments):,}; "
            f"deferred={len(targets) - len(assignments):,}",
            flush=True,
        )
        catalog_manifest = {
            "collected_at_utc": collected_at,
            "source_endpoint": HOSPITAL_URL,
            "catalog_rows": len(catalog),
            "page_size": args.catalog_page_size,
            "manual_matches": len(manual),
            "manual_match_codes": sorted(manual["기관코드"].astype("string").tolist()),
            "automatic_matches": len(assignments),
            "deferred_matches": len(targets) - len(assignments),
            "algorithmic_deferred_matches": (
                len(targets) - len(exclusion_codes) - len(assignments)
            ),
            "matching_logic_version": MATCH_LOGIC_VERSION,
        }
        failures = []
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {}
            for institution_code, (candidate, score, _) in assignments.items():
                row = target_by_code[institution_code]
                futures[executor.submit(refresh_catalog_match, row, candidate, score, key)] = row
            futures.update({
                executor.submit(refresh_match, row, key): row
                for row in manual.to_dict("records")
            })
            completed = 0
            for future in as_completed(futures):
                row = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    failures.append(f"{row['기관코드']} {row['병원명']}: {exc}")
                completed += 1
                if completed % 50 == 0 or completed == len(futures):
                    print(
                        f"HIRA specialist requests: {completed:,}/{len(futures):,}; "
                        f"failures={len(failures):,}",
                        flush=True,
                    )

        if failures:
            preview = "\n".join(f"  {message}" for message in failures[:10])
            raise RuntimeError(
                f"HIRA API 기관 요청 {len(failures)}건이 실패해 기존 산출물을 보존합니다.\n{preview}"
            )

        detail = pd.DataFrame(results)

    if "HIRA주소" not in detail:
        detail["HIRA주소"] = pd.NA
    detail = apply_overrides(detail, overwrite_specialist=args.rebuild_only)
    detail = apply_exclusions(detail, exclusions)
    detail = invalidate_duplicate_identifiers(detail)
    if detail["기관코드"].duplicated().any() or set(detail["기관코드"]) != set(read_csv(MASTER)["기관코드"]):
        raise RuntimeError("HIRA 연결 결과가 NEMC 기관 모집단을 보존하지 못했습니다.")
    identifiers = detail["암호화요양기호"].astype("string").str.strip()
    duplicate_identifiers = identifiers.notna() & identifiers.ne("") & identifiers.duplicated(keep=False)
    if duplicate_identifiers.any():
        raise RuntimeError("HIRA 연결 결과에 중복 요양기호가 있어 기존 산출물을 보존합니다.")
    confirmed_exclusion_identifiers = set(
        exclusions["확인요양기호"].astype("string").str.strip()
    )
    matched_identifier_set = set(identifiers[identifiers.notna() & identifiers.ne("")])
    if confirmed_exclusion_identifiers & matched_identifier_set:
        raise RuntimeError("HIRA 원천 불일치의 이전 요양기호가 최종 매칭에도 사용됐습니다.")
    unknown_states = set(detail["매칭상태"].dropna().astype("string")) - (
        MATCHED_STATES | REVIEW_STATES
    )
    if unknown_states:
        raise RuntimeError(
            "HIRA 연결 결과에 정의되지 않은 매칭상태가 있습니다: "
            + ", ".join(sorted(unknown_states))
        )
    detail_exclusion_codes = set(
        detail.loc[
            detail["매칭상태"].eq("HIRA원천불일치"),
            "기관코드",
        ].astype("string")
    )
    if detail_exclusion_codes != exclusion_codes:
        raise RuntimeError("HIRA 원천 불일치 근거와 최종 연결 상태가 다릅니다.")
    matched_mask = detail["매칭상태"].isin(MATCHED_STATES)
    matched_count = int(matched_mask.sum())
    matched_specialists = pd.to_numeric(
        detail.loc[matched_mask, "응급의학과전문의수"],
        errors="coerce",
    )
    if (
        identifiers.loc[matched_mask].isna().any()
        or identifiers.loc[matched_mask].eq("").any()
        or matched_specialists.isna().any()
        or matched_specialists.lt(0).any()
        or matched_specialists.mod(1).ne(0).any()
    ):
        raise RuntimeError(
            "매칭된 HIRA 기관의 요양기호 또는 전문의 수가 비어 있거나 올바르지 않습니다."
        )
    if matched_count < MIN_HIRA_MATCHES:
        raise RuntimeError(
            "HIRA 매칭 수가 검토 기준보다 급감해 기존 산출물을 보존합니다: "
            f"matched={matched_count}, required>={MIN_HIRA_MATCHES}"
        )
    allowed_exclusion_transitions = (
        set(catalog_manifest.get("source_exclusion_transitions", []))
        if args.rebuild_only and catalog_manifest is not None
        else previous_matched_codes & exclusion_codes
    )
    if not allowed_exclusion_transitions.issubset(exclusion_codes):
        raise RuntimeError("HIRA 원천 불일치 전환 이력이 현재 근거 파일과 다릅니다.")
    if (
        not args.rebuild_only
        and matched_count + len(allowed_exclusion_transitions) < previous_matched_count
    ):
        raise RuntimeError(
            "HIRA 매칭 수가 직전 정상 스냅샷보다 감소해 기존 산출물을 보존합니다: "
            f"matched={matched_count}, previous={previous_matched_count}, "
            f"validated_source_transitions={len(allowed_exclusion_transitions)}"
        )
    current_specialist_total = float(
        matched_specialists.sum()
    )
    if not args.rebuild_only and previous_specialist_total > 0 and current_specialist_total < previous_specialist_total * 0.5:
        raise RuntimeError(
            "HIRA 전문의 합계가 이전 정상 스냅샷의 절반 미만으로 급감해 기존 산출물을 보존합니다: "
            f"current={current_specialist_total:g}, previous={previous_specialist_total:g}"
        )
    grouped = build_regional_source(detail.copy())
    usable_region_count = int(grouped["데이터품질"].eq("사용가능").sum())
    gaps = grouped[grouped["데이터품질"].ne("사용가능")].copy()
    gaps["추가필요기관수"] = (
        (gaps["전체기관수"] * 0.8).apply(math.ceil) - gaps["매칭기관수"]
    ).clip(lower=0)
    gap_records = [
        {
            "province": str(row["시도"]),
            "district": str(row["시군구"]),
            "total_hospitals": int(row["전체기관수"]),
            "matched_hospitals": int(row["매칭기관수"]),
            "additional_matches_needed": int(row["추가필요기관수"]),
            "source_exclusion_codes": sorted(
                detail.loc[
                    detail["시도"].eq(row["시도"])
                    & detail["시군구"].eq(row["시군구"])
                    & detail["매칭상태"].eq("HIRA원천불일치"),
                    "기관코드",
                ].astype("string")
            ),
        }
        for row in gaps.to_dict("records")
    ]
    gap_preview = ", ".join(
        f"{row['시도']} {row['시군구']}({int(row['추가필요기관수'])})"
        for row in gaps.to_dict("records")[:15]
    )
    if usable_region_count < MIN_USABLE_REGIONS:
        raise RuntimeError(
            "HIRA 지역 80% 기준을 충족하지 못해 기존 산출물을 보존합니다: "
            f"usable={usable_region_count}, required>={MIN_USABLE_REGIONS}; {gap_preview}"
        )
    unexplained_gaps = [
        row
        for row in gap_records
        if row["additional_matches_needed"] > len(row["source_exclusion_codes"])
    ]
    if unexplained_gaps:
        unexplained_preview = ", ".join(
            f"{row['province']} {row['district']}"
            for row in unexplained_gaps[:15]
        )
        raise RuntimeError(
            "HIRA 80% 미달 지역이 검증된 원천 불일치 기관으로 설명되지 않습니다: "
            + unexplained_preview
        )
    if usable_region_count < TARGET_USABLE_REGIONS:
        print(
            "HIRA source mismatch leaves regional review gaps: "
            f"usable={usable_region_count}/{TARGET_USABLE_REGIONS}; {gap_preview}",
            flush=True,
        )
    save_csv(detail.sort_values(["시도", "시군구", "병원명"]), DETAIL_OUTPUT)
    save_csv(grouped, OUTPUT)
    if candidate_audit is not None:
        save_csv(
            candidate_audit.sort_values(["시도", "시군구", "병원명", "후보순위"]),
            CANDIDATE_OUTPUT,
        )
    no_search_count, review_count = save_review_outputs(detail)
    if catalog_manifest is not None:
        catalog_manifest.update(
            {
                "matching_logic_version": MATCH_LOGIC_VERSION,
                "matched_hospitals": matched_count,
                "automatic_matches": int(detail["매칭상태"].eq("자동매칭").sum()),
                "manual_matches": int(detail["매칭상태"].eq("수동검증").sum()),
                "manual_match_codes": sorted(
                    detail.loc[
                        detail["매칭상태"].eq("수동검증"),
                        "기관코드",
                    ].astype("string")
                ),
                "deferred_matches": len(detail) - matched_count,
                "algorithmic_deferred_matches": (
                    len(detail) - matched_count - len(exclusion_codes)
                ),
                "usable_regions": usable_region_count,
                "target_usable_regions": TARGET_USABLE_REGIONS,
                "regional_quality_gaps": gap_records,
                "regional_rows": len(grouped),
                "emergency_specialist_total": current_specialist_total,
                "source_exclusion_count": len(expected_source_exclusions),
                "source_exclusion_codes": sorted(exclusion_codes),
                "source_exclusions": expected_source_exclusions,
                "source_exclusion_transitions": sorted(allowed_exclusion_transitions),
            }
        )
        save_json(catalog_manifest, CATALOG_MANIFEST_OUTPUT)
    print(f"Saved {len(detail):,} hospital matches; matched={matched_count:,}")
    print(f"Saved {len(grouped):,} regional doctor rows; usable={usable_region_count:,}")
    print(f"Saved HIRA review queues: no_search={no_search_count:,}, other_review={review_count:,}")


if __name__ == "__main__":
    main()
