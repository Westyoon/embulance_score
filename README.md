# embulance_score

전국 응급의료기관의 실시간 병상 현황과 지역별 의료 접근 위험도를 분석하는 데이터 파이프라인입니다.

## 현재 진행 상황

2026-07-19 실행 결과 기준입니다.

| 단계 | 상태 | 현재 결과 |
|---|---|---|
| PART 1 병원 마스터 | 완료 | 전국 응급의료기관 534개 |
| PART 2 실시간 병상 | 완료 | API 매칭 415개, 유효 포화율 392개 |
| PART 3 접근성 | 완료 | 219개 시군구 |
| PART 3 인구 대비 병상 | 완료 | KOSIS 2024 인구 기준, 215개 지역 매칭 |
| PART 3 의료진 부족 | 부분 완료 | HIRA 병원 415개 자동매칭, 144개 지역 사용 가능 |
| PART 3 최종 regionRisk | 부분 완료 | 219개 중 124개 지역 산출 완료 |
| PART 4 상관관계·VIF | 완료 | 최종 점수가 완성된 지역 대상 |
| PART 4 원천값 회귀 | 완료 | 79개 지역, R² 0.833, MAE 2.70 |
| PART 4 K-Means | 완료 | 124개 지역, 최적 k=2 |
| PART 4 과거 시간대 분석 | 미완료 | 2024년 병상 이력 원자료 필요 |

최종 위험도가 없는 95개 지역은 임의 점수로 대체하지 않고 `원천데이터부족`으로 표시합니다.

## 데이터 출처

- 국립중앙의료원 전국 응급의료기관 정보 조회 서비스
  - 병원 기본정보
  - 현재 시점 실시간 가용 응급실 병상
- KOSIS `행정구역(시군구)별/1세별 주민등록인구`
  - 현재 계산에는 2024년 연간 CSV 사용
- 건강보험심사평가원 병원정보서비스
  - 병원명, 주소, 암호화 요양기호
- 건강보험심사평가원 의료기관별 상세정보서비스
  - 전문과목 코드 24, 응급의학과 전문의 수

## 설치와 인증

Python 3.10 이상을 권장합니다.

```powershell
python -m pip install -r requirements.txt
```

프로젝트 루트의 `.env`에 두 인증키를 설정합니다.

```env
DATA_GO_KR_API_KEY=NEMC_API_DECODING_KEY
HIRA_API_KEY=HIRA_API_DECODING_KEY
```

`.env`는 Git에서 제외됩니다. 스크립트가 파일을 직접 읽으므로 VS Code의 `python.terminal.useEnvFile` 설정은 필요하지 않습니다.

## 실행

전체 파이프라인:

```powershell
.\run_pipeline.bat
```

개별 실행:

```powershell
python scripts\part1_collect_hospital_master.py
python scripts\part2_collect_bed_status.py
python scripts\part3_prepare_population.py
python scripts\part3_collect_hira_doctors.py
python scripts\part3_build_component_scores.py
python scripts\part3_calculate_region_risk.py
python scripts\part4_analyze.py
```

주의: PART 2는 전국 시군구별 API를 호출하고, HIRA 최초 수집은 약 1,000회의 API 요청이 발생할 수 있습니다. HIRA 재실행 시 기존 자동매칭 결과를 재사용하고 미매칭 병원만 다시 조회합니다.

## 분석 산식

### 병상 포화율

```text
포화율 = (기준 응급실 병상 - 가용 응급실 병상) / 기준 응급실 병상 × 100
```

전체 병상이 없거나 0인 경우와 음수 가용병상은 결측으로 처리합니다.

### 지역 위험도

```text
regionRisk =
    0.35 × 병상포화도점수
  + 0.30 × 접근성점수
  + 0.20 × 인구대비병상점수
  + 0.15 × 의료진부족점수
```

네 구성점수가 모두 존재할 때만 `regionRisk`를 산출합니다.

### 점수 정규화

- 접근거리, 인구 대비 병상비율, 전문의 1인당 병상 수는 P5~P95 기준 Min-Max 방식으로 0~100점화합니다.
- 응급의학과 전문의가 실제로 0명인 지역은 의료진 부족 100점으로 처리합니다.
- HIRA 병원 자동매칭률이 80% 미만인 지역은 전문의 수를 신뢰하지 않고 결측 처리합니다.

## 분석 결과

### 상관관계와 VIF

네 구성점수의 상관계수와 VIF를 계산합니다. 현재 VIF는 약 1.05~2.84로 심각한 다중공선성은 확인되지 않았습니다.

### 원천값 선형회귀

가중합을 그대로 역추정하는 순환 회귀를 피하기 위해 점수 대신 다음 원천값을 사용합니다.

```text
X = 포화율, 직선거리_km, 인구대비병상비율, 병상대비전문의부족비율
y = regionRisk
```

현재 결과:

- 분석 지역: 79개
- R²: 0.833
- MAE: 2.70

`regionRisk` 자체가 원천값을 정규화해 만든 지표이므로 이 회귀는 인과분석이 아니라 위험도 산식의 민감도 분석으로 해석해야 합니다. 실제 정책 효과를 분석하려면 이송 거절, 재이송, 장기 체류 등 외부 결과변수가 필요합니다.

### K-Means 지역 유형

k=2~8의 실루엣 점수를 비교했으며 현재 최적값은 k=2입니다.

| 클러스터 | 지역 수 | 해석 | 평균 regionRisk |
|---|---:|---|---:|
| 0 | 55 | 접근성·의료진 취약형 | 47.94 |
| 1 | 69 | 도시형·인구 대비 병상 부담형 | 25.95 |

클러스터 번호는 위험등급이나 순위를 의미하지 않습니다. 군집별 평균 특성을 확인한 뒤 붙인 설명입니다.

## 주요 결과 파일

### 원천 및 중간 데이터

- `data/hospital_master.csv`: 전국 응급의료기관 마스터
- `data/bed_status.csv`: 현재 병상 상태
- `data/bed_status_history.csv`: 실행 시점별 병상 스냅샷 누적
- `data/population_source.csv`: 정제한 KOSIS 시군구 인구
- `data/doctor_source.csv`: 시군구별 응급의학과 전문의 수와 매칭 품질
- `data/hira_doctor_matches.csv`: NEMC-HIRA 병원별 매칭 상세

### 점수 및 최종 위험도

- `data/accessibility_score.csv`
- `data/population_bed_score.csv`
- `data/doctor_score.csv`
- `data/region_risk_final.csv`

### 분석 결과

- `data/heatmap_matrix.csv`: 현재 누적 이력의 요일×시간 포화율
- `data/correlation_matrix.csv`: 상관계수 행렬
- `data/vif_result.csv`: 다중공선성 진단
- `data/regression_result.csv`: 원천값 회귀계수
- `data/regression_metrics.json`: R², MAE, 절편
- `data/cluster_k_evaluation.csv`: k별 실루엣 점수
- `data/cluster_result.csv`: 지역별 클러스터
- `data/cluster_profile.csv`: 클러스터별 평균 특성

## 데이터 한계와 남은 작업

### 2024년 병상 이력

현재 NEMC API는 과거 기간 조회가 아니라 현재 시점의 실시간 상태를 제공합니다. 따라서 현재 `bed_status_history.csv`만으로는 2024년 요일·시간·계절 패턴을 분석할 수 없습니다.

다음 중 하나가 필요합니다.

1. 국립중앙의료원 또는 공공데이터포털을 통해 2024년 병상 이력 원자료 제공신청
2. 현 시점부터 파이프라인을 정기 실행해 자체 이력 축적

### 행정구역 개편

현재 NEMC 병원 주소에는 2026년 행정구역 명칭이 적용되어 있지만 KOSIS 인구는 2024년 경계입니다. 인천의 제물포구·서해구·검단구·영종구는 2024년 자료와 직접 대응하지 않아 결측 처리했습니다. 임의 비율로 과거 인구를 배분하지 않습니다.

### HIRA 병원 매칭

NEMC의 `hpid`와 HIRA의 암호화 요양기호는 서로 다른 코드입니다. 병원명과 주소를 이용해 자동매칭하며, 지역별 매칭률이 낮으면 해당 지역의 전문의 점수를 산출하지 않습니다. `hira_doctor_matches.csv`의 미매칭 병원을 수동 검토하면 최종 위험도 산출 지역을 늘릴 수 있습니다.

### 접근성 중심점

`data/region_centroids.csv`가 없으면 시군구 내 병원 좌표 평균을 중심점 대체값으로 사용합니다. 공식 행정구역 중심점 파일을 추가할 경우 필요한 컬럼은 다음과 같습니다.

```csv
시도,시군구,위도,경도
```
