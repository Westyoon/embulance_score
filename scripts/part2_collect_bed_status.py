from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from common import DATA_DIR, read_csv, request_xml, save_csv, xml_items

MASTER = DATA_DIR / "hospital_master.csv"
OUTPUT = DATA_DIR / "bed_status.csv"
HISTORY = DATA_DIR / "bed_status_history.csv"

# NEMC 응급의료정보조회서비스 V4: hvec=가용 응급실 병상, hvs01=기준 응급실 병상.
AVAILABLE_FIELD = "hvec"
TOTAL_FIELD = "hvs01"


def collect_region(province: str, district: str) -> list[dict]:
    root = request_xml(
        "getEmrrmRltmUsefulSckbdInfoInqire",
        {"STAGE1": province, "STAGE2": district, "pageNo": 1, "numOfRows": 100},
    )
    return xml_items(root)


def main() -> None:
    master = read_csv(MASTER)
    regions = master[["시도", "시군구"]].dropna().drop_duplicates()
    records = []
    failures = []
    region_pairs = list(regions.itertuples(index=False, name=None))
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(collect_region, str(province), str(district)): (province, district)
            for province, district in region_pairs
        }
        for future in as_completed(futures):
            province, district = futures[future]
            try:
                records.extend(future.result())
            except Exception as exc:
                failures.append(f"{province}|{district}: {exc}")

    raw = pd.DataFrame(records)
    if raw.empty:
        raise RuntimeError("실시간 병상 데이터가 한 건도 수집되지 않았습니다.")
    raw = raw.drop_duplicates("hpid", keep="last")
    bed = pd.DataFrame(
        {
            "기관코드": raw["hpid"].astype("string").str.strip(),
            "가용병상": pd.to_numeric(raw.get(AVAILABLE_FIELD), errors="coerce"),
            "전체병상": pd.to_numeric(raw.get(TOTAL_FIELD), errors="coerce"),
            "API기준시각": raw.get("hvidate", ""),
        }
    )
    bed["포화율"] = ((bed["전체병상"] - bed["가용병상"]) / bed["전체병상"] * 100).clip(0, 100)
    valid = bed["전체병상"].gt(0) & bed["가용병상"].ge(0)
    bed.loc[~valid, "포화율"] = pd.NA
    bed["상태"] = pd.cut(
        bed["포화율"],
        bins=[-0.001, 50, 80, 100],
        labels=["여유", "주의", "포화"],
        include_lowest=True,
    ).astype("string").fillna("결측")
    bed["수집시각"] = datetime.now().astimezone().isoformat(timespec="seconds")

    columns = ["기관코드", "병원명", "등급", "시도", "시군구", "가용병상", "전체병상", "포화율", "상태", "API기준시각", "수집시각"]
    result = master.merge(bed, on="기관코드", how="left").reindex(columns=columns)
    result["상태"] = result["상태"].fillna("결측")
    save_csv(result, OUTPUT)

    history_row = result.copy()
    if HISTORY.exists():
        history = pd.read_csv(HISTORY)
        history_row = pd.concat([history, history_row], ignore_index=True)
    save_csv(history_row, HISTORY)
    print(f"Saved {len(result):,} hospitals ({len(bed):,} live matches): {OUTPUT}")
    if failures:
        print(f"Warning: {len(failures)} regional requests failed")
        for message in failures[:10]:
            print(f"  {message}")


if __name__ == "__main__":
    main()
