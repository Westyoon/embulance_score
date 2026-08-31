import argparse
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
from uuid import uuid4

from common import ROOT

LIVE_DATA = Path(os.getenv("PIPELINE_LIVE_DATA_DIR", ROOT / "data")).resolve()
LIVE_BOUNDARY = Path(
    os.getenv("BOUNDARY_FILE", ROOT / "src" / "data" / "koreaGeo.json")
).resolve()
PIPELINE_STATE_DIR = Path(os.getenv("PIPELINE_STATE_DIR", ROOT)).resolve()


def install_shutdown_handler() -> None:
    def terminate(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt("파이프라인 종료 신호를 받았습니다.")

    signal.signal(signal.SIGTERM, terminate)


def run(command: list[str], environment: dict[str, str]) -> None:
    print(f"Running: {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def promote(
    staged_data: Path,
    staged_boundary: Path,
    backup_data: Path,
    backup_boundary: Path,
) -> None:
    failed_data = staged_data.parent / "failed-promoted-data"
    data_backed_up = False
    shutil.copy2(LIVE_BOUNDARY, backup_boundary)
    try:
        os.replace(LIVE_DATA, backup_data)
        data_backed_up = True
        os.replace(staged_data, LIVE_DATA)
        os.replace(staged_boundary, LIVE_BOUNDARY)
    except BaseException:
        if data_backed_up:
            if LIVE_DATA.exists():
                os.replace(LIVE_DATA, failed_data)
            os.replace(backup_data, LIVE_DATA)
        if backup_boundary.exists():
            os.replace(backup_boundary, LIVE_BOUNDARY)
        raise
    shutil.rmtree(backup_data)
    backup_boundary.unlink(missing_ok=True)


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
    try:
        os.write(lock_fd, str(os.getpid()).encode("ascii"))
        shutil.copytree(LIVE_DATA, staged_data)
        environment = os.environ.copy()
        environment["PIPELINE_DATA_DIR"] = str(staged_data)
        environment["BOUNDARY_OUTPUT"] = str(staged_boundary)
        environment["BOUNDARY_FILE"] = str(staged_boundary)
        if args.period:
            environment["PIPELINE_POPULATION_PERIOD"] = args.period
        environment.setdefault("PYTHONIOENCODING", "utf-8")

        python = sys.executable
        npm = shutil.which("npm.cmd") if os.name == "nt" else shutil.which("npm")
        if not npm:
            raise RuntimeError("npm 실행 파일을 찾지 못했습니다.")
        node = shutil.which("node.exe") if os.name == "nt" else shutil.which("node")
        if not node:
            raise RuntimeError("node 실행 파일을 찾지 못했습니다.")

        run([python, "scripts/part1_collect_hospital_master.py"], environment)
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
        run([python, "scripts/part3_build_component_scores.py"], environment)
        run([python, "scripts/part3_calculate_region_risk.py"], environment)
        run([python, "scripts/part4_analyze.py"], environment)
        run([python, "scripts/validate_data_contract.py"], environment)
        run([node, "scripts/validate_frontend_data.mjs"], environment)
        promote(staged_data, staged_boundary, backup_data, backup_boundary)
        print(f"Pipeline completed after staged validation: run_id={run_id}")
    finally:
        try:
            if backup_data.exists() and not LIVE_DATA.exists():
                os.replace(backup_data, LIVE_DATA)
            if backup_boundary.exists() and not LIVE_BOUNDARY.exists():
                os.replace(backup_boundary, LIVE_BOUNDARY)
            elif backup_boundary.exists():
                backup_boundary.unlink()
            if staging_root.exists():
                shutil.rmtree(staging_root)
        finally:
            os.close(lock_fd)
            lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
