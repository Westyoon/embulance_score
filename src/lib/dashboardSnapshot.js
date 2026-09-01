import "server-only";

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { BOUNDARY_FILE, DATA_DIR } from "./csvServer";
import { DASHBOARD_SOURCE_FILES, loadDashboardData } from "./loadDashboardData";

const CACHE_KEY = Symbol.for("embulance-score.dashboard-snapshot");
const DASHBOARD_SCHEMA_VERSION = "dashboard-api-v3";
const PIPELINE_STATE_DIR = process.env.PIPELINE_STATE_DIR
  ? path.resolve(process.env.PIPELINE_STATE_DIR)
  : path.join(process.cwd(), "runtime", "state");
const PIPELINE_STATUS_FILE = path.join(PIPELINE_STATE_DIR, "pipeline_status.json");
const BED_SOURCE_MAX_AGE_HOURS = positiveNumber("BED_SOURCE_MAX_AGE_HOURS", 12);
const DASHBOARD_DATA_STALE_AFTER_MINUTES = positiveNumber(
  "DASHBOARD_DATA_STALE_AFTER_MINUTES",
  600,
);

function positiveNumber(name, fallback) {
  const value = Number(process.env[name] || fallback);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${name} must be a positive number`);
  }
  return value;
}

function sourceState(filePath) {
  try {
    const stat = fs.statSync(filePath);
    return `${filePath}:${stat.size}:${stat.mtimeMs}`;
  } catch (error) {
    if (error?.code === "ENOENT") return `${filePath}:missing`;
    throw error;
  }
}

function dashboardBuildId() {
  const configured = String(
    process.env.DASHBOARD_BUILD_ID
      || process.env.RAILWAY_GIT_COMMIT_SHA
      || process.env.SOURCE_VERSION
      || "",
  ).trim();
  if (configured) return configured;
  try {
    const nextBuildId = fs.readFileSync(
      path.join(process.cwd(), ".next", "BUILD_ID"),
      "utf-8",
    ).trim();
    return nextBuildId || "development";
  } catch (error) {
    if (error?.code === "ENOENT") return "development";
    throw error;
  }
}

export function dashboardVersion() {
  const sourcePaths = DASHBOARD_SOURCE_FILES.map((filename) => (
    path.join(DATA_DIR, filename)
  ));
  sourcePaths.push(BOUNDARY_FILE);
  const state = [
    `schema:${DASHBOARD_SCHEMA_VERSION}`,
    `build:${dashboardBuildId()}`,
    `bedSourceMaxAgeHours:${BED_SOURCE_MAX_AGE_HOURS}`,
    `dataStaleAfterMinutes:${DASHBOARD_DATA_STALE_AFTER_MINUTES}`,
    ...sourcePaths.map(sourceState),
  ].join("\n");
  return crypto.createHash("sha256").update(state).digest("hex").slice(0, 20);
}

function getRawDashboardSnapshot() {
  const cached = globalThis[CACHE_KEY];
  if (cached && readPipelineStatus().state === "running") return cached;
  try {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const versionBefore = dashboardVersion();
      if (cached?.version === versionBefore) return cached;
      const data = loadDashboardData();
      const versionAfter = dashboardVersion();
      if (versionBefore === versionAfter) {
        const snapshot = {
          version: versionAfter,
          loadedAt: new Date().toISOString(),
          degraded: false,
          data,
        };
        globalThis[CACHE_KEY] = snapshot;
        return snapshot;
      }
    }
    throw new Error("Dashboard source files changed while loading");
  } catch (error) {
    if (cached) {
      console.error(`Using last-known-good dashboard snapshot: ${error?.name ?? "Error"}`);
      return { ...cached, degraded: true };
    }
    throw error;
  }
}

function dataAgeMinutes(dataAsOf) {
  const timestamp = Date.parse(dataAsOf || "");
  return Number.isFinite(timestamp)
    ? Math.max(0, Math.round((Date.now() - timestamp) / 60_000))
    : null;
}

function hasBedValue(hospital) {
  return [hospital.availableBeds, hospital.totalBeds, hospital.saturation]
    .some((value) => Number.isFinite(value));
}

function hasUsableBedSaturation(hospital) {
  return Number.isFinite(hospital.saturation);
}

function hospitalBedExpired(hospital, nowMillis) {
  if (!hasBedValue(hospital)) return false;
  const validUntil = Date.parse(hospital.bedValidUntil || "");
  return !Number.isFinite(validUntil) || validUntil <= nowMillis;
}

function maskHospital(hospital) {
  return {
    ...hospital,
    status: "결측",
    availableBeds: null,
    totalBeds: null,
    saturation: null,
  };
}

function withBedCoverage(region, hospitals) {
  const totalHospitals = hospitals.length;
  const bedDataHospitals = hospitals.filter(hasUsableBedSaturation).length;
  return {
    ...region,
    bedDataHospitals,
    totalHospitals,
    bedDataCoverage: totalHospitals > 0
      ? bedDataHospitals / totalHospitals
      : null,
    bedDataQuality: bedDataHospitals === 0
      ? "결측"
      : (bedDataHospitals === totalHospitals ? "전체응답" : "부분응답"),
    hospitals,
  };
}

function maskRegion(region, hospitals) {
  return withBedCoverage({
    ...region,
    missing: true,
    bed: null,
    popBed: null,
    doc: null,
    missingComponents: [
      ...new Set([...(region.missingComponents || []), "병상데이터만료"]),
    ],
    missingReason: "병상데이터만료",
    risk: null,
    cluster: null,
    clusterLabel: null,
    clusterColor: null,
  }, hospitals);
}

function maskRegionAnalytics(region) {
  return {
    ...region,
    cluster: null,
    clusterLabel: null,
    clusterColor: null,
  };
}

function maskAnalyticsPayload(data) {
  return {
    ...data,
    regionIndex: Object.fromEntries(
      Object.entries(data.regionIndex).map(([key, region]) => (
        [key, maskRegionAnalytics(region)]
      )),
    ),
    regionsByKey: Object.fromEntries(
      Object.entries(data.regionsByKey).map(([key, region]) => (
        [key, maskRegionAnalytics(region)]
      )),
    ),
    ranked: data.ranked.map(maskRegionAnalytics),
    clusterProfile: [],
    clusterIds: [],
    clusterMetaById: {},
    correlation: [],
    regression: { coef: [], r2: null, mae: null, rows: 0 },
  };
}

function analysisRegion(region, expiredRegionKeys, asOfMillis) {
  const validUntilMillis = Date.parse(region.bedRiskValidUntil || "");
  return {
    key: region.key,
    name: region.name,
    sido: region.sido,
    bed: region.bed,
    access: region.access,
    popBed: region.popBed,
    doc: region.doc,
    risk: region.risk,
    cluster: region.cluster,
    clusterLabel: region.clusterLabel,
    clusterColor: region.clusterColor,
    bedRiskValidUntil: region.bedRiskValidUntil,
    sourcePolicyValidAtCalculation: (
      Number.isFinite(validUntilMillis) && validUntilMillis > asOfMillis
    ),
    scoreExpired: expiredRegionKeys.has(region.key),
  };
}

function buildAnalysisSnapshot(data, expiredRegionKeys) {
  const asOfMillis = Date.parse(data.kpi.asOf || "");
  if (!Number.isFinite(asOfMillis)) return null;
  const ranked = data.ranked.map((region) => (
    analysisRegion(region, expiredRegionKeys, asOfMillis)
  ));
  const missingRegions = Object.values(data.regionsByKey)
    .filter((region) => region.missing)
    .map((region) => ({
      key: region.key,
      name: region.name,
      sido: region.sido,
      missingComponents: [...(region.missingComponents || [])],
    }))
    .sort((left, right) => String(left.key).localeCompare(String(right.key), "ko"));
  const currentComplete = ranked.filter((region) => !region.scoreExpired).length;
  const sourcePolicyValid = ranked.filter((region) => (
    region.sourcePolicyValidAtCalculation
  )).length;

  return {
    ranked,
    kpi: { ...data.kpi },
    clusterProfile: data.clusterProfile,
    clusterIds: data.clusterIds,
    clusterMetaById: data.clusterMetaById,
    correlation: data.correlation,
    regression: data.regression,
    asOf: data.kpi.asOf,
    sourceComplete: ranked.length,
    sourcePolicyValid,
    sourcePolicyInvalid: ranked.length - sourcePolicyValid,
    currentComplete,
    sourceMissing: data.kpi.total - ranked.length,
    expiredRegions: ranked.length - currentComplete,
    missingRegions,
  };
}

export function applyDashboardFreshness(data, nowMillis = Date.now()) {
  const completeRegions = Object.values(data.regionsByKey).filter((region) => !region.missing);
  const expiredRegionKeys = new Set(completeRegions
    .filter((region) => {
      const validUntil = Date.parse(region.bedRiskValidUntil || "");
      return region.bedRiskFreshnessUnknown || !Number.isFinite(validUntil) || validUntil <= nowMillis;
    })
    .map((region) => region.key));
  const expiredHospitalCodes = new Set(data.allHospitals
    .filter((hospital) => hospitalBedExpired(hospital, nowMillis))
    .map((hospital) => hospital.orgCode));
  const analyticsStale = expiredRegionKeys.size > 0;

  const refreshHospital = (hospital) => (
    expiredHospitalCodes.has(hospital.orgCode) ? maskHospital(hospital) : hospital
  );
  const refreshRegion = (region) => {
    const hospitals = (region.hospitals || []).map(refreshHospital);
    return expiredRegionKeys.has(region.key)
      ? maskRegion(region, hospitals)
      : withBedCoverage(region, hospitals);
  };
  const regionsByKey = Object.fromEntries(
    Object.entries(data.regionsByKey).map(([key, region]) => [key, refreshRegion(region)]),
  );
  const ranked = data.ranked
    .map((region) => regionsByKey[region.key])
    .filter((region) => region && !region.missing)
    .sort((left, right) => right.risk - left.risk);
  const averageRisk = ranked.length > 0
    ? ranked.reduce((sum, region) => sum + region.risk, 0) / ranked.length
    : null;
  const futureExpiryTimes = completeRegions
    .map((region) => Date.parse(region.bedRiskValidUntil || ""))
    .filter((timestamp) => Number.isFinite(timestamp) && timestamp > nowMillis);

  const refreshedData = {
      ...data,
      analysisSnapshot: buildAnalysisSnapshot(data, expiredRegionKeys),
      currentRiskAvailable: ranked.length > 0,
      analyticsStale,
      bedRiskExpiredRegions: expiredRegionKeys.size,
      bedRiskExpiredHospitals: expiredHospitalCodes.size,
      nextBedRiskExpiryAt: futureExpiryTimes.length > 0
        ? new Date(Math.min(...futureExpiryTimes)).toISOString()
        : null,
      regionIndex: Object.fromEntries(
        Object.entries(data.regionIndex).map(([key, region]) => [key, refreshRegion(region)]),
      ),
      regionsByKey,
      ranked,
      kpi: {
        ...data.kpi,
        avg: averageRisk,
        high: ranked.filter((region) => region.risk > 50).length,
        complete: ranked.length,
        missing: data.kpi.total - ranked.length,
      },
      allHospitals: data.allHospitals.map(refreshHospital),
  };

  return {
    data: analyticsStale ? maskAnalyticsPayload(refreshedData) : refreshedData,
    expiredRegionKeys,
    expiredHospitalCodes,
  };
}

function freshnessVersion(expiredRegionKeys, expiredHospitalCodes, collectionStale) {
  const state = [
    `collectionStale:${collectionStale}`,
    ...[...expiredRegionKeys].sort().map((key) => `r:${key}`),
    ...[...expiredHospitalCodes].sort().map((key) => `h:${key}`),
  ].join("\n");
  return crypto.createHash("sha256").update(state).digest("hex").slice(0, 12);
}

export function maskStaleDashboardData(data) {
  const masked = applyDashboardFreshness(data, Number.POSITIVE_INFINITY);
  return maskAnalyticsPayload({
    ...masked.data,
    currentRiskAvailable: false,
    analyticsStale: true,
    bedRiskExpiredRegions: data.kpi.complete,
    bedRiskExpiredHospitals: data.allHospitals.filter(hasBedValue).length,
    nextBedRiskExpiryAt: null,
  });
}

export function getDashboardSnapshot() {
  const raw = getRawDashboardSnapshot();
  const ageMinutes = dataAgeMinutes(raw.data.kpi.asOf);
  const freshness = applyDashboardFreshness(raw.data);
  const collectionStale = (
    ageMinutes == null
    || ageMinutes > DASHBOARD_DATA_STALE_AFTER_MINUTES
  );
  const freshnessHash = freshnessVersion(
    freshness.expiredRegionKeys,
    freshness.expiredHospitalCodes,
    collectionStale,
  );
  const expiredRegions = freshness.expiredRegionKeys.size;
  const stale = collectionStale || expiredRegions > 0;
  return {
    ...raw,
    version: `${raw.version}-${freshnessHash}`,
    dataAgeMinutes: ageMinutes,
    dataStaleAfterMinutes: DASHBOARD_DATA_STALE_AFTER_MINUTES,
    dataStale: stale,
    bedRiskExpiredRegions: expiredRegions,
    bedRiskExpiredHospitals: freshness.expiredHospitalCodes.size,
    nextBedRiskExpiryAt: freshness.data.nextBedRiskExpiryAt,
    data: ageMinutes == null ? maskStaleDashboardData(raw.data) : freshness.data,
  };
}

export function readPipelineStatus() {
  try {
    return JSON.parse(fs.readFileSync(PIPELINE_STATUS_FILE, "utf-8"));
  } catch (error) {
    if (error?.code === "ENOENT") return { state: "idle", lastRun: null };
    return { state: "unknown", lastRun: null };
  }
}
