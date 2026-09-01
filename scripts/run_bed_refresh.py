import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

from run_pipeline import (
    LIVE_DATA,
    PIPELINE_STATE_DIR,
    ROOT,
    committed_backup_path,
    install_shutdown_handler,
    run,
)


def promote_data(staged_data: Path, backup_data: Path) -> None:
    committed_data = committed_backup_path(backup_data)
    data_backed_up = False
    try:
        os.replace(LIVE_DATA, backup_data)
        data_backed_up = True
        os.replace(staged_data, LIVE_DATA)
        # Moving the old data out of the startup-recovery namespace is the
        # durable commit point for a beds-only refresh.
        os.replace(backup_data, committed_data)
    except BaseException:
        if committed_data.exists():
            return
        if data_backed_up:
            restore_uncommitted_data(backup_data, staged_data.parent)
        raise


def restore_uncommitted_data(backup_data: Path, staging_root: Path) -> None:
    """Restore the old data while preserving its backup on any failed retry."""
    if not backup_data.exists():
        return
    if LIVE_DATA.exists():
        failed_data = staging_root / f"failed-live-data-{uuid4().hex}"
        os.replace(LIVE_DATA, failed_data)
    os.replace(backup_data, LIVE_DATA)


def cleanup_bed_refresh_run(
    *,
    staging_root: Path,
    backup_data: Path,
    lock_fd: int,
    lock_path: Path,
    promotion_committed: bool,
) -> None:
    committed_data = committed_backup_path(backup_data)
    if promotion_committed:
        cleanup_actions = (
            lambda: shutil.rmtree(committed_data) if committed_data.exists() else None,
            lambda: shutil.rmtree(backup_data) if backup_data.exists() else None,
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
        if staging_root.exists():
            shutil.rmtree(staging_root)
    finally:
        try:
            os.close(lock_fd)
        finally:
            lock_path.unlink(missing_ok=True)


def main() -> None:
    install_shutdown_handler()
    if sys.version_info < (3, 12):
        raise RuntimeError("고정된 분석 라이브러리 환경을 위해 Python 3.12 이상이 필요합니다.")
    if not LIVE_DATA.exists():
        raise FileNotFoundError(f"운영 데이터 디렉터리가 없습니다: {LIVE_DATA}")

    PIPELINE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = PIPELINE_STATE_DIR / ".pipeline.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("다른 데이터 갱신 작업이 실행 중입니다.") from exc

    run_id = uuid4().hex
    staging_root = PIPELINE_STATE_DIR / f".bed-refresh-staging-{run_id}"
    staged_data = staging_root / "data"
    backup_data = PIPELINE_STATE_DIR / f".bed-refresh-backup-{run_id}"
    promotion_committed = False
    try:
        os.write(lock_fd, str(os.getpid()).encode("ascii"))
        shutil.copytree(LIVE_DATA, staged_data)
        environment = os.environ.copy()
        environment["PIPELINE_DATA_DIR"] = str(staged_data)
        environment.setdefault("PYTHONIOENCODING", "utf-8")

        python = sys.executable
        node = shutil.which("node.exe") if os.name == "nt" else shutil.which("node")
        if not node:
            raise RuntimeError("node 실행 파일을 찾지 못했습니다.")

        # A beds-only refresh must inherit one already validated data generation.
        # Fail before the quota-consuming NEMC calls if managed inputs and their
        # dependent HIRA artifacts (or any other live contract) are inconsistent.
        run([python, "scripts/validate_data_contract.py"], environment)
        run([python, "scripts/part2_collect_bed_status.py"], environment)
        run([python, "scripts/part3_build_component_scores.py"], environment)
        run([python, "scripts/part3_calculate_region_risk.py"], environment)
        run([python, "scripts/build_missingness_report.py"], environment)
        run([python, "scripts/part4_analyze.py"], environment)
        run([python, "scripts/validate_data_contract.py"], environment)
        run([node, "scripts/validate_frontend_data.mjs"], environment)
        print(f"Bed refresh validated; promoting run_id={run_id}", flush=True)
        promote_data(staged_data, backup_data)
        promotion_committed = True
    finally:
        promotion_committed = promotion_committed or committed_backup_path(backup_data).exists()
        cleanup_bed_refresh_run(
            staging_root=staging_root,
            backup_data=backup_data,
            lock_fd=lock_fd,
            lock_path=lock_path,
            promotion_committed=promotion_committed,
        )


if __name__ == "__main__":
    main()
