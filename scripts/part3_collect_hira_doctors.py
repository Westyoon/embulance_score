import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from urllib.parse import unquote

import pandas as pd
import requests
from dotenv import dotenv_values

from common import DATA_DIR, ROOT, read_csv, save_csv

MASTER = DATA_DIR / "hospital_master.csv"
DETAIL_OUTPUT = DATA_DIR / "hira_doctor_matches.csv"
OUTPUT = DATA_DIR / "doctor_source.csv"
HOSPITAL_URL = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
SPECIALIST_URL = "https://apis.data.go.kr/B551182/MadmDtlInfoService2.8/getSpcSbjtSdrInfo2.8"


def hira_key() -> str:
    value = (dotenv_values(ROOT / ".env").get("HIRA_API_KEY") or "").strip()
    if not value:
        raise RuntimeError(".env에 HIRA_API_KEY를 설정하세요.")
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
    scored = []
    for candidate in candidates:
        candidate_name = normalize_name(candidate.get("yadmNm", ""))
        score = SequenceMatcher(None, target, candidate_name).ratio()
        address = candidate.get("addr", "") or ""
        if district and district in address:
            score += 0.15
        if target == candidate_name:
            score += 0.25
        scored.append((score, candidate))
    if not scored:
        return None, 0.0, "미매칭"
    score, best = max(scored, key=lambda pair: pair[0])
    if score < 0.72:
        return None, score, "낮은유사도"
    return best, min(score, 1.0), "자동매칭"


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
    result = {**row, "HIRA병원명": None, "암호화요양기호": None, "매칭점수": score, "매칭상태": method, "응급의학과전문의수": pd.NA}
    if candidate is None:
        return result
    result["HIRA병원명"] = candidate.get("yadmNm")
    result["암호화요양기호"] = candidate.get("ykiho")
    response = requests.get(
        SPECIALIST_URL,
        params={"serviceKey": key, "ykiho": candidate.get("ykiho"), "pageNo": 1, "numOfRows": 100},
        timeout=20,
    )
    response.raise_for_status()
    specialists = items(response.content)
    emergency = [x for x in specialists if x.get("dgsbjtCd") == "24"]
    result["응급의학과전문의수"] = sum(int(x.get("dtlSdrCnt") or 0) for x in emergency)
    return result


def main() -> None:
    master = read_csv(MASTER)[["기관코드", "병원명", "주소", "시도", "시군구"]].fillna("")
    key = hira_key()
    results = []
    if DETAIL_OUTPUT.exists():
        cached = read_csv(DETAIL_OUTPUT)
        cached = cached[cached["매칭상태"].eq("자동매칭")]
        results.extend(cached.to_dict("records"))
        master = master[~master["기관코드"].isin(cached["기관코드"])]
        print(f"Reusing {len(cached):,} cached matches; retrying {len(master):,}")
    with ThreadPoolExecutor(max_workers=24) as executor:
        futures = [executor.submit(fetch_one, row, key) for row in master.to_dict("records")]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"기관코드": "", "병원명": "", "주소": "", "시도": "", "시군구": "", "매칭상태": f"API오류: {exc}", "응급의학과전문의수": pd.NA})

    detail = pd.DataFrame(results)
    save_csv(detail.sort_values(["시도", "시군구", "병원명"]), DETAIL_OUTPUT)
    detail["매칭성공"] = detail["매칭상태"].eq("자동매칭")
    detail["전문의수치확인"] = detail["응급의학과전문의수"].notna()
    grouped = detail.groupby(["시도", "시군구"], as_index=False).agg(
        응급의학과전문의수=("응급의학과전문의수", "sum"),
        전체기관수=("기관코드", "count"),
        매칭기관수=("매칭성공", "sum"),
        전문의수확인기관수=("전문의수치확인", "sum"),
    )
    grouped["매칭률"] = grouped["매칭기관수"] / grouped["전체기관수"].replace(0, pd.NA)
    grouped["데이터품질"] = grouped["매칭률"].ge(0.8).map({True: "사용가능", False: "검토필요"})
    grouped.loc[grouped["데이터품질"].eq("검토필요"), "응급의학과전문의수"] = pd.NA
    save_csv(grouped, OUTPUT)
    print(f"Saved {len(detail):,} hospital matches; matched={int(detail['매칭성공'].sum()):,}")
    print(f"Saved {len(grouped):,} regional doctor rows; usable={int(grouped['데이터품질'].eq('사용가능').sum()):,}")


if __name__ == "__main__":
    main()
