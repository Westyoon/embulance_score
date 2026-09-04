# Railway 동적 운영 배포

전체 시스템 구성은 [ARCHITECTURE.md](./ARCHITECTURE.md), 원천별 수집·결측·데이터 계보는 [DATA_PIPELINE.md](./DATA_PIPELINE.md), 로컬 재현은 [README.md](./README.md)를 먼저 참고합니다. 이 문서는 Railway 운영 설정과 장애 대응 절차에 집중합니다.

이 문서는 Next.js 화면과 Python 데이터 파이프라인을 Railway의 **단일 서비스**에서 운영하는 1차 배포 절차입니다. 웹은 현재 검증된 데이터를 계속 제공하고, 같은 프로세스 관리자가 병상 갱신과 전체 갱신을 별도 자식 프로세스로 실행합니다.

```text
Railway public domain
  └─ npm run start:dynamic
      ├─ Next.js
      │   ├─ GET  /api/dashboard
      │   ├─ GET  /api/health
      │   └─ POST /api/ops/refresh
      └─ Python pipeline scheduler
          ├─ beds: 8시간 간격 (API 증설 전 안전값)
          └─ full: 24시간 간격

Railway Volume: /app/runtime
  ├─ data/
  ├─ state/
  └─ koreaGeo.json
```

## 운영 전제

- Railway Volume을 서비스에 연결하고 마운트 경로를 정확히 `/app/runtime`으로 지정합니다.
- replica는 반드시 **1개**만 사용합니다. Railway Volume은 replicas와 함께 사용할 수 없고, 이 애플리케이션의 스케줄러와 파일 잠금도 단일 writer를 전제로 합니다.
- Railway의 Serverless 기능은 끕니다. 프로세스 내부 타이머가 8시간·24시간 주기를 관리하므로 서비스가 sleep 상태가 되면 정기 실행을 보장할 수 없습니다.
- 실제 API 키와 관리자 토큰은 Git이나 Docker 이미지에 넣지 않고 Railway Variables에 저장합니다.
- 전체 갱신에는 Python 3.12 환경과 Node.js/npm이 모두 필요합니다. 배포 이미지는 프론트 전용 Node 이미지가 아니라 `requirements.txt`까지 설치된 동적 런타임 이미지여야 합니다.

Railway는 볼륨을 빌드나 pre-deploy 단계가 아니라 서비스 시작 시 마운트합니다. 따라서 `/app/runtime` 초기화는 start command에서 수행하며, 볼륨이 비어 있으면 저장소에 포함된 검증 완료 데이터와 경계를 최초 데이터로 복사합니다. 자세한 플랫폼 동작은 [Railway Volumes](https://docs.railway.com/volumes), [Volumes 제한](https://docs.railway.com/volumes/reference), [Serverless](https://docs.railway.com/deployments/serverless) 문서를 참고합니다.

HIRA 수동 매칭·원천 제외와 병원 좌표·지역 보정 CSV는 이미지/저장소가 관리하는 입력입니다. 단, 새 이미지가 시작될 때 이 파일만 기존 Volume의 live `data/`에 복사하지 않습니다. 관리 입력 하나와 이전 HIRA 매칭 결과가 섞이면 서로 다른 데이터 세대가 되기 때문입니다. 변경된 관리 입력은 `full` 실행의 staging에만 복사하고 HIRA·병원·점수·결측 산출물을 모두 다시 만든 뒤, 전체 계약을 통과한 generation으로 함께 승격합니다. `beds`는 외부 NEMC API를 호출하기 전에 live 계약을 검사해 불일치 세대에서는 호출량을 쓰지 않고 중단합니다.

## 1. Railway 서비스 만들기

1. `main` push 시 GitHub Actions가 애플리케이션·파이프라인·컨테이너를 검증합니다.
2. `main` CI 검증을 모두 통과한 동일 이미지를 불변 커밋 태그와 `ghcr.io/westyoon/embulance-score:production`, `:latest`에 발행합니다. Railway 기존 설정과의 무중단 호환을 위해 `:backend`도 같은 digest로 함께 발행합니다.
3. Railway에서 새 프로젝트와 서비스를 만들고 이 공개 GHCR 이미지를 Source로 연결합니다.
4. 서비스에 Volume을 추가하고 Mount Path를 `/app/runtime`으로 지정합니다.
5. Settings의 Scale/Regions에서 replica가 1개인지 확인합니다.
6. Settings의 Serverless를 비활성화합니다.
7. 새 서비스는 GHCR의 `production` 태그, 기존 서비스는 호환용 `backend` 태그에 대해 Image Auto Updates를 `Anytime`으로 설정합니다. 두 태그는 `main` CI에서 같은 digest로 발행됩니다.

이미지에는 다음 시작 명령이 이미 들어 있으므로 Railway Start Command는 비워 두는 것이 기본입니다. 직접 재정의해야 할 때만 다음 명령을 사용합니다.

```text
npm run start:dynamic
```

Railway가 주입하는 `PORT`를 애플리케이션이 그대로 사용하므로 포트 번호를 직접 고정하지 않습니다. GitHub Actions가 만든 검증 완료 이미지를 그대로 배포하면 Railway 빌더와 CI가 서로 다른 산출물을 만드는 문제도 피할 수 있습니다. 이미지 자동 갱신은 연결한 `production` 또는 호환용 `backend` 태그의 digest가 바뀌면 새 배포를 만들지만 감지가 수 시간 지연될 수 있습니다. 이번 릴리스처럼 즉시 반영해야 할 때는 CI 발행 성공 뒤 Railway redeploy를 명시적으로 실행합니다. 재배포가 실행 중인 배치를 중단하면 검증 전 운영본은 보존되지만 그 배치가 이미 사용한 API 호출은 되돌릴 수 없습니다. [Railway Start Command](https://docs.railway.com/deployments/start-command), [Image Auto Updates](https://docs.railway.com/deployments/image-auto-updates)

## 2. 환경변수 설정

Railway 서비스의 Variables 탭에서 다음 값을 설정합니다. 아래 블록은 키 이름과 비밀값이 아닌 운영 기본값만 보여주는 예시입니다.

```env
DATA_GO_KR_API_KEY=<Railway에서 설정>
HIRA_API_KEY=<Railway에서 설정>
KAKAO_REST_API_KEY=<Railway에서 설정>
PIPELINE_ADMIN_TOKEN=<Railway에서 생성한 임의의 긴 값>

ENABLE_PIPELINE_SCHEDULER=true
FAST_REFRESH_INTERVAL_MINUTES=480
FULL_REFRESH_INTERVAL_HOURS=24
PIPELINE_FAILURE_RETRY_MINUTES=60
BEDS_FAILURE_RETRY_MINUTES=480
FULL_FAILURE_RETRY_MINUTES=60
FULL_REFRESH_REUSE_BEDS=true
FULL_REFRESH_BED_MAX_AGE_HOURS=12
BED_SOURCE_MAX_AGE_HOURS=12
DASHBOARD_DATA_STALE_AFTER_MINUTES=600
BED_API_MAX_ATTEMPTS=3
RUN_FAST_REFRESH_ON_START=false
CLEAR_STALE_PIPELINE_LOCK_ON_START=true
BED_HISTORY_RETENTION_DAYS=30
RAILWAY_RUN_UID=0
RAILWAY_DEPLOYMENT_DRAINING_SECONDS=30
```

비밀값 네 개는 각각 실제 값을 입력한 뒤 Railway에서 Seal 처리합니다.

- `DATA_GO_KR_API_KEY`: NEMC 공공데이터포털 디코딩 키
- `HIRA_API_KEY`: HIRA 공공데이터포털 디코딩 키
- `KAKAO_REST_API_KEY`: 카카오모빌리티 REST API 키
- `PIPELINE_ADMIN_TOKEN`: 수동 갱신 API의 Bearer 토큰

관리자 토큰은 로컬에서 다음과 같이 생성할 수 있습니다. 생성된 값은 Railway에만 저장하고 명령 기록, 문서, 이슈, 채팅에 붙여 넣지 않습니다.

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

다음 변수는 직접 만들 필요가 없습니다.

- `PORT`: Railway가 자동 주입
- `RAILWAY_VOLUME_MOUNT_PATH`: `/app/runtime` 볼륨을 붙이면 Railway가 자동 주입
- `PIPELINE_DATA_DIR`, `PIPELINE_LIVE_DATA_DIR`, `PIPELINE_STATE_DIR`, `BOUNDARY_FILE`: `start:dynamic`이 볼륨 하위 경로로 설정
- `PIPELINE_MUTATIONS_ENABLED`: 스케줄러 활성화 여부에 맞춰 웹 자식 프로세스에 설정

`RAILWAY_VOLUME_MOUNT_PATH`는 Railway 제공 변수이므로 같은 이름을 수동으로 덮어쓰지 않고, Railway에서는 `PIPELINE_RUNTIME_DIR`도 만들지 않습니다. 스케줄러가 켜진 Railway 런타임에서 실제 Volume mount 변수가 없으면 애플리케이션은 임시 디스크로 우회하지 않고 시작을 거부합니다. 로컬에서만 `.env`의 `PIPELINE_RUNTIME_DIR=./runtime`을 사용합니다. Railway Variables는 빌드와 런타임에 전달되며, 비밀값은 sealed variable로 관리할 수 있습니다. [Railway Variables](https://docs.railway.com/variables), [제공 변수 목록](https://docs.railway.com/variables/reference)

`NEXT_PUBLIC_DASHBOARD_POLL_SECONDS`는 브라우저 번들 빌드 시 고정되는 값입니다. Railway 런타임 Variables가 아니라 GitHub Actions 이미지 빌드 환경에서만 바꾸며, 현재 이미지는 기본 60초를 사용합니다.

현재 Dockerfile은 기본적으로 non-root 사용자로 실행되지만 Railway Volume은 root 소유로 마운트됩니다. Railway가 런타임 사용자를 root로 지정해 `/app/runtime`에 쓸 수 있도록 `RAILWAY_RUN_UID=0`을 설정합니다. 이 값은 비밀이 아니며 Railway 배포에만 필요합니다.

## 3. Health check와 공개 도메인

Railway 서비스 Settings에서 다음 값을 지정합니다.

```text
Healthcheck Path: /api/health
Healthcheck Timeout: 300 seconds
```

그다음 Networking에서 Railway 도메인을 생성합니다. 배포가 끝나면 다음 요청이 HTTP 200인지 확인합니다.

```powershell
$env:APP_URL = "https://your-service.up.railway.app"
curl.exe -fsS "$env:APP_URL/api/health"
```

정상 응답에는 현재 데이터 버전, 데이터 기준시각, 현재·최근 계산·만료 지역 수와 파이프라인 상태가 포함됩니다. 아래 값은 형식 예시이며 실제 운영 수치가 아닙니다.

```json
{
  "status": "ok",
  "dataVersion": "...",
  "dataAsOf": "...",
  "regions": "<integer>",
  "completeRegions": "<integer>",
  "scoredRegions": "<integer>",
  "expiredScoreRegions": "<integer>",
  "pipeline": {
    "state": "idle"
  }
}
```

`/api/health`는 현재 운영 데이터가 읽히지 않을 때만 503을 반환합니다. 최근 배치가 실패했더라도 마지막 검증 데이터가 정상이라면 화면을 계속 제공하고, `pipeline.state`를 `failed`로 표시합니다. 가장 오래된 병상 수집시각이 `DASHBOARD_DATA_STALE_AFTER_MINUTES`(운영 600분)를 넘으면 HTTP 200은 유지하되 `status=degraded`, `dataStale=true`로 갱신 지연을 경고합니다. 실제 수치 차단은 더 정확한 병원별 `API기준시각 + BED_SOURCE_MAX_AGE_HOURS`를 사용하며, 만료 병원이 점수에 포함된 지역만 위험도와 병상 의존 점수를 숨깁니다.

Railway health check는 새 deployment가 트래픽을 받을 준비가 됐는지 확인하는 용도이며 배포 후 지속 모니터링은 아닙니다. 지속 감시가 필요하면 외부 uptime monitor에서 같은 경로를 호출합니다. 볼륨이 연결된 서비스는 이전 deployment와 새 deployment가 동시에 같은 볼륨을 마운트할 수 없어 재배포 때 짧은 중단이 생길 수 있습니다. [Railway Healthchecks](https://docs.railway.com/deployments/healthchecks)

## 4. 자동 갱신 주기

기본 운영 주기는 다음과 같습니다.

| 모드 | 간격 | 실행 내용 |
|---|---:|---|
| `beds` | 8시간 | NEMC 병상, 구성점수, 위험도, 분석, 데이터 계약 검증 |
| `full` | 24시간 | NEMC 기관·인구·HIRA·경계·카카오 경로, 전체 분석·검증. 병상은 최근 검증 스냅샷 재사용 |

NEMC 실시간 병상 API는 공식 계약상 `STAGE1`(시도)과 `STAGE2`(시군구)가 모두 필수라 한 번의 갱신에 현재 분석 지역 수만큼 요청이 필요합니다. 현재 지역 수를 `R`이라 하면 8시간 주기는 하루 약 `3R`, 시간당 갱신은 하루 약 `24R`의 성공 호출을 사용합니다. `/api/health`의 `regions`로 `R`을 확인하고 재시도 여유까지 더해 공공데이터포털 일일 할당량을 증설한 뒤에만 `FAST_REFRESH_INTERVAL_MINUTES=60`으로 바꿉니다. 429·한도초과(`22`)·키 일시중지(`21`) 응답은 `Retry-After`를 최대 60초까지만 반영해 전체 실행에서 단 한 번 복구 재시도합니다. 다시 실패하면 공유 회로 차단기가 아직 시작하지 않은 지역 호출을 취소하고 운영 CSV는 그대로 보존합니다.

전체 갱신은 `FULL_REFRESH_REUSE_BEDS=true`일 때 병상 API를 중복 호출하지 않습니다. 기존 병상 스냅샷이 새 NEMC 기관코드 집합과 정확히 일치하고, 유효 기관이 373개 이상이며, 가장 오래된 수집시각이 `FULL_REFRESH_BED_MAX_AGE_HOURS` 이내일 때만 병원 메타데이터를 새 마스터 기준으로 다시 결합합니다. 병원이 보고한 `API기준시각`도 한국 시간으로 해석해 `BED_SOURCE_MAX_AGE_HOURS`(운영값 12시간)를 넘긴 행은 병상값을 결측 처리한 뒤 유효 기관 기준을 다시 검사합니다. HIRA·경계·카카오 수집이 끝난 뒤 점수 계산 직전에도 다시 검사하고, 그 사이 새로 만료된 행을 제외한 데이터로 점수와 분석을 재계산합니다. 검증 실패 시 API fallback 없이 전체 갱신을 중단하고 기존 운영본을 보존합니다. 병상 이력에는 재사용본을 새 관측처럼 추가하지 않으며, `/api/health`의 `lastFullReusedBedSnapshot`, `lastFullBedSnapshotAt`, `lastFullBedStaleSourceHospitals`, `lastFullBedSanitizedSourceHospitals`로 재사용·원천 제외 여부를 확인할 수 있습니다.

지역 병상 구성점수는 해당 시점에 유효하게 보고한 NEMC 기관을 분석 모집단으로 사용합니다. 일부 미보고 기관을 0병상으로 간주하지 않으며, 화면의 지역 상세에 `병상 API 반영 기관 / 전체 NEMC 기관`을 함께 표시해 부분 응답 범위를 숨기지 않습니다.

고정 시각 cron이 아니라 상태 파일의 `schedulerStartedAt`과 모드별 최근 성공 시각을 기준으로 다음 실행을 계산합니다. 이 값은 영속 Volume에 남으므로 재배포나 재시작이 주기를 0부터 되돌리지 않습니다. `BEDS_FAILURE_RETRY_MINUTES`와 `FULL_FAILURE_RETRY_MINUTES`가 모드별 실패 재시도를 제어합니다. 값이 없을 때의 안전 기본값은 각각 480분과 60분이며, 기존 호환용 `PIPELINE_FAILURE_RETRY_MINUTES`는 상태 표시와 별도 운영 설정에만 남겨 둡니다.

동시에 실행되는 파이프라인은 최대 하나입니다. 실행 중 다른 정기 작업 시각이 오면 작업 종료 후 다음 scheduler tick에서 overdue 여부를 다시 계산하고 `full`을 `beds`보다 먼저 실행합니다. 운영 데이터는 기존 검증 버전을 계속 제공합니다. 각 작업은 별도 staging에서 실행되고 모든 검증을 통과한 뒤에만 `/app/runtime/data`를 승격합니다.

`beds`는 staging을 만든 직후 현재 live generation에 `validate_data_contract.py`를 먼저 실행합니다. HIRA 관리 입력과 매칭 결과 등 기존 세대 자체가 불일치하면 NEMC 병상 API 호출 전에 종료합니다. `full`은 live를 staging에 복사한 다음 이미지가 관리하는 네 입력을 staging에만 반영하고, 모든 종속 산출물과 경계를 다시 만든 뒤 함께 검증·승격합니다. 배포 재시작 자체는 기존 live generation을 바꾸지 않습니다.

`RUN_FAST_REFRESH_ON_START=false`를 권장합니다. 이를 `true`로 바꾸면 매 배포 시작 약 30초 뒤 병상 API를 호출하므로 잦은 재배포가 공공 API 트래픽을 불필요하게 사용할 수 있습니다. 단, 비어 있는 Volume을 이미지의 검증 데이터로 처음 채운 경우에는 이 값이 `false`여도 최초 1회 병상 갱신을 자동 실행합니다. 그 30초 동안 수동 또는 주기 갱신이 먼저 시작되면 startup 갱신은 건너뛰어 중복 호출하지 않습니다.

## 5. 수동 갱신 API

수동 갱신은 다음 endpoint에 인증된 POST 요청을 보내 예약합니다.

```text
POST /api/ops/refresh
Authorization: Bearer <PIPELINE_ADMIN_TOKEN>
Content-Type: application/json
```

병상 중심의 빠른 갱신:

```powershell
$env:APP_URL = "https://your-service.up.railway.app"
$secureToken = Read-Host "PIPELINE_ADMIN_TOKEN" -AsSecureString
$tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
$env:PIPELINE_ADMIN_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
curl.exe -fsS -X POST "$env:APP_URL/api/ops/refresh" `
  -H "Authorization: Bearer $env:PIPELINE_ADMIN_TOKEN" `
  -H "Content-Type: application/json" `
  --data-raw '{"mode":"beds"}'
```

전체 원천 갱신:

```powershell
curl.exe -fsS -X POST "$env:APP_URL/api/ops/refresh" `
  -H "Authorization: Bearer $env:PIPELINE_ADMIN_TOKEN" `
  -H "Content-Type: application/json" `
  --data-raw '{"mode":"full"}'
```

Bash에서는 다음과 같습니다.

```bash
curl -fsS -X POST "$APP_URL/api/ops/refresh" \
  -H "Authorization: Bearer $PIPELINE_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"mode":"beds"}'
```

성공 시 HTTP 202와 다음 형식이 반환됩니다.

```json
{"accepted":true,"mode":"beds"}
```

202는 **갱신 완료가 아니라 요청 파일이 접수됐음**을 뜻합니다. 스케줄러는 최대 약 5초 안에 요청을 확인하며, 다른 작업이 실행 중이면 끝난 뒤 처리합니다. 대기 중인 요청은 완전한 메시지 큐가 아니라 단일 파일이므로 완료를 확인하기 전에 여러 수동 요청을 연속으로 보내지 않습니다.

오류 응답은 다음과 같습니다.

- 400: JSON이 아니거나 `mode`가 `beds`/`full`이 아님
- 401: Bearer 토큰 누락 또는 불일치
- 503: `ENABLE_PIPELINE_SCHEDULER`가 활성화되지 않아 변경 작업이 차단됨

토큰을 query string에 넣지 말고, `NEXT_PUBLIC_` 접두사를 붙이지 않으며, 브라우저 코드에서 이 endpoint를 직접 호출하지 않습니다.
호출 확인 뒤에는 `Remove-Item Env:PIPELINE_ADMIN_TOKEN`으로 현재 셸에서도 토큰을 제거합니다.

## 6. 갱신 상태와 로그 확인

현재 상태는 `/api/health`의 `pipeline` 객체와 Railway deployment 로그에서 확인합니다.

```powershell
curl.exe -fsS "$env:APP_URL/api/health"
```

주요 상태값:

- `running`: `mode`, `trigger`, `startedAt` 확인
- `idle`: 최근 작업이 정상 종료됨
- `failed`: `lastFailureAt`과 Railway 로그 확인
- `lastSuccessAt`, `lastSuccessfulMode`: 마지막 정상 승격 시각과 모드

대시보드 데이터 자체는 `GET /api/dashboard`에서 확인할 수 있습니다. 이 endpoint는 데이터 파일과 경계의 버전을 ETag로 제공하며, 브라우저 화면은 기본 60초마다 새 버전을 확인합니다.

## 7. 볼륨과 복구

`/app/runtime` 전체를 한 볼륨에 둡니다. `/app/runtime/data`만 별도로 마운트하면 상태 파일과 경계가 재배포 때 사라지므로 잘못된 구성입니다.

- `data/`: 마지막 검증 완료 CSV·JSON과 병상 이력
- `koreaGeo.json`: 현재 대시보드 경계
- `state/pipeline_status.json`: 작업 상태
- `state/.pipeline.lock`: 실행 중 중복 작업 방지
- `state/.pipeline-*-staging-*`, `state/.bed-refresh-*`: 실행 중 staging과 복구용 임시 파일
- `state/interrupted-*`, `state/recovered-*`, `state/superseded-*`: 비정상 종료 복구 시 보존한 진단용 사본

갱신 실패 시 staging만 제거하고 기존 운영 데이터는 유지합니다. 승격 중 일반 오류가 발생하면 백업을 복구합니다. `CLEAR_STALE_PIPELINE_LOCK_ON_START`의 기본값은 `false`입니다. Railway처럼 볼륨을 한 인스턴스만 마운트하고 이전 컨테이너 종료 후 새 컨테이너가 시작되는 구성에서만 `true`로 설정할 수 있습니다. 실행 중인 컨테이너의 `.pipeline.lock`은 임의로 지우면 안 됩니다.

시작 시 중단된 승격 backup을 발견하면 이미지 seed보다 먼저 마지막 정상본으로 되돌리고, 당시 live와 backup은 위 진단용 이름으로 보존합니다. `/api/health`의 `pipeline.recoveredAt`과 `recoveredFrom`을 확인한 다음 필요 시 별도 백업하고 오래된 진단용 사본을 정리합니다.

### 관리 입력·NEMC 모집단 세대 교착 복구

다음 두 오류가 이어지면 일반 `beds` 재시도로 해결하지 않습니다.

- HIRA override/exclusion의 수동 집합과 `hira_doctor_matches.csv`가 서로 다른 generation이라 live 사전 계약이 실패
- 새 NEMC 기관 모집단과 기존 병상 스냅샷의 기관코드 집합이 달라 `FULL_REFRESH_REUSE_BEDS=true` 전체 갱신도 실패

이 상태에서 `beds`는 의도적으로 NEMC 호출 전에 중단되고, `full`은 기존 병상을 자동으로 버리고 API에 fallback하지 않습니다. 자동 fallback은 호출량을 예측할 수 없고 장애 원인을 숨기므로 금지합니다. 운영자가 다음 순서로 새 generation을 명시적으로 한 번 만들어야 합니다.

1. `/api/health`의 `dataVersion`, `pipeline.lastFailureAt`, `pipeline.error`와 Railway 로그를 기록하고 Volume backup을 확인합니다.
2. Railway Variables에서 `FULL_REFRESH_REUSE_BEDS=false`로 바꾸고 새 deployment가 시작될 때까지 기다립니다. `RUN_FAST_REFRESH_ON_START=false`는 유지합니다.
3. [수동 갱신 API](#5-수동-갱신-api)에 `{"mode":"full"}`을 한 번만 전송합니다. 이 실행은 새 NEMC 기관·병상, 인구, HIRA, 경계, 카카오와 전체 점수·결측·분석을 staging에서 다시 만듭니다.
4. `/api/health`의 `pipeline.state`가 `running`에서 `idle`로 돌아오고, `lastSuccessfulMode`가 `full`, `lastSuccessAt`과 `dataVersion`이 갱신됐는지 확인합니다. `regions`, `completeRegions`, `scoredRegions`, `expiredScoreRegions`도 함께 검토합니다.
5. 성공을 확인한 직후 Railway Variables의 `FULL_REFRESH_REUSE_BEDS=true`를 복구하고 재배포 후 health를 다시 확인합니다.

전체 갱신이 실패하면 staging만 폐기되고 이전 live generation은 유지됩니다. 이때 `FULL_REFRESH_REUSE_BEDS=false` 상태에서 요청을 반복하지 말고, 실패한 수집 단계·API 할당량·데이터 계약을 먼저 수정합니다. 성공 전에는 `beds`로 우회하거나 live CSV를 수동으로 섞지 않습니다.

병상 이력은 기본 30일만 유지합니다. 시간대별 히트맵에는 충분한 기간을 남기면서 staging 복사와 pandas 재계산 비용을 제한하기 위한 초기 운영값입니다. 진단용 `interrupted-*`, `recovered-*`, `superseded-*` 사본은 자동 삭제하지 않으므로 주 1회 용량을 확인하고, 원인 확인과 별도 백업 뒤에만 명시적으로 정리합니다. 현재 8시간 주기와 향후 증설 후 60분 주기 모두를 고려해 볼륨 사용량을 감시하고 정기 백업을 설정합니다. 장기 원시 이력이 필요해지면 CSV 한 파일을 계속 키우지 말고 날짜별 파티션이나 별도 저장소로 분리합니다. Railway CLI의 `railway volume browse /` 또는 Railway의 Volume backup 기능으로 내용을 점검할 수 있습니다.

## 배포 체크리스트

- [ ] `main` 브랜치 CI가 불변 커밋 태그와 `:production`, `:latest`, Railway 호환용 `:backend`를 동일 digest로 발행
- [ ] Railway Source가 위 공개 GHCR 이미지에 연결되고 Image Auto Updates 활성화
- [ ] Volume mount가 정확히 `/app/runtime`
- [ ] replica 1개, Serverless 비활성화
- [ ] Python 3.12와 Node.js/npm이 모두 포함된 이미지
- [ ] `RAILWAY_RUN_UID=0`으로 `/app/runtime` 쓰기 권한 확보
- [ ] 세 원천 API 키와 `PIPELINE_ADMIN_TOKEN`을 Railway Variables에 저장·Seal
- [ ] `ENABLE_PIPELINE_SCHEDULER=true`
- [ ] 단일 Volume 서비스에서만 `CLEAR_STALE_PIPELINE_LOCK_ON_START=true`
- [ ] `RAILWAY_DEPLOYMENT_DRAINING_SECONDS=30`
- [ ] `FAST_REFRESH_INTERVAL_MINUTES=480` (현재 `regions` 기준 `24R`+재시도 할당량 승인 후 `60`)
- [ ] `FULL_REFRESH_INTERVAL_HOURS=24`
- [ ] `BEDS_FAILURE_RETRY_MINUTES=480`
- [ ] `FULL_FAILURE_RETRY_MINUTES=60`
- [ ] `FULL_REFRESH_REUSE_BEDS=true`
- [ ] `FULL_REFRESH_BED_MAX_AGE_HOURS=12`
- [ ] `BED_SOURCE_MAX_AGE_HOURS=12`
- [ ] `DASHBOARD_DATA_STALE_AFTER_MINUTES=600`
- [ ] Healthcheck Path `/api/health`
- [ ] Railway 공개 도메인 생성
- [ ] `/api/health` HTTP 200 확인
- [ ] `/api/health`의 `dataVersion`, `dataAsOf`, `completeRegions`, `scoredRegions`, `expiredScoreRegions`, `pipeline` 확인
- [ ] 기존 Volume 재배포가 live 관리 입력을 직접 덮어쓰지 않는지 확인
- [ ] 빈 Volume 최초 배포는 자동 `beds` 갱신 완료와 `lastSuccessAt` 갱신을 먼저 확인(실패했거나 기존 Volume일 때만 수동 갱신)
- [ ] 첫 `full` 실행 전 공공데이터·HIRA·카카오 API 할당량 확인
