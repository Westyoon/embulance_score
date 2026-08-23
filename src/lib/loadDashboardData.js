import { readCsv, readJson } from "./csvServer";
import { SIDO_GEO_PREFIXES } from "./sido";
import { CLUSTER_LABELS } from "./riskScale";
import { corrLevel, CORR_INSIGHTS } from "./correlationScale";
import koreaGeo from "@/data/koreaGeo.json";

const COMPONENT_KEYS = [
  { field: "병상포화도점수", short: "병상포화도" },
  { field: "접근성점수", short: "접근성" },
  { field: "인구대비병상점수", short: "인구대비병상" },
  { field: "의료진부족점수", short: "의료진부족" },
];

function toRegion(row, clusterByKey, clusterMetaById, hospitalsByKey) {
  const key = row["시군구코드"];
  const complete = row["산출상태"] === "완료";
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
    risk: complete ? row["regionRisk"] : null,
    cluster,
    clusterLabel: meta?.label ?? null,
    clusterColor: meta?.color ?? null,
    hospitals: hospitalsByKey.get(key) ?? [],
  };
}

function buildPrefixToSido() {
  const map = {};
  for (const [sido, prefixes] of Object.entries(SIDO_GEO_PREFIXES)) {
    for (const p of prefixes) map[p] = sido;
  }
  return map;
}

// KOSTAT 2013 단순화 경계(지도용)와 파이프라인의 "시도|시군구" 키를 잇는다.
// 1) 시도 접두사 + 시군구명 직접 매칭
// 2) 안 되면 "OO시 + 구" 형태를 부모 도시(OO시) 단위 파이프라인 로우로 대체
//    (예: 수원시영통구 -> 경기도|수원시 값을 그대로 적용) — 최신 구 단위
//    행정구역 개편이 2013 경계와 어긋나는 경우의 절충.
// 3) 그래도 없으면 원천데이터 자체가 없는 지역으로 보고 회색 처리.
function buildRegionIndexByGeoCode(regionsByKey) {
  const prefixToSido = buildPrefixToSido();
  const index = {};
  for (const feature of koreaGeo.features) {
    const { code, name } = feature.properties;
    const sido = prefixToSido[code.slice(0, 2)];
    let region = sido ? regionsByKey.get(`${sido}|${name}`) : null;
    if (!region && sido) {
      const m = name.match(/^(.+?시)(.+구)$/);
      if (m) region = regionsByKey.get(`${sido}|${m[1]}`);
    }
    // 사이드 테이블에서 지역을 클릭했을 때 지도에서 하이라이트할 대표 GEO 코드.
    // 시 단위로만 집계된 지역(예: 수원시)은 여러 구 폴리곤에 매칭되므로 처음
    // 매칭된 코드 하나만 대표로 남긴다.
    if (region && region.code == null) region.code = code;
    index[code] = region ?? { key: null, name, sido: sido ?? null, missing: true, hospitals: [] };
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

function buildHospitals() {
  const hospitals = readCsv("hospital_master.csv");
  const bedStatus = readCsv("bed_status.csv");
  const bedByCode = new Map(bedStatus.map((r) => [r["기관코드"], r]));
  const byKey = new Map();
  const all = [];
  for (const h of hospitals) {
    const key = `${h["시도"]}|${h["시군구"]}`;
    const bed = bedByCode.get(h["기관코드"]);
    const entry = {
      name: h["병원명"],
      grade: h["등급"],
      region: h["시군구"],
      sido: h["시도"],
      orgCode: h["기관코드"],
      address: h["주소"],
      phone: h["전화"],
      status: bed?.["상태"] ?? "결측",
      availableBeds: bed?.["가용병상"] ?? null,
      totalBeds: bed?.["전체병상"] ?? null,
      saturation: bed?.["포화율"] ?? null,
      updatedAt: bed?.["수집시각"] ?? null,
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
    { name: "직선거리(km)", value: coefByName.get("직선거리_km") },
    { name: "인구대비병상비율", value: coefByName.get("인구대비병상비율"), note: "단위 스케일 상이" },
    { name: "전문의부족비율", value: coefByName.get("병상대비전문의부족비율") },
  ];

  return { coef, r2: metrics.r2, mae: metrics.mae, rows: metrics.rows };
}

function latestTimestamp(bedStatus) {
  let latest = null;
  for (const r of bedStatus) {
    const t = r["수집시각"];
    if (!t) continue;
    if (!latest || t > latest) latest = t;
  }
  return latest;
}

export function loadDashboardData() {
  const riskRows = readCsv("region_risk_final.csv");
  const clusterRows = readCsv("cluster_result.csv");
  const clusterProfileRows = readCsv("cluster_profile.csv");
  const bedStatus = readCsv("bed_status.csv");

  const clusterByKey = new Map(clusterRows.map((r) => [r["시군구코드"], r]));
  const clusterMetaById = buildClusterMeta(clusterProfileRows);
  const { byKey: hospitalsByKey, all: allHospitals } = buildHospitals();

  const regions = riskRows.map((row) => toRegion(row, clusterByKey, clusterMetaById, hospitalsByKey));
  const regionsByKey = new Map(regions.map((r) => [r.key, r]));
  const regionIndex = buildRegionIndexByGeoCode(regionsByKey);

  const complete = regions.filter((r) => !r.missing);
  const ranked = [...complete].sort((a, b) => b.risk - a.risk);
  const avg = complete.reduce((s, r) => s + r.risk, 0) / complete.length;
  const high = complete.filter((r) => r.risk >= 50).length;

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
      asOf: latestTimestamp(bedStatus),
    },
    clusterProfile,
    clusterIds,
    clusterMetaById,
    correlation: buildCorrelation(),
    regression: buildRegression(),
    allHospitals,
  };
}
