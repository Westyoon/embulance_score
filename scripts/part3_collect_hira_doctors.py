import argparse
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from urllib.parse import unquote

import pandas as pd
import requests

from common import DATA_DIR, read_csv, save_csv

MASTER = DATA_DIR / "hospital_master.csv"
DETAIL_OUTPUT = DATA_DIR / "hira_doctor_matches.csv"
OUTPUT = DATA_DIR / "doctor_source.csv"
OVERRIDES = DATA_DIR / "hira_match_overrides.csv"
HOSPITAL_URL = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
SPECIALIST_URL = "https://apis.data.go.kr/B551182/MadmDtlInfoService2.8/getSpcSbjtSdrInfo2.8"
MATCHED_STATES = {"자동매칭", "수동검증"}
MIN_HIRA_MATCHES = 400


def hira_key() -> str:
    value = os.getenv("HIRA_API_KEY", "").strip()
    if not value:
        raise RuntimeError("환경변수 또는 .env에 HIRA_API_KEY를 설정하세요.")
    return unquote(value)


def items(content: bytes) -> list[dict]:
    root = ET.fromstring(content)
    if root.findtext(".//resultCode") != "00":
        raise RuntimeError(f"HIRA API 오류: {root.findtext('.//resultCode')} {root.findtext('.//resultMsg')}")
    return [{child.tag: child.text for child in item} for item in root.findall(".//item")]


def normalize_name(value: str) -> str:
    value = re.sub(r"\([^)]*\)|（[^）]*）", "", str(value or ""))
    value = re.sub(r"의료법인|재단법인|학교법인|사회복지법인|사단법인|병원|의원|대학교", "", value)
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value).lower()


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
    response = requests.get(
        SPECIALIST_URL,
        params={"serviceKey": key, "ykiho": ykiho, "pageNo": 1, "numOfRows": 100},
        timeout=20,
    )
    response.raise_for_status()
    specialists = items(response.content)
    emergency = [item for item in specialists if item.get("dgsbjtCd") == "24"]
    return sum(int(item.get("dtlSdrCnt") or 0) for item in emergency)


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
        response = requests.get(
            HOSPITAL_URL,
            params={"serviceKey": key, "yadmNm": term, "pageNo": 1, "numOfRows": 20},
            timeout=20,
        )
        response.raise_for_status()
        candidates.extend(items(response.content))
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


def apply_overrides(detail: pd.DataFrame, *, overwrite_specialist: bool = True) -> pd.DataFrame:
    if not OVERRIDES.exists():
        return detail
    overrides = read_csv(OVERRIDES)
    if overrides["기관코드"].duplicated().any():
        raise ValueError("HIRA 수동 검증 파일의 기관코드가 중복됩니다.")
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


def main() -> None:
    parser = argparse.ArgumentParser(description="NEMC 기관에 HIRA 응급의학과 전문의 수를 연결")
    parser.add_argument(
        "--rebuild-only",
        action="store_true",
        help="API 호출 없이 기존 기관 매칭과 수동 검증 파일로 지역 집계만 재생성",
    )
    args = parser.parse_args()
    master = read_csv(MASTER)[["기관코드", "병원명", "주소", "시도", "시군구"]].fillna("")
    previous_specialist_total = 0.0
    if DETAIL_OUTPUT.exists():
        previous_detail = read_csv(DETAIL_OUTPUT)
        previous_specialist_total = float(
            pd.to_numeric(
                previous_detail.get("응급의학과전문의수", pd.Series(dtype=float)),
                errors="coerce",
            ).sum()
        )
    if args.rebuild_only:
        if not DETAIL_OUTPUT.exists():
            raise FileNotFoundError(f"기존 HIRA 기관 매칭 파일이 없습니다: {DETAIL_OUTPUT}")
        detail = read_csv(DETAIL_OUTPUT)
    else:
        key = hira_key()
        results = []
        cached = pd.DataFrame()
        if DETAIL_OUTPUT.exists():
            cached = read_csv(DETAIL_OUTPUT)
            cached = cached[cached["매칭상태"].isin(MATCHED_STATES)]
            # HIRA 결과만 캐시하고 병원명·주소·지역은 최신 NEMC 마스터로 되돌린다.
            hira_columns = [
                "기관코드", "HIRA병원명", "HIRA주소", "암호화요양기호",
                "매칭점수", "매칭상태", "응급의학과전문의수",
            ]
            for column in hira_columns:
                if column not in cached:
                    cached[column] = pd.NA
            cached = master.merge(cached[hira_columns], on="기관코드", how="inner", validate="one_to_one")
        manual = manual_match_rows(master)
        if not cached.empty:
            cached = cached[~cached["기관코드"].isin(manual["기관코드"])]
        cached = pd.concat([cached, manual], ignore_index=True)
        master = master[~master["기관코드"].isin(cached["기관코드"])]
        print(
            f"Refreshing {len(cached):,} cached specialist counts; "
            f"retrying {len(master):,} unmatched institutions"
        )
        failures = []
        with ThreadPoolExecutor(max_workers=24) as executor:
            futures = {
                executor.submit(fetch_one, row, key): row
                for row in master.to_dict("records")
            }
            futures.update({
                executor.submit(refresh_match, row, key): row
                for row in cached.to_dict("records")
            })
            for future in as_completed(futures):
                row = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    failures.append(f"{row['기관코드']} {row['병원명']}: {exc}")

        if failures:
            preview = "\n".join(f"  {message}" for message in failures[:10])
            raise RuntimeError(
                f"HIRA API 기관 요청 {len(failures)}건이 실패해 기존 산출물을 보존합니다.\n{preview}"
            )

        detail = pd.DataFrame(results)

    if "HIRA주소" not in detail:
        detail["HIRA주소"] = pd.NA
    detail = apply_overrides(detail, overwrite_specialist=args.rebuild_only)
    detail = invalidate_duplicate_identifiers(detail)
    if detail["기관코드"].duplicated().any() or set(detail["기관코드"]) != set(read_csv(MASTER)["기관코드"]):
        raise RuntimeError("HIRA 연결 결과가 NEMC 기관 모집단을 보존하지 못했습니다.")
    matched_count = int(detail["매칭상태"].isin(MATCHED_STATES).sum())
    if matched_count < MIN_HIRA_MATCHES:
        raise RuntimeError(
            "HIRA 매칭 수가 검토 기준보다 급감해 기존 산출물을 보존합니다: "
            f"matched={matched_count}, required>={MIN_HIRA_MATCHES}"
        )
    current_specialist_total = float(
        pd.to_numeric(detail.loc[detail["매칭상태"].isin(MATCHED_STATES), "응급의학과전문의수"], errors="coerce").sum()
    )
    if not args.rebuild_only and previous_specialist_total > 0 and current_specialist_total < previous_specialist_total * 0.5:
        raise RuntimeError(
            "HIRA 전문의 합계가 이전 정상 스냅샷의 절반 미만으로 급감해 기존 산출물을 보존합니다: "
            f"current={current_specialist_total:g}, previous={previous_specialist_total:g}"
        )
    save_csv(detail.sort_values(["시도", "시군구", "병원명"]), DETAIL_OUTPUT)
    grouped = build_regional_source(detail)
    save_csv(grouped, OUTPUT)
    print(f"Saved {len(detail):,} hospital matches; matched={matched_count:,}")
    print(f"Saved {len(grouped):,} regional doctor rows; usable={int(grouped['데이터품질'].eq('사용가능').sum()):,}")


if __name__ == "__main__":
    main()
