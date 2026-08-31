from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
import math
import os
import re
import time

import pandas as pd

from common import (
    DATA_DIR,
    PublicDataApiError,
    read_csv,
    request_xml,
    save_csv,
    xml_items,
)

MASTER = DATA_DIR / "hospital_master.csv"
OUTPUT = DATA_DIR / "bed_status.csv"
HISTORY = DATA_DIR / "bed_status_history.csv"

# NEMC 응급의료정보조회서비스 V4: hvec=가용 응급실 병상, hvs01=기준 응급실 병상.
AVAILABLE_FIELD = "hvec"
TOTAL_FIELD = "hvs01"
MIN_LIVE_MATCHES = 373
MAX_RETRY_DELAY_SECONDS = 60.0
RETRYABLE_RESULT_CODES = {"01", "02", "04", "05", "21", "22", "99"}


def bed_api_max_attempts() -> int:
    raw = os.getenv("BED_API_MAX_ATTEMPTS", "3")
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError("BED_API_MAX_ATTEMPTS는 양의 정수여야 합니다.") from None
    if value < 1:
        raise RuntimeError("BED_API_MAX_ATTEMPTS는 양의 정수여야 합니다.")
    return value


def retriable_bed_error(error: RuntimeError) -> bool:
    if isinstance(error, PublicDataApiError):
        return (
            error.kind in {"timeout", "connection", "request", "parse"}
            or error.status_code == 429
            or (error.status_code is not None and 500 <= error.status_code < 600)
            or error.result_code in RETRYABLE_RESULT_CODES
        )
    message = str(error)
    return (
        message.startswith("공공데이터 API 요청 시간 초과")
        or message.startswith("공공데이터 API 연결 실패")
        or message.startswith("공공데이터 API 요청 실패")
        or message.startswith("공공데이터 API 응답 형식을 해석할 수 없습니다")
        or message.startswith("병상 API 응답이 일부만 반환됐습니다")
        or re.search(r"공공데이터 API HTTP 오류: (429|5\d\d)$", message) is not None
        or re.search(
            r"공공데이터 API 응답 오류\(resultCode=(01|02|04|05|21|22|99)\)$",
            message,
        ) is not None
    )


def retry_delay(error: RuntimeError, attempt: int) -> float:
    retry_after = error.retry_after if isinstance(error, PublicDataApiError) else None
    if retry_after:
        try:
            seconds = float(retry_after)
            if math.isfinite(seconds):
                return min(max(seconds, 0.0), MAX_RETRY_DELAY_SECONDS)
        except (ValueError, OverflowError):
            pass
        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.astimezone()
            seconds = (retry_at - datetime.now().astimezone()).total_seconds()
            if math.isfinite(seconds):
                return min(max(seconds, 0.0), MAX_RETRY_DELAY_SECONDS)
        except (TypeError, ValueError, OverflowError):
            pass
    return min(2**attempt, MAX_RETRY_DELAY_SECONDS)


def trim_history(history: pd.DataFrame) -> pd.DataFrame:
    raw_days = os.getenv("BED_HISTORY_RETENTION_DAYS", "").strip()
    if not raw_days:
        return history
    try:
        retention_days = int(raw_days)
    except ValueError:
        raise RuntimeError("BED_HISTORY_RETENTION_DAYS는 양의 정수여야 합니다.") from None
    if retention_days < 1:
        raise RuntimeError("BED_HISTORY_RETENTION_DAYS는 양의 정수여야 합니다.")
    timestamps = pd.to_datetime(history.get("수집시각"), errors="coerce", utc=True)
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=retention_days)
    return history[timestamps.isna() | timestamps.ge(cutoff)].reset_index(drop=True)


def collect_region(province: str, district: str) -> list[dict]:
    attempts = bed_api_max_attempts()
    for attempt in range(attempts):
        try:
            root = request_xml(
                "getEmrrmRltmUsefulSckbdInfoInqire",
                {"STAGE1": province, "STAGE2": district, "pageNo": 1, "numOfRows": 100},
            )
            records = xml_items(root)
            reported_total = pd.to_numeric(root.findtext(".//totalCount"), errors="coerce")
            if pd.isna(reported_total) or int(reported_total) != len(records):
                raise RuntimeError(
                    f"병상 API 응답이 일부만 반환됐습니다: totalCount={reported_total}, rows={len(records)}"
                )
            return records
        except RuntimeError as error:
            if attempt + 1 >= attempts or not retriable_bed_error(error):
                raise
            time.sleep(retry_delay(error, attempt))
    raise AssertionError("unreachable")


def main() -> None:
    master = read_csv(MASTER)
    previous_live_matches = 0
    if OUTPUT.exists():
        previous = read_csv(OUTPUT)
        previous_live_matches = int(
            previous[["가용병상", "전체병상", "API기준시각"]].notna().any(axis=1).sum()
        )
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

    if failures:
        preview = "\n".join(f"  {message}" for message in failures[:10])
        raise RuntimeError(
            f"실시간 병상 API 지역 요청 {len(failures)}건이 실패해 기존 산출물을 보존합니다.\n{preview}"
        )

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

    live_matches = int(master["기관코드"].isin(set(bed["기관코드"])).sum())
    minimum_matches = max(MIN_LIVE_MATCHES, int(previous_live_matches * 0.9))
    if live_matches < minimum_matches:
        raise RuntimeError(
            "실시간 병상 응답 기관 수가 정상 스냅샷보다 급감해 기존 산출물을 보존합니다: "
            f"matched={live_matches}, required>={minimum_matches}, previous={previous_live_matches}"
        )

    columns = ["기관코드", "병원명", "등급", "시도", "시군구", "가용병상", "전체병상", "포화율", "상태", "API기준시각", "수집시각"]
    result = master.merge(bed, on="기관코드", how="left").reindex(columns=columns)
    result["상태"] = result["상태"].fillna("결측")
    save_csv(result, OUTPUT)

    history_row = result.copy()
    if HISTORY.exists():
        history = pd.read_csv(HISTORY)
        history_row = pd.concat([history, history_row], ignore_index=True)
    history_row = trim_history(history_row)
    save_csv(history_row, HISTORY)
    print(f"Saved {len(result):,} hospitals ({live_matches:,} live matches): {OUTPUT}")


if __name__ == "__main__":
    main()
