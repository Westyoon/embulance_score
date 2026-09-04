import argparse
import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
from uuid import uuid4

import pandas as pd

from common import ROOT, read_csv, save_csv
from part2_collect_bed_status import bed_source_max_age_hours, fresh_bed_source_mask

LIVE_DATA = Path(os.getenv("PIPELINE_LIVE_DATA_DIR", ROOT / "data")).resolve()
LIVE_BOUNDARY = Path(
    os.getenv("BOUNDARY_FILE", ROOT / "src" / "data" / "koreaGeo.json")
).resolve()
PIPELINE_STATE_DIR = Path(os.getenv("PIPELINE_STATE_DIR", ROOT)).resolve()
FULL_BED_REUSE_MARKER = PIPELINE_STATE_DIR / "full_bed_reuse.json"
MANAGED_INPUT_FILENAMES = (
    "hira_match_exclusions.csv",
    "hira_match_overrides.csv",
    "hospital_coordinate_overrides.csv",
    "hospital_region_overrides.csv",
)
MIN_LIVE_MATCHES = 373
MAX_BED_FUTURE_SKEW_MINUTES = 5.0
BED_COLUMNS = [
    "기관코드",
    "병원명",
    "등급",
    "시도",
    "시군구",
    "가용병상",
    "전체병상",
    "포화율",
    "상태",
    "API기준시각",
    "수집시각",
]
BED_VALUE_COLUMNS = [
    "기관코드",
    "가용병상",
    "전체병상",
    "포화율",
    "상태",
    "API기준시각",
    "수집시각",
]


def reuse_beds_enabled() -> bool:
    raw = os.getenv("FULL_REFRESH_REUSE_BEDS", "true").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    raise RuntimeError(
        "FULL_REFRESH_REUSE_BEDS는 true/false 형식이어야 합니다. "
        "잘못된 설정에서는 병상 API를 호출하지 않습니다."
    )


def bed_snapshot_max_age_hours() -> float:
    raw = os.getenv("FULL_REFRESH_BED_MAX_AGE_HOURS", "12").strip()
    try:
        value = float(raw)
    except ValueError:
        raise RuntimeError("FULL_REFRESH_BED_MAX_AGE_HOURS는 양수여야 합니다.") from None
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError("FULL_REFRESH_BED_MAX_AGE_HOURS는 양수여야 합니다.")
    return value


def validate_reusable_bed_snapshot(
    master_path: Path,
    bed_path: Path,
    *,
    max_age_hours: float,
    max_source_age_hours: float | None = None,
    minimum_usable_hospitals: int = MIN_LIVE_MATCHES,
    now: pd.Timestamp | None = None,
    max_future_skew_minutes: float = MAX_BED_FUTURE_SKEW_MINUTES,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Validate a copied live bed snapshot and rebase only its master metadata.

    This deliberately raises on any validation failure. When reuse is configured,
    the full refresh must never fall back to the quota-consuming bed API.
    """
    master = read_csv(master_path)
    beds = read_csv(bed_path)
    missing_master = set(BED_COLUMNS[:5]) - set(master.columns)
    missing_beds = set(BED_COLUMNS) - set(beds.columns)
    if missing_master:
        raise RuntimeError(f"신규 NEMC 마스터 필수 컬럼이 없습니다: {sorted(missing_master)}")
    if missing_beds:
        raise RuntimeError(f"재사용 병상 스냅샷 필수 컬럼이 없습니다: {sorted(missing_beds)}")

    master_codes = master["기관코드"].astype("string").str.strip()
    bed_codes = beds["기관코드"].astype("string").str.strip()
    if master_codes.isna().any() or master_codes.eq("").any() or master_codes.duplicated().any():
        raise RuntimeError("신규 NEMC 마스터 기관코드가 비었거나 중복되었습니다.")
    if bed_codes.isna().any() or bed_codes.eq("").any() or bed_codes.duplicated().any():
        raise RuntimeError("재사용 병상 스냅샷 기관코드가 비었거나 중복되었습니다.")
    if set(master_codes) != set(bed_codes):
        missing = sorted(set(master_codes) - set(bed_codes))[:5]
        extra = sorted(set(bed_codes) - set(master_codes))[:5]
        raise RuntimeError(
            "재사용 병상 스냅샷이 신규 NEMC 모집단과 정확히 일치하지 않습니다: "
            f"missing={missing}, extra={extra}"
        )

    reference = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if reference.tzinfo is None:
        reference = reference.tz_localize("UTC")
    else:
        reference = reference.tz_convert("UTC")
    available = pd.to_numeric(beds["가용병상"], errors="coerce")
    total = pd.to_numeric(beds["전체병상"], errors="coerce")
    saturation = pd.to_numeric(beds["포화율"], errors="coerce")
    source_valid = total.gt(0) & available.ge(0)
    expected_saturation = ((total - available) / total * 100).clip(0, 100)
    saturation_sane = saturation.between(0, 100) & saturation.sub(expected_saturation).abs().le(1e-8)
    if (saturation.notna() & ~(source_valid & saturation_sane)).any():
        raise RuntimeError("재사용 병상 스냅샷에 원천 병상값과 맞지 않는 포화율이 있습니다.")
    source_age_limit = (
        bed_source_max_age_hours()
        if max_source_age_hours is None
        else max_source_age_hours
    )
    source_fresh = fresh_bed_source_mask(
        beds["API기준시각"],
        reference=reference,
        max_age_hours=source_age_limit,
        max_future_skew_minutes=max_future_skew_minutes,
    )
    api_timestamp_text = beds["API기준시각"].astype("string").str.strip()
    source_observed = api_timestamp_text.notna() & api_timestamp_text.ne("")
    usable = source_valid & saturation_sane & source_fresh
    usable_hospitals = int(usable.sum())
    if usable_hospitals < minimum_usable_hospitals:
        raise RuntimeError(
            "재사용 병상 스냅샷의 유효 기관 수가 검토 기준보다 적습니다: "
            f"usable={usable_hospitals}, required>={minimum_usable_hospitals}"
        )

    timestamp_text = beds["수집시각"].astype("string").str.strip()
    has_timestamp = timestamp_text.notna() & timestamp_text.ne("")
    timestamps = pd.to_datetime(timestamp_text, errors="coerce", utc=True)
    if (has_timestamp & timestamps.isna()).any() or timestamps[usable].isna().any():
        raise RuntimeError("재사용 병상 스냅샷의 수집시각이 비었거나 해석할 수 없습니다.")
    present_timestamps = timestamps[has_timestamp]
    if present_timestamps.empty:
        raise RuntimeError("재사용 병상 스냅샷에 수집시각이 없습니다.")

    future_limit = reference + pd.Timedelta(minutes=max_future_skew_minutes)
    if present_timestamps.gt(future_limit).any():
        newest = present_timestamps.max()
        raise RuntimeError(
            "재사용 병상 스냅샷 수집시각이 허용된 미래 오차를 초과합니다: "
            f"newest={newest.isoformat()}, now={reference.isoformat()}"
        )
    snapshot_collected_at = present_timestamps.min()
    snapshot_age = reference - snapshot_collected_at
    if snapshot_age > pd.Timedelta(hours=max_age_hours):
        raise RuntimeError(
            "재사용 병상 스냅샷이 너무 오래되었습니다. 병상 API를 대신 호출하지 않고 종료합니다: "
            f"oldest={snapshot_collected_at.isoformat()}, age={snapshot_age}, "
            f"max={max_age_hours}h"
        )

    beds = beds.copy()
    beds["기관코드"] = bed_codes
    beds.loc[~source_fresh, ["가용병상", "전체병상", "포화율"]] = pd.NA
    beds.loc[~source_fresh, "상태"] = "결측"
    master = master.copy()
    master["기관코드"] = master_codes
    rebased = master[BED_COLUMNS[:5]].merge(
        beds[BED_VALUE_COLUMNS],
        on="기관코드",
        how="left",
        validate="one_to_one",
    ).reindex(columns=BED_COLUMNS)
    audit = {
        "reused": True,
        "snapshotCollectedAt": snapshot_collected_at.isoformat(),
        "snapshotAgeMinutes": round(max(snapshot_age.total_seconds(), 0.0) / 60, 3),
        "usableHospitals": usable_hospitals,
        "staleSourceHospitals": int((source_observed & ~source_fresh).sum()),
        "sanitizedSourceHospitals": int((source_valid & ~source_fresh).sum()),
        "maxAgeHours": max_age_hours,
        "sourceMaxAgeHours": source_age_limit,
    }
    return rebased, audit


def write_bed_reuse_marker(audit: dict[str, object]) -> None:
    marker = {
        **audit,
        "recordedAt": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    temporary = FULL_BED_REUSE_MARKER.with_suffix(f".json.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, FULL_BED_REUSE_MARKER)


def install_shutdown_handler() -> None:
    def terminate(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt("파이프라인 종료 신호를 받았습니다.")

    signal.signal(signal.SIGTERM, terminate)


def run(command: list[str], environment: dict[str, str]) -> None:
    print(f"Running: {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def build_pipeline_environment(
    staged_data: Path,
    staged_boundary: Path,
    period: str | None,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PIPELINE_DATA_DIR"] = str(staged_data)
    environment["PIPELINE_STATE_DIR"] = str(PIPELINE_STATE_DIR)
    environment["BOUNDARY_OUTPUT"] = str(staged_boundary)
    environment["BOUNDARY_FILE"] = str(staged_boundary)
    if period:
        environment["PIPELINE_POPULATION_PERIOD"] = period
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    return environment


def copy_managed_inputs(
    staged_data: Path,
    source_data: Path | None = None,
) -> None:
    """Copy image-managed inputs into a full-refresh staging generation.

    These files affect downstream HIRA matching and hospital normalization, so
    they must never be copied directly into the persistent live generation.
    A full refresh recalculates every dependent artifact in staging and promotes
    the inputs and outputs together only after the complete contract passes.
    """
    source = ROOT / "data" if source_data is None else source_data
    missing = [
        filename
        for filename in MANAGED_INPUT_FILENAMES
        if not (source / filename).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "전체 갱신 관리 입력 파일이 없습니다: " + ", ".join(missing)
        )
    for filename in MANAGED_INPUT_FILENAMES:
        shutil.copy2(source / filename, staged_data / filename)


def committed_backup_path(backup_path: Path) -> Path:
    return backup_path.with_name(f"committed-{backup_path.name.lstrip('.')}")


def restore_uncommitted_data(backup_data: Path, staging_root: Path) -> None:
    """Restore the old data without ever discarding its recovery backup.

    A failed commit can leave both the newly promoted live directory and the old
    backup present. Move the uncommitted live directory aside first, then put
    the backup back. If either rename fails, the backup remains available for a
    later cleanup/recovery attempt.
    """
    if not backup_data.exists():
        return
    if LIVE_DATA.exists():
        failed_data = staging_root / f"failed-live-data-{uuid4().hex}"
        os.replace(LIVE_DATA, failed_data)
    os.replace(backup_data, LIVE_DATA)


def promote(
    staged_data: Path,
    staged_boundary: Path,
    backup_data: Path,
    backup_boundary: Path,
) -> None:
    committed_data = committed_backup_path(backup_data)
    committed_boundary = committed_backup_path(backup_boundary)
    boundary_backup_temporary = backup_boundary.with_name(
        f"{backup_boundary.name}.{uuid4().hex}.tmp"
    )
    data_backed_up = False
    try:
        shutil.copy2(LIVE_BOUNDARY, boundary_backup_temporary)
        os.replace(boundary_backup_temporary, backup_boundary)
    except BaseException:
        try:
            boundary_backup_temporary.unlink(missing_ok=True)
        except BaseException:
            pass
        raise
    try:
        os.replace(LIVE_DATA, backup_data)
        data_backed_up = True
        os.replace(staged_data, LIVE_DATA)
        os.replace(staged_boundary, LIVE_BOUNDARY)
        # Renaming the data backup out of the recovery namespace is the commit point.
        # Startup recovery only considers `.pipeline-backup-*` directories.
        os.replace(backup_data, committed_data)
    except BaseException:
        # The atomic backup rename above is the durable commit marker. A signal
        # delivered immediately after it must never enter the rollback path.
        if committed_data.exists():
            return
        if data_backed_up:
            restore_uncommitted_data(backup_data, staged_data.parent)
        if backup_boundary.exists():
            os.replace(backup_boundary, LIVE_BOUNDARY)
        raise

    # Move the boundary backup out of the recovery namespace too. The committed
    # data directory deliberately remains until the caller's finally block so it
    # can detect a signal between this function's commit point and return.
    try:
        os.replace(backup_boundary, committed_boundary)
    except BaseException:
        pass


def cleanup_pipeline_run(
    *,
    staging_root: Path,
    backup_data: Path,
    backup_boundary: Path,
    lock_fd: int,
    lock_path: Path,
    promotion_committed: bool,
) -> None:
    """Recover an incomplete promotion or clean up a committed run.

    Once ``promote`` returns, the live data has crossed its commit point. Any
    later cleanup error must not make the scheduler report that committed data as
    a failed refresh. Before that point, restoration errors remain fatal so an
    operator cannot mistake an uncertain live state for a clean rollback.
    """
    committed_data = committed_backup_path(backup_data)
    committed_boundary = committed_backup_path(backup_boundary)
    if promotion_committed:
        cleanup_actions = (
            lambda: shutil.rmtree(committed_data) if committed_data.exists() else None,
            lambda: committed_boundary.unlink(missing_ok=True),
            lambda: shutil.rmtree(backup_data) if backup_data.exists() else None,
            lambda: backup_boundary.unlink(missing_ok=True),
            lambda: shutil.rmtree(staging_root) if staging_root.exists() else None,
            lambda: os.close(lock_fd),
            lambda: lock_path.unlink(missing_ok=True),
        )
        for cleanup in cleanup_actions:
            try:
                cleanup()
            except BaseException:
                pass
        return

    try:
        restore_uncommitted_data(backup_data, staging_root)
        # Before the data-backup commit rename, this is always the verified old
        # boundary. Restore it even when the new live boundary exists.
        if backup_boundary.exists():
            os.replace(backup_boundary, LIVE_BOUNDARY)
        if staging_root.exists():
            shutil.rmtree(staging_root)
    finally:
        try:
            os.close(lock_fd)
        finally:
            lock_path.unlink(missing_ok=True)


def main() -> None:
    install_shutdown_handler()
    parser = argparse.ArgumentParser(
        description="모든 산출물을 staging에서 검증한 뒤 백업과 함께 승격하는 전체 데이터 파이프라인"
    )
    parser.add_argument("period", nargs="?", help="고정할 주민등록 인구 연월(YYYYMM); 생략하면 최신")
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        raise RuntimeError("고정된 분석 라이브러리 환경을 위해 Python 3.12 이상이 필요합니다.")
    if args.period and (len(args.period) != 6 or not args.period.isdigit()):
        raise ValueError("period는 YYYYMM 형식이어야 합니다.")

    PIPELINE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_DATA.parent.mkdir(parents=True, exist_ok=True)
    LIVE_BOUNDARY.parent.mkdir(parents=True, exist_ok=True)
    lock_path = PIPELINE_STATE_DIR / ".pipeline.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"다른 파이프라인 실행 중이거나 이전 lock이 남았습니다: {lock_path}") from exc

    run_id = uuid4().hex
    staging_root = PIPELINE_STATE_DIR / f".pipeline-staging-{run_id}"
    staged_data = staging_root / "data"
    staged_boundary = staging_root / "koreaGeo.json"
    backup_data = PIPELINE_STATE_DIR / f".pipeline-backup-{run_id}"
    backup_boundary = PIPELINE_STATE_DIR / f".pipeline-backup-{run_id}.koreaGeo.json"
    promotion_committed = False
    try:
        os.write(lock_fd, str(os.getpid()).encode("ascii"))
        FULL_BED_REUSE_MARKER.unlink(missing_ok=True)
        shutil.copytree(LIVE_DATA, staged_data)
        copy_managed_inputs(staged_data)
        environment = build_pipeline_environment(staged_data, staged_boundary, args.period)

        python = sys.executable
        npm = shutil.which("npm.cmd") if os.name == "nt" else shutil.which("npm")
        if not npm:
            raise RuntimeError("npm 실행 파일을 찾지 못했습니다.")
        node = shutil.which("node.exe") if os.name == "nt" else shutil.which("node")
        if not node:
            raise RuntimeError("node 실행 파일을 찾지 못했습니다.")

        run([python, "scripts/part1_collect_hospital_master.py"], environment)
        reuse_beds = reuse_beds_enabled()
        reuse_max_age_hours = bed_snapshot_max_age_hours() if reuse_beds else None
        reuse_initial_audit = None
        if reuse_beds:
            print(
                "FULL_REFRESH_REUSE_BEDS=true: 기존 병상 스냅샷을 검증해 재사용하며, "
                "검증 실패 시 API로 대체하지 않고 전체 새로고침을 중단합니다.",
                flush=True,
            )
            rebased_beds, reuse_initial_audit = validate_reusable_bed_snapshot(
                staged_data / "hospital_master.csv",
                staged_data / "bed_status.csv",
                max_age_hours=reuse_max_age_hours,
            )
            # 병상 이력에는 재사용 행을 추가하지 않고 현재 마스터 메타데이터만 갱신한다.
            save_csv(rebased_beds, staged_data / "bed_status.csv")
        else:
            run([python, "scripts/part2_collect_bed_status.py"], environment)
        population_command = [python, "scripts/part3_collect_population.py"]
        prepare_command = [python, "scripts/part3_prepare_population.py"]
        if args.period:
            population_command.extend(["--period", args.period])
            prepare_command.extend(["--period", args.period])
        run(population_command, environment)
        run(prepare_command, environment)
        run([python, "scripts/part3_collect_hira_doctors.py"], environment)
        # 카카오 경로의 지역 대표점과 지도 경계가 같은 버전을 보도록 경계를 먼저 갱신한다.
        run([npm, "run", "update:boundaries"], environment)
        run([python, "scripts/part3_collect_kakao_routes.py"], environment)
        reuse_sanitized_sources = 0
        if reuse_beds:
            # HIRA/Kakao 수집 중 새로 만료된 원천 병상값을 점수 계산 전에 다시
            # 제거한다. 전역 유효기관 하한을 통과하지 못하면 기존 운영본을 유지한다.
            rebased_beds, pre_analysis_audit = validate_reusable_bed_snapshot(
                staged_data / "hospital_master.csv",
                staged_data / "bed_status.csv",
                max_age_hours=reuse_max_age_hours,
            )
            reuse_sanitized_sources = int(
                reuse_initial_audit["sanitizedSourceHospitals"]
            ) + int(pre_analysis_audit["sanitizedSourceHospitals"])
            save_csv(rebased_beds, staged_data / "bed_status.csv")
        run([python, "scripts/part3_build_component_scores.py"], environment)
        run([python, "scripts/part3_calculate_region_risk.py"], environment)
        run([python, "scripts/build_missingness_report.py"], environment)
        run([python, "scripts/part4_analyze.py"], environment)
        run([python, "scripts/validate_data_contract.py"], environment)
        run([node, "scripts/validate_frontend_data.mjs"], environment)
        reuse_audit = None
        if reuse_beds:
            # 긴 전체 파이프라인 동안 오래된 스냅샷이 승격되지 않도록 승격 직전에 다시 확인한다.
            _, reuse_audit = validate_reusable_bed_snapshot(
                staged_data / "hospital_master.csv",
                staged_data / "bed_status.csv",
                max_age_hours=reuse_max_age_hours,
            )
            newly_stale_sources = int(reuse_audit["sanitizedSourceHospitals"])
            if newly_stale_sources:
                raise RuntimeError(
                    "전체 갱신 실행 중 병원 원천 병상 기준시각이 만료되었습니다. "
                    f"newly_stale={newly_stale_sources}; 새 병상 갱신 후 다시 실행하세요."
                )
            reuse_audit["sanitizedSourceHospitals"] = reuse_sanitized_sources
        if reuse_audit is not None:
            write_bed_reuse_marker(reuse_audit)
        print(f"Pipeline validated; promoting run_id={run_id}", flush=True)
        promote(staged_data, staged_boundary, backup_data, backup_boundary)
        promotion_committed = True
    finally:
        # The committed backup directory is an atomic on-disk witness, closing
        # the signal window between promote()'s commit syscall and this assignment.
        promotion_committed = promotion_committed or committed_backup_path(backup_data).exists()
        cleanup_pipeline_run(
            staging_root=staging_root,
            backup_data=backup_data,
            backup_boundary=backup_boundary,
            lock_fd=lock_fd,
            lock_path=lock_path,
            promotion_committed=promotion_committed,
        )


if __name__ == "__main__":
    main()
