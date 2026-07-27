@echo off
setlocal
cd /d "%~dp0"
python scripts\part1_collect_hospital_master.py || exit /b 1
python scripts\part2_collect_bed_status.py || exit /b 1
python scripts\part3_prepare_population.py || exit /b 1
python scripts\part3_collect_hira_doctors.py || exit /b 1
python scripts\part3_collect_kakao_routes.py || exit /b 1
python scripts\part3_build_component_scores.py || exit /b 1
python scripts\part3_calculate_region_risk.py || exit /b 1
python scripts\part4_analyze.py || exit /b 1
echo Pipeline completed.
