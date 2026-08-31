import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

from run_pipeline import LIVE_DATA, PIPELINE_STATE_DIR, ROOT, install_shutdown_handler, run


def promote_data(staged_data: Path, backup_data: Path) -> None:
    failed_data = PIPELINE_STATE_DIR / f"failed-bed-refresh-{uuid4().hex}"
    data_backed_up = False
    try:
        os.replace(LIVE_DATA, backup_data)
        data_backed_up = True
        os.replace(staged_data, LIVE_DATA)
    except BaseException:
        if data_backed_up:
            if LIVE_DATA.exists():
                os.replace(LIVE_DATA, failed_data)
            os.replace(backup_data, LIVE_DATA)
        raise
    shutil.rmtree(backup_data)


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

        run([python, "scripts/part2_collect_bed_status.py"], environment)
        run([python, "scripts/part3_build_component_scores.py"], environment)
        run([python, "scripts/part3_calculate_region_risk.py"], environment)
        run([python, "scripts/part4_analyze.py"], environment)
        run([python, "scripts/validate_data_contract.py"], environment)
        run([node, "scripts/validate_frontend_data.mjs"], environment)
        promote_data(staged_data, backup_data)
        print(f"Bed refresh completed after staged validation: run_id={run_id}")
    finally:
        try:
            if backup_data.exists() and not LIVE_DATA.exists():
                os.replace(backup_data, LIVE_DATA)
            elif backup_data.exists():
                shutil.rmtree(backup_data)
            if staging_root.exists():
                shutil.rmtree(staging_root)
        finally:
            os.close(lock_fd)
            lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
