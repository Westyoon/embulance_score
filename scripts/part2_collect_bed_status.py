from datetime import datetime
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
import math
import os
import re
import threading
import time

import pandas as pd

from common import (
    DATA_DIR,
    PublicDataApiError,
    read_csv,
    request_xml,
    save_csv,
    save_json,
    xml_items,
)

MASTER = DATA_DIR / "hospital_master.csv"
OUTPUT = DATA_DIR / "bed_status.csv"
HISTORY = DATA_DIR / "bed_status_history.csv"
AUDIT = DATA_DIR / "bed_refresh_audit.json"

# NEMC 응급의료정보조회서비스 V4: hvec=가용 응급실 병상, hvs01=기준 응급실 병상.
AVAILABLE_FIELD = "hvec"
TOTAL_FIELD = "hvs01"
MIN_LIVE_MATCHES = 373
MAX_RETRY_DELAY_SECONDS = 60.0
RETRYABLE_RESULT_CODES = {"01", "02", "04", "05", "21", "22", "99"}
QUOTA_CIRCUIT_RESULT_CODES = {"21", "22"}
DEFAULT_BED_SOURCE_MAX_AGE_HOURS = 12.0
MAX_BED_SOURCE_FUTURE_SKEW_MINUTES = 5.0


class RegionFailure(str):
    """A backward-compatible failure message with structured region fields."""

    province: str
    district: str
    reason: str

    def __new__(cls, province: str, district: str, reason: str):
        value = super().__new__(cls, f"{province}|{district}: {reason}")
        value.province = province
        value.district = district
        value.reason = reason
        return value

    def audit_record(self) -> dict[str, str]:
        return {
            "province": self.province,
            "district": self.district,
            "reason": self.reason,
        }


class BedApiQuotaCircuitOpen(RuntimeError):
    """Raised before an API call when another region exhausted the shared quota."""


class BedApiCircuitBreaker:
    """Coordinate one bounded quota retry, then stop all queued region work."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._state = "closed"
        self._quota_retry_used = False

    def is_tripped(self) -> bool:
        with self._condition:
            return self._state == "open"

    def wait_until_request_allowed(self) -> None:
        with self._condition:
            while self._state == "retrying":
                self._condition.wait()
            if self._state == "open":
                raise BedApiQuotaCircuitOpen(
                    "병상 API 쿼터 회로가 열려 있어 추가 지역 요청을 중단합니다."
                )

    def begin_quota_retry(self) -> bool:
        """Return true only to the single worker allowed to retry the quota error."""
        with self._condition:
            if self._state == "retrying":
                return False
            if self._state == "open" or self._quota_retry_used:
                self._state = "open"
                self._condition.notify_all()
                return False
            self._quota_retry_used = True
            self._state = "retrying"
            return True

    def finish_quota_retry(self, *, recovered: bool) -> None:
        with self._condition:
            if recovered:
                # Another in-flight worker may have tripped the shared circuit
                # while the retry leader was waiting for its response. A late
                # success must not overwrite that stronger, global signal.
                if self._state == "retrying":
                    self._state = "closed"
            else:
                self._state = "open"
            self._condition.notify_all()

    def trip(self) -> None:
        with self._condition:
            self._state = "open"
            self._condition.notify_all()


def bed_api_max_attempts() -> int:
    raw = os.getenv("BED_API_MAX_ATTEMPTS", "3")
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError("BED_API_MAX_ATTEMPTS는 양의 정수여야 합니다.") from None
    if value < 1:
        raise RuntimeError("BED_API_MAX_ATTEMPTS는 양의 정수여야 합니다.")
    return value


def bed_source_max_age_hours() -> float:
    raw = os.getenv("BED_SOURCE_MAX_AGE_HOURS", str(DEFAULT_BED_SOURCE_MAX_AGE_HOURS)).strip()
    try:
        value = float(raw)
    except ValueError:
        raise RuntimeError("BED_SOURCE_MAX_AGE_HOURS는 양수여야 합니다.") from None
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError("BED_SOURCE_MAX_AGE_HOURS는 양수여야 합니다.")
    return value


def bed_source_timestamp_text(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    integral = numeric.where(numeric.mod(1).eq(0))
    return integral.astype("Int64").astype("string").str.zfill(14)


def fresh_bed_source_mask(
    values: pd.Series,
    *,
    reference: pd.Timestamp | datetime | None = None,
    max_age_hours: float | None = None,
    max_future_skew_minutes: float = MAX_BED_SOURCE_FUTURE_SKEW_MINUTES,
) -> pd.Series:
    """Return rows whose hospital-reported hvidate is fresh in Korea time."""
    age_limit = bed_source_max_age_hours() if max_age_hours is None else max_age_hours
    if not math.isfinite(age_limit) or age_limit <= 0:
        raise RuntimeError("BED_SOURCE_MAX_AGE_HOURS는 양수여야 합니다.")
    now = pd.Timestamp.now(tz="UTC") if reference is None else pd.Timestamp(reference)
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    else:
        now = now.tz_convert("UTC")
    text = bed_source_timestamp_text(values)
    parsed = pd.to_datetime(text, format="%Y%m%d%H%M%S", errors="coerce")
    parsed = parsed.dt.tz_localize(
        "Asia/Seoul",
        ambiguous="NaT",
        nonexistent="NaT",
    ).dt.tz_convert("UTC")
    return (
        parsed.notna()
        & parsed.ge(now - pd.Timedelta(hours=age_limit))
        & parsed.le(now + pd.Timedelta(minutes=max_future_skew_minutes))
    )


def fresh_bed_source_at_collection_mask(
    values: pd.Series,
    collected_values: pd.Series,
    *,
    max_age_hours: float | None = None,
    max_future_skew_minutes: float = MAX_BED_SOURCE_FUTURE_SKEW_MINUTES,
) -> pd.Series:
    """Validate hvidate against each persisted collection timestamp."""
    age_limit = bed_source_max_age_hours() if max_age_hours is None else max_age_hours
    if not math.isfinite(age_limit) or age_limit <= 0:
        raise RuntimeError("BED_SOURCE_MAX_AGE_HOURS는 양수여야 합니다.")
    text = bed_source_timestamp_text(values)
    source = pd.to_datetime(text, format="%Y%m%d%H%M%S", errors="coerce")
    source = source.dt.tz_localize(
        "Asia/Seoul",
        ambiguous="NaT",
        nonexistent="NaT",
    ).dt.tz_convert("UTC")
    collected = pd.to_datetime(collected_values, errors="coerce", utc=True)
    return (
        source.notna()
        & collected.notna()
        & source.ge(collected - pd.Timedelta(hours=age_limit))
        & source.le(collected + pd.Timedelta(minutes=max_future_skew_minutes))
    )


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


def rate_limited_bed_error(error: RuntimeError) -> bool:
    if isinstance(error, PublicDataApiError):
        return error.status_code == 429 or error.result_code in QUOTA_CIRCUIT_RESULT_CODES
    message = str(error)
    return (
        re.search(r"공공데이터 API HTTP 오류: 429$", message) is not None
        or re.search(r"공공데이터 API 응답 오류\(resultCode=(21|22)\)$", message) is not None
        or re.search(r"(?:quota|rate.?limit|쿼터|요청\s*한도)", message, re.IGNORECASE) is not None
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


def collect_region(
    province: str,
    district: str,
    circuit_breaker: BedApiCircuitBreaker | None = None,
) -> list[dict]:
    attempts = bed_api_max_attempts()
    quota_retry_leader = False
    for attempt in range(attempts):
        if circuit_breaker is not None and not quota_retry_leader:
            circuit_breaker.wait_until_request_allowed()
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
            if quota_retry_leader and circuit_breaker is not None:
                circuit_breaker.finish_quota_retry(recovered=True)
            return records
        except RuntimeError as error:
            if quota_retry_leader and circuit_breaker is not None:
                circuit_breaker.finish_quota_retry(recovered=False)
                raise
            if rate_limited_bed_error(error):
                if circuit_breaker is not None:
                    if attempt + 1 >= attempts:
                        circuit_breaker.trip()
                        raise
                    if circuit_breaker.begin_quota_retry():
                        quota_retry_leader = True
                        try:
                            time.sleep(retry_delay(error, attempt))
                        except BaseException:
                            circuit_breaker.finish_quota_retry(recovered=False)
                            raise
                        continue
                    circuit_breaker.wait_until_request_allowed()
                    continue
                if attempt + 1 >= attempts:
                    raise
                time.sleep(retry_delay(error, attempt))
                continue
            if attempt + 1 >= attempts or not retriable_bed_error(error):
                raise
            time.sleep(retry_delay(error, attempt))
        except BaseException:
            # KeyboardInterrupt/SystemExit and unexpected non-RuntimeError
            # failures must release workers waiting on the retrying state.
            if quota_retry_leader and circuit_breaker is not None:
                circuit_breaker.finish_quota_retry(recovered=False)
            raise
    raise AssertionError("unreachable")


def collect_regions(
    region_pairs: list[tuple[object, object]],
    *,
    max_workers: int = 8,
) -> tuple[list[dict], list[RegionFailure]]:
    records: list[dict] = []
    failures: list[RegionFailure] = []
    circuit_breaker = BedApiCircuitBreaker()
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = {
        executor.submit(
            collect_region,
            str(province),
            str(district),
            circuit_breaker,
        ): (province, district)
        for province, district in region_pairs
    }
    try:
        for future in as_completed(futures):
            province, district = futures[future]
            try:
                records.extend(future.result())
            except CancelledError:
                failures.append(
                    RegionFailure(
                        str(province),
                        str(district),
                        "병상 API 쿼터 회로로 요청 취소",
                    )
                )
            except Exception as exc:
                failures.append(
                    RegionFailure(str(province), str(district), str(exc))
                )

            if circuit_breaker.is_tripped():
                for pending in futures:
                    pending.cancel()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    failures.sort(key=lambda failure: (failure.province, failure.district))
    return records, failures


def previous_response_count(previous: pd.DataFrame | None) -> int:
    if previous is None or previous.empty:
        return 0
    value_columns = ["가용병상", "전체병상", "API기준시각"]
    if not set(value_columns).issubset(previous.columns):
        return 0
    return int(previous[value_columns].notna().any(axis=1).sum())


def failed_region_fallback(
    master: pd.DataFrame,
    previous: pd.DataFrame | None,
    failures: list[RegionFailure],
    *,
    reference: pd.Timestamp | datetime,
) -> tuple[pd.DataFrame, dict[str, int]]:
    columns = ["기관코드", "가용병상", "전체병상", "API기준시각", "수집시각"]
    failed_keys = {(failure.province, failure.district) for failure in failures}
    if not failed_keys:
        return pd.DataFrame(columns=columns), {
            "failedRegionHospitals": 0,
            "fallbackCandidateHospitals": 0,
            "freshFallbackHospitals": 0,
            "maskedFailedRegionHospitals": 0,
        }

    master_regions = master[["시도", "시군구"]].astype("string")
    failed_master = master[
        master_regions.apply(tuple, axis=1).isin(failed_keys)
    ]
    failed_codes = set(failed_master["기관코드"].astype("string"))
    failed_hospitals = len(failed_codes)
    if previous is None or previous.empty:
        return pd.DataFrame(columns=columns), {
            "failedRegionHospitals": failed_hospitals,
            "fallbackCandidateHospitals": 0,
            "freshFallbackHospitals": 0,
            "maskedFailedRegionHospitals": failed_hospitals,
        }

    fallback = previous.copy()
    for column in columns:
        if column not in fallback.columns:
            fallback[column] = pd.NA
    fallback = fallback[
        fallback["기관코드"].astype("string").isin(failed_codes)
    ][columns].drop_duplicates("기관코드", keep="last")
    fallback["기관코드"] = fallback["기관코드"].astype("string").str.strip()
    fallback["가용병상"] = pd.to_numeric(fallback["가용병상"], errors="coerce")
    fallback["전체병상"] = pd.to_numeric(fallback["전체병상"], errors="coerce")

    source_fresh = fresh_bed_source_mask(
        fallback["API기준시각"],
        reference=reference,
    )
    values_valid = fallback["전체병상"].gt(0) & fallback["가용병상"].ge(0)
    usable_fallback = source_fresh & values_valid
    fallback.loc[~usable_fallback, ["가용병상", "전체병상"]] = pd.NA
    fresh_fallback_hospitals = int(usable_fallback.sum())
    return fallback, {
        "failedRegionHospitals": failed_hospitals,
        "fallbackCandidateHospitals": len(fallback),
        "freshFallbackHospitals": fresh_fallback_hospitals,
        "maskedFailedRegionHospitals": failed_hospitals - fresh_fallback_hospitals,
    }


def build_bed_snapshot(
    master: pd.DataFrame,
    records: list[dict],
    failures: list[RegionFailure],
    previous: pd.DataFrame | None,
    *,
    collected_at: pd.Timestamp | datetime | None = None,
    minimum_live_matches: int = MIN_LIVE_MATCHES,
) -> tuple[pd.DataFrame, dict]:
    raw = pd.DataFrame(records)
    if raw.empty:
        raise RuntimeError("실시간 병상 데이터가 한 건도 수집되지 않았습니다.")
    raw = raw.drop_duplicates("hpid", keep="last")
    timestamp = pd.Timestamp.now(tz="UTC") if collected_at is None else pd.Timestamp(collected_at)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    new_bed = pd.DataFrame(
        {
            "기관코드": raw["hpid"].astype("string").str.strip(),
            "가용병상": pd.to_numeric(raw.get(AVAILABLE_FIELD), errors="coerce"),
            "전체병상": pd.to_numeric(raw.get(TOTAL_FIELD), errors="coerce"),
            "API기준시각": raw.get("hvidate", ""),
            "수집시각": timestamp.isoformat(timespec="seconds"),
        }
    )

    previous_live_matches = previous_response_count(previous)
    live_matches = int(master["기관코드"].isin(set(new_bed["기관코드"])).sum())
    minimum_matches = max(minimum_live_matches, int(previous_live_matches * 0.9))
    if live_matches < minimum_matches:
        raise RuntimeError(
            "실시간 병상 응답 기관 수가 정상 스냅샷보다 급감해 기존 산출물을 보존합니다: "
            f"matched={live_matches}, required>={minimum_matches}, previous={previous_live_matches}"
        )

    fallback, fallback_audit = failed_region_fallback(
        master,
        previous,
        failures,
        reference=timestamp,
    )
    fallback = fallback[~fallback["기관코드"].isin(set(new_bed["기관코드"]))]
    bed = (
        new_bed.copy()
        if fallback.empty
        else pd.concat([new_bed, fallback], ignore_index=True)
    )
    bed["가용병상"] = pd.to_numeric(bed["가용병상"], errors="coerce")
    bed["전체병상"] = pd.to_numeric(bed["전체병상"], errors="coerce")
    source_fresh = fresh_bed_source_mask(
        bed["API기준시각"],
        reference=timestamp,
    )
    stale_source_records = int((~source_fresh).sum())
    bed.loc[~source_fresh, ["가용병상", "전체병상"]] = pd.NA
    bed["포화율"] = ((bed["전체병상"] - bed["가용병상"]) / bed["전체병상"] * 100).clip(0, 100)
    valid = bed["전체병상"].gt(0) & bed["가용병상"].ge(0)
    bed.loc[~valid, "포화율"] = pd.NA
    bed["상태"] = pd.cut(
        bed["포화율"],
        bins=[-0.001, 50, 80, 100],
        labels=["여유", "주의", "포화"],
        include_lowest=True,
    ).astype("string").fillna("결측")
    usable_matches = int(master["기관코드"].isin(set(bed.loc[valid, "기관코드"])).sum())
    if usable_matches < minimum_live_matches:
        raise RuntimeError(
            "원천 기준시각까지 유효한 병상 기관 수가 검토 기준보다 적어 기존 산출물을 보존합니다: "
            f"usable={usable_matches}, required>={minimum_live_matches}, "
            f"stale_source={stale_source_records}"
        )

    columns = ["기관코드", "병원명", "등급", "시도", "시군구", "가용병상", "전체병상", "포화율", "상태", "API기준시각", "수집시각"]
    result = master.merge(bed, on="기관코드", how="left").reindex(columns=columns)
    result["상태"] = result["상태"].fillna("결측")
    requested_regions = len(master[["시도", "시군구"]].dropna().drop_duplicates())
    audit = {
        "schemaVersion": 1,
        "collectedAt": timestamp.isoformat(timespec="seconds"),
        "requestedRegions": requested_regions,
        "successfulRegions": requested_regions - len(failures),
        "failedRegionCount": len(failures),
        "failedRegions": [failure.audit_record() for failure in failures],
        "newResponseHospitals": live_matches,
        "previousResponseHospitals": previous_live_matches,
        "responseGuardRequiredHospitals": minimum_matches,
        "usableHospitals": usable_matches,
        "staleSourceHospitals": stale_source_records,
        **fallback_audit,
    }
    return result, audit


def main() -> None:
    master = read_csv(MASTER)
    previous = read_csv(OUTPUT) if OUTPUT.exists() else None
    regions = master[["시도", "시군구"]].dropna().drop_duplicates()
    region_pairs = list(regions.itertuples(index=False, name=None))
    records, failures = collect_regions(region_pairs)
    if failures:
        print(f"Bed API region failures: count={len(failures)}", flush=True)
        for failure in failures:
            print(f"  {failure}", flush=True)

    result, audit = build_bed_snapshot(
        master,
        records,
        failures,
        previous,
        collected_at=datetime.now().astimezone(),
    )

    if failures:
        print(
            "Partial bed refresh: "
            f"failed_regions={len(failures)}, "
            f"fresh_fallback_hospitals={audit['freshFallbackHospitals']}, "
            f"masked_failed_region_hospitals={audit['maskedFailedRegionHospitals']}",
            flush=True,
        )

    save_csv(result, OUTPUT)
    save_json(audit, AUDIT)

    history_row = result.copy()
    if HISTORY.exists():
        history = pd.read_csv(HISTORY)
        history_row = pd.concat([history, history_row], ignore_index=True)
        history_row = history_row.drop_duplicates(
            ["기관코드", "수집시각"],
            keep="last",
        )
    history_row = trim_history(history_row)
    save_csv(history_row, HISTORY)
    print(
        f"Saved {len(result):,} hospitals "
        f"({audit['newResponseHospitals']:,} new responses, "
        f"{audit['freshFallbackHospitals']:,} fresh fallbacks, "
        f"{audit['usableHospitals']:,} usable, "
        f"{audit['staleSourceHospitals']:,} stale-source excluded): {OUTPUT}"
    )


if __name__ == "__main__":
    main()
