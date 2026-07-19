# embulance_score

전국 응급의료기관의 실시간 병상 현황과 시군구별 의료 접근 위험도를 계산하는 데이터 파이프라인입니다.

## 설치 및 인증

```powershell
python -m pip install -r requirements.txt
```

프로젝트 루트의 `.env`:

```env
DATA_GO_KR_API_KEY=공공데이터포털_일반인증키_Decoding
```

스크립트가 `.env`를 직접 읽으므로 VS Code의 `python.terminal.useEnvFile` 설정은 필요하지 않습니다.

## 실행

```powershell
.\run_pipeline.bat
```

개별 실행 순서는 다음과 같습니다.

```powershell
python scripts\part1_collect_hospital_master.py
python scripts\part2_collect_bed_status.py
python scripts\part3_build_component_scores.py
python scripts\part3_calculate_region_risk.py
python scripts\part4_analyze.py
```

## 추가 원천 데이터

NEMC API 키만으로는 KOSIS 인구와 HIRA 전문의 데이터에 접근할 수 없습니다. 아래 파일을 `data` 폴더에 넣으면 PART 3이 자동으로 결합합니다.

- `population_source.csv`: `시도,시군구,인구`
- `doctor_source.csv`: `시도,시군구,응급의학과전문의수`
- `region_centroids.csv`(선택): `시도,시군구,위도,경도`

`region_centroids.csv`가 없으면 지역 내 병원 좌표 평균을 중심점 대체값으로 사용하며 결과의 `중심점방법`에 표시합니다. 인구 또는 전문의 원천이 없을 때는 임의 점수를 채우지 않으며, `region_risk_final.csv`의 `산출상태`가 `원천데이터부족`이 됩니다.

## 결과 파일

- `hospital_master.csv`: 병원 기본정보
- `bed_status.csv`: 현재 가용·전체 응급실 병상, 포화율, 상태
- `bed_status_history.csv`: 실행할 때마다 누적되는 병상 스냅샷
- `accessibility_score.csv`: 최근접 응급의료센터 거리와 접근성 점수
- `population_bed_score.csv`: 인구 대비 병상 점수(원천 제공 시)
- `doctor_score.csv`: 병상 대비 응급의학과 전문의 부족 점수(원천 제공 시)
- `region_risk_final.csv`: 네 구성점수와 최종 `regionRisk`
- `heatmap_matrix.csv`: 요일×시간 평균 포화율
- `regression_result.csv`, `regression_metrics.json`: 회귀계수와 성능
- `correlation_matrix.csv`, `vif_result.csv`: 상관관계와 다중공선성 진단
- `cluster_result.csv`, `cluster_profile.csv`: K-Means 지역 유형과 군집 특성
- `cluster_k_evaluation.csv`: k=2~8 실루엣 점수 비교

## 산식

```text
포화율 = (전체 응급실 병상 - 가용 응급실 병상) / 전체 응급실 병상 × 100
regionRisk = 0.35×병상포화도 + 0.30×접근성 + 0.20×인구대비병상 + 0.15×의료진부족
```

결측 병상은 0으로 해석하지 않습니다. 네 구성점수가 모두 존재할 때만 최종 위험도를 산출합니다.
