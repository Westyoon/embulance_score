import { readBoundaryJson, readCsv, readJson } from "./csvServer";
import { CLUSTER_LABELS } from "./riskScale";
import { corrLevel, CORR_INSIGHTS } from "./correlationScale";

export const DASHBOARD_SOURCE_FILES = [
  "region_risk_final.csv",
  "cluster_result.csv",
  "cluster_profile.csv",
  "bed_status.csv",
  "accessibility_score.csv",
  "kakao_hospital_routes.csv",
  "hospital_master.csv",
  "correlation_matrix.csv",
  "regression_result.csv",
  "regression_metrics.json",
];

const COMPONENT_KEYS = [
  { field: "병상포화도점수", short: "병상포화도" },
  { field: "접근성점수", short: "접근성" },
  { field: "인구대비병상점수", short: "인구대비병상" },
  { field: "의료진부족점수", short: "의료진부족" },
];
const SUCCESS_ROUTE_STATUSES = new Set(["성공", "성공:출도착5m이내"]);
const BED_SOURCE_MAX_AGE_HOURS = positiveNumber("BED_SOURCE_MAX_AGE_HOURS", 12);

function positiveNumber(name, fallback) {
  const value = Number(process.env[name] || fallback);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${name} must be a positive number`);
  }
  return value;
}

function finiteNumber(value) {
  if (value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function bedSourceValidUntil(value) {
  const text = String(value ?? "").trim().replace(/\.0+$/, "");
  const match = text.match(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/);
  if (!match) return null;
  const [, year, month, day, hour, minute, second] = match.map(Number);
  const localAsUtc = Date.UTC(year, month - 1, day, hour, minute, second);
  const localCheck = new Date(localAsUtc);
  if (
    localCheck.getUTCFullYear() !== year
    || localCheck.getUTCMonth() !== month - 1
    || localCheck.getUTCDate() !== day
    || localCheck.getUTCHours() !== hour
    || localCheck.getUTCMinutes() !== minute
    || localCheck.getUTCSeconds() !== second
  ) return null;
  const sourceUtc = localAsUtc - 9 * 3_600_000;
  return new Date(sourceUtc + BED_SOURCE_MAX_AGE_HOURS * 3_600_000).toISOString();
}

function readOptionalCsv(filename) {
  try {
    return readCsv(filename);
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
}

function normalizeHospitalRoute(row) {
  if (!row) return null;
  const straightDistanceKm = finiteNumber(row["직선거리_km"]);
  const roadDistanceKm = finiteNumber(row["도로거리_km"]);
  const routeEtaMin = finiteNumber(row["예상시간_분"]);
  const routeStatus = row["경로상태"] || null;
  const hasRoadRoute = SUCCESS_ROUTE_STATUSES.has(routeStatus)
    && roadDistanceKm != null
    && routeEtaMin != null;

  return {
    straightDistanceKm,
    roadDistanceKm: hasRoadRoute ? roadDistanceKm : null,
    distanceKm: hasRoadRoute ? roadDistanceKm : straightDistanceKm,
    etaMin: hasRoadRoute ? routeEtaMin : null,
    distanceBasis: hasRoadRoute
      ? "카카오 도로거리"
      : (straightDistanceKm != null ? "직선거리 대체" : "미산출"),
    routeOriginMethod: row["중심점방법"] || null,
    routeStatus,
    routeUpdatedAt: row["수집시각"] || null,
  };
}

function normalizeAccessibilityRoute(row) {
  if (!row) return null;
  const straightDistanceKm = finiteNumber(row["직선거리_km"]);
  const roadDistanceKm = finiteNumber(row["도로거리_km"]);
  const routeEtaMin = finiteNumber(row["예상시간_분"]);
  const routeStatus = row["경로상태"] || null;
  const hasRoadRoute = SUCCESS_ROUTE_STATUSES.has(routeStatus)
    && roadDistanceKm != null
    && routeEtaMin != null;

  return {
    destinationOrgCode: row["최근접기관코드"] || null,
    destinationName: row["최근접병원"] || null,
    straightDistanceKm,
    roadDistanceKm: hasRoadRoute ? roadDistanceKm : null,
    distanceKm: hasRoadRoute ? roadDistanceKm : straightDistanceKm,
    etaMin: hasRoadRoute ? routeEtaMin : null,
    distanceBasis: hasRoadRoute
      ? "카카오 도로거리"
      : (straightDistanceKm != null ? "직선거리 대체" : (row["거리기준"] || "미산출")),
    originMethod: row["중심점방법"] || null,
    routeStatus,
    routeUpdatedAt: row["경로수집시각"] || null,
  };
}

function toRegion(row, clusterByKey, clusterMetaById, hospitalsByKey, accessibilityByKey) {
  const key = row["시군구코드"];
  const complete = row["산출상태"] === "완료";
  const hospitals = hospitalsByKey.get(key) ?? [];
  const bedDataHospitals = finiteNumber(row["병상데이터기관수"]) ?? 0;
  const contributingHospitals = hospitals.filter((hospital) => (
    finiteNumber(hospital.saturation) != null
  ));
  const validUntilTimes = contributingHospitals
    .map((hospital) => Date.parse(hospital.bedValidUntil || ""))
    .filter(Number.isFinite);
  const bedRiskFreshnessUnknown = (
    contributingHospitals.length !== bedDataHospitals
    || validUntilTimes.length !== bedDataHospitals
  );
  const bedRiskValidUntil = !bedRiskFreshnessUnknown && validUntilTimes.length > 0
    ? new Date(Math.min(...validUntilTimes)).toISOString()
    : null;
  const clusterRow = clusterByKey.get(key);
  const cluster = clusterRow != null ? clusterRow["클러스터"] : null;
  const meta = cluster != null ? clusterMetaById[cluster] : null;
  return {
    key,
    name: row["시군구명"],
    sido: key.split("|")[0],
    missing: !complete,
    bed: row["병상포화도점수"],
    access: row["접근성점수"],
    popBed: row["인구대비병상점수"],
    doc: row["의료진부족점수"],
    missingComponents: COMPONENT_KEYS
      .filter(({ field }) => finiteNumber(row[field]) == null)
      .map(({ short }) => short),
    bedDataHospitals,
    totalHospitals: hospitals.length,
    bedDataCoverage: hospitals.length > 0 ? bedDataHospitals / hospitals.length : null,
    bedDataQuality: bedDataHospitals === 0
      ? "결측"
      : (bedDataHospitals === hospitals.length ? "전체응답" : "부분응답"),
    bedRiskValidUntil,
    bedRiskFreshnessUnknown,
    risk: complete ? row["regionRisk"] : null,
    cluster,
    clusterLabel: meta?.label ?? null,
    clusterColor: meta?.color ?? null,
    hospitals,
    accessibilityRoute: accessibilityByKey.get(key) ?? null,
  };
}

// 행안부 2026-07-01 체계를 반영한 시군구 경계와 파이프라인의
// "시도|시군구" 키를 잇는다.
// 1) 최신 경계의 시도명 + 시군구명 직접 매칭
// 2) 안 되면 "OO시 + 구" 형태를 부모 도시(OO시) 단위 파이프라인 로우로 대체
//    (예: 수원시영통구 -> 경기도|수원시 값을 그대로 적용).
// 3) 그래도 없으면 원천데이터 자체가 없는 지역으로 보고 회색 처리.
function buildRegionIndexByGeoCode(regionsByKey, koreaGeo) {
  const index = {};
  for (const feature of koreaGeo.features) {
    const { code, name, sido } = feature.properties;
    let region = sido ? regionsByKey.get(`${sido}|${name}`) : null;
    if (!region && sido) {
      const m = name.match(/^(.+?시)(.+구)$/);
      if (m) region = regionsByKey.get(`${sido}|${m[1]}`);
    }
    if (region) {
      // 부모 시 집계는 여러 일반구 폴리곤을 공유한다. 원본 region에는 사이드
      // 테이블용 대표 코드를 두되, 지도 인덱스에는 클릭한 폴리곤 코드를 가진
      // 별도 객체를 넣어 다른 구가 강조되는 일을 막는다.
      if (!region.geoCodes) region.geoCodes = [];
      region.geoCodes.push(code);
      if (region.code == null) region.code = code;
      index[code] = { ...region, code, geoName: name };
    } else {
      index[code] = { key: null, code, name, sido: sido ?? null, missing: true, hospitals: [] };
    }
  }
  return index;
}

function buildClusterMeta(clusterProfileRows) {
  if (clusterProfileRows.length === 0) return {};
  const sorted = [...clusterProfileRows].sort((a, b) => b["regionRisk"] - a["regionRisk"]);
  const metaById = {};
  sorted.forEach((row, i) => {
    metaById[row["클러스터"]] = { ...(i === 0 ? CLUSTER_LABELS.vulnerable : CLUSTER_LABELS.moderate), count: row["지역수"] };
  });
  return metaById;
}

function hospitalGeoCode(hospital, koreaGeo) {
  const address = String(hospital["주소"] ?? "").replace(/\s+/g, "");
  const candidates = koreaGeo.features.filter((feature) => (
    feature.properties.sido === hospital["시도"]
      && address.includes(String(feature.properties.name).replace(/\s+/g, ""))
  ));
  candidates.sort((a, b) => String(b.properties.name).length - String(a.properties.name).length);
  return candidates[0]?.properties.code ?? null;
}

function buildHospitals(routeByCode, koreaGeo) {
  const hospitals = readCsv("hospital_master.csv");
  const bedStatus = readCsv("bed_status.csv");
  const bedByCode = new Map(bedStatus.map((r) => [r["기관코드"], r]));
  const byKey = new Map();
  const all = [];
  for (const h of hospitals) {
    const key = `${h["시도"]}|${h["시군구"]}`;
    const bed = bedByCode.get(h["기관코드"]);
    const route = routeByCode.get(h["기관코드"]);
    const availableBeds = finiteNumber(bed?.["가용병상"]);
    const totalBeds = finiteNumber(bed?.["전체병상"]);
    const saturation = finiteNumber(bed?.["포화율"]);
    const usableBed = (
      availableBeds != null
      && availableBeds >= 0
      && totalBeds != null
      && totalBeds > 0
      && saturation != null
    );
    const entry = {
      name: h["병원명"],
      grade: h["등급"],
      region: h["시군구"],
      sido: h["시도"],
      orgCode: h["기관코드"],
      address: h["주소"],
      phone: h["전화"],
      latitude: h["위도"],
      longitude: h["경도"],
      geoCode: hospitalGeoCode(h, koreaGeo),
      status: usableBed ? (bed?.["상태"] ?? "결측") : "결측",
      availableBeds: usableBed ? availableBeds : null,
      totalBeds: usableBed ? totalBeds : null,
      saturation: usableBed ? saturation : null,
      updatedAt: bed?.["수집시각"] ?? null,
      sourceUpdatedAt: bed?.["API기준시각"] ?? null,
      bedValidUntil: usableBed ? bedSourceValidUntil(bed?.["API기준시각"]) : null,
      straightDistanceKm: route?.straightDistanceKm ?? null,
      roadDistanceKm: route?.roadDistanceKm ?? null,
      distanceKm: route?.distanceKm ?? null,
      etaMin: route?.etaMin ?? null,
      distanceBasis: route?.distanceBasis ?? "미산출",
      routeOriginMethod: route?.routeOriginMethod ?? null,
      routeStatus: route?.routeStatus ?? null,
      routeUpdatedAt: route?.routeUpdatedAt ?? null,
    };
    all.push(entry);
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(entry);
  }
  return { byKey, all };
}

function buildCorrelation() {
  const rows = readCsv("correlation_matrix.csv");
  const riskRow = rows.find((r) => r["변수명"] === "regionRisk");
  return COMPONENT_KEYS.map(({ field }) => {
    const r = riskRow[field];
    const lvl = corrLevel(r);
    return {
      name: field,
      r,
      level: lvl.label,
      color: lvl.color,
      pct: Math.round(Math.abs(r) * 100),
      insight: CORR_INSIGHTS[field],
    };
  }).sort((a, b) => Math.abs(b.r) - Math.abs(a.r));
}

function buildRegression() {
  const coefRows = readCsv("regression_result.csv");
  const metrics = readJson("regression_metrics.json");
  if (coefRows.length === 0 || metrics.status !== "complete") {
    return { coef: [], r2: null, mae: null, rows: 0 };
  }
  const coefByName = new Map(coefRows.map((r) => [r["변수명"], r["회귀계수"]]));
  const coef = [
    { name: "포화율(원천)", value: coefByName.get("포화율_원천") },
    { name: "도로거리(km)", value: coefByName.get("도로거리_km") },
    { name: "인구대비병상비율", value: coefByName.get("인구대비병상비율"), note: "단위 스케일 상이" },
    { name: "의료진부족점수", value: coefByName.get("의료진부족점수") },
  ].filter(({ value }) => Number.isFinite(value));

  return { coef, r2: metrics.r2, mae: metrics.mae, rows: metrics.rows };
}

function oldestSnapshotTimestamp(bedStatus) {
  let oldest = null;
  let oldestMillis = Number.POSITIVE_INFINITY;
  for (const r of bedStatus) {
    const t = r["수집시각"];
    if (!t) continue;
    const millis = Date.parse(t);
    if (Number.isFinite(millis) && millis < oldestMillis) {
      oldest = t;
      oldestMillis = millis;
    }
  }
  return oldest;
}

export function loadDashboardData() {
  const koreaGeo = readBoundaryJson();
  const riskRows = readCsv("region_risk_final.csv");
  const clusterRows = readCsv("cluster_result.csv");
  const clusterProfileRows = readCsv("cluster_profile.csv");
  const bedStatus = readCsv("bed_status.csv");
  const accessibilityRows = readCsv("accessibility_score.csv");
  const hospitalRouteRows = readOptionalCsv("kakao_hospital_routes.csv");

  const clusterByKey = new Map(clusterRows.map((r) => [r["시군구코드"], r]));
  const clusterMetaById = buildClusterMeta(clusterProfileRows);
  const accessibilityByKey = new Map(accessibilityRows.map((row) => [
    row["시군구코드"],
    normalizeAccessibilityRoute(row),
  ]));
  const routeByCode = new Map(hospitalRouteRows.map((row) => [
    row["기관코드"],
    normalizeHospitalRoute(row),
  ]));
  const { byKey: hospitalsByKey, all: allHospitals } = buildHospitals(routeByCode, koreaGeo);

  const regions = riskRows.map((row) => (
    toRegion(row, clusterByKey, clusterMetaById, hospitalsByKey, accessibilityByKey)
  ));
  const regionsByKey = new Map(regions.map((r) => [r.key, r]));
  const regionIndex = buildRegionIndexByGeoCode(regionsByKey, koreaGeo);

  const complete = regions.filter((r) => !r.missing);
  const ranked = [...complete].sort((a, b) => b.risk - a.risk);
  const avg = complete.reduce((s, r) => s + r.risk, 0) / complete.length;
  const high = complete.filter((r) => r.risk > 50).length;

  const clusterProfile = COMPONENT_KEYS.map(({ field, short }) => {
    const row = { subject: short, full: 100 };
    for (const p of clusterProfileRows) row[`c${p["클러스터"]}`] = p[field];
    return row;
  });
  const clusterIds = clusterProfileRows.map((r) => r["클러스터"]).sort((a, b) => a - b);

  // 병원 상세 팝업(HD-06)에서 "이 병원이 속한 지역"을 시도+시군구명으로
  // 역참조할 때 쓰는 평면 객체. Server->Client 컴포넌트로 넘길 땐 Map을
  // 직렬화할 수 없어 plain object로 노출한다.
  const regionsByKeyPlain = Object.fromEntries([...regionsByKey.entries()].filter(([k]) => k != null));

  return {
    geo: koreaGeo,
    regionIndex,
    regionsByKey: regionsByKeyPlain,
    ranked,
    kpi: {
      avg,
      high,
      complete: complete.length,
      total: regions.length,
      missing: regions.length - complete.length,
      asOf: oldestSnapshotTimestamp(bedStatus),
    },
    clusterProfile,
    clusterIds,
    clusterMetaById,
    correlation: buildCorrelation(),
    regression: buildRegression(),
    allHospitals,
  };
}
