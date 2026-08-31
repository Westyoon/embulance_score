import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

const ROOT = path.resolve(import.meta.dirname, "..");
const SNAPSHOT_SOURCE = fs.readFileSync(
  path.join(ROOT, "src", "lib", "dashboardSnapshot.js"),
  "utf-8",
);

function syntheticModule(context, exports) {
  return new vm.SyntheticModule(
    Object.keys(exports),
    function setExports() {
      for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
    },
    { context },
  );
}

async function loadSnapshotModule(env = {}) {
  const context = vm.createContext({
    console,
    process: {
      cwd: () => ROOT,
      env: { ...process.env, ...env },
    },
  });
  const snapshotModule = new vm.SourceTextModule(SNAPSHOT_SOURCE, {
    context,
    identifier: path.join(ROOT, "src", "lib", "dashboardSnapshot.js"),
  });
  await snapshotModule.link(async (specifier) => {
    if (specifier === "server-only") return syntheticModule(context, {});
    if (specifier === "node:crypto") return syntheticModule(context, { default: crypto });
    if (specifier === "node:fs") return syntheticModule(context, { default: fs });
    if (specifier === "node:path") return syntheticModule(context, { default: path });
    if (specifier === "./csvServer") {
      return syntheticModule(context, {
        BOUNDARY_FILE: path.join(ROOT, "test-fixtures", "missing-boundary.json"),
        DATA_DIR: path.join(ROOT, "test-fixtures", "missing-data"),
      });
    }
    if (specifier === "./loadDashboardData") {
      return syntheticModule(context, {
        DASHBOARD_SOURCE_FILES: ["snapshot.csv"],
        loadDashboardData: () => {
          throw new Error("not used by focused tests");
        },
      });
    }
    throw new Error(`Unexpected import: ${specifier}`);
  });
  await snapshotModule.evaluate();
  return snapshotModule.namespace;
}

function hospital(orgCode, saturation, bedValidUntil) {
  return {
    orgCode,
    status: saturation == null ? "결측" : "주의",
    availableBeds: saturation == null ? 2 : 1,
    totalBeds: 10,
    saturation,
    bedValidUntil,
  };
}

function region(key, { missing, risk, bedRiskValidUntil, hospitals }) {
  return {
    key,
    name: key,
    missing,
    risk,
    bed: risk,
    access: 10,
    popBed: 20,
    doc: 30,
    missingComponents: missing ? ["의료진부족"] : [],
    bedDataHospitals: 99,
    totalHospitals: 99,
    bedDataCoverage: 1,
    bedDataQuality: "전체응답",
    bedRiskValidUntil,
    bedRiskFreshnessUnknown: false,
    cluster: 1,
    clusterLabel: "기존 군집",
    clusterColor: "#fff",
    hospitals,
  };
}

test("freshness recomputes every region's coverage and hides stale analytics", async () => {
  const { applyDashboardFreshness } = await loadSnapshotModule();
  const now = Date.parse("2026-09-01T00:00:00.000Z");
  const expired = "2026-08-31T23:59:59.000Z";
  const fresh = "2026-09-01T01:00:00.000Z";
  const hospitalsA = [hospital("A1", 80, expired), hospital("A2", null, fresh)];
  const hospitalsB = [hospital("B1", 90, expired), hospital("B2", 30, fresh)];
  const hospitalsC = [hospital("C1", 60, fresh)];
  const regions = {
    A: region("A", { missing: false, risk: 70, bedRiskValidUntil: expired, hospitals: hospitalsA }),
    B: region("B", { missing: true, risk: null, bedRiskValidUntil: null, hospitals: hospitalsB }),
    C: region("C", { missing: false, risk: 30, bedRiskValidUntil: fresh, hospitals: hospitalsC }),
  };
  const data = {
    regionsByKey: regions,
    regionIndex: {
      "geo-a": { ...regions.A, code: "geo-a" },
      "geo-b": { ...regions.B, code: "geo-b" },
      "geo-c": { ...regions.C, code: "geo-c" },
    },
    ranked: [regions.A, regions.C],
    allHospitals: [...hospitalsA, ...hospitalsB, ...hospitalsC],
    kpi: { total: 3, complete: 2, missing: 1, avg: 50, high: 1 },
    clusterProfile: [{ subject: "병상포화도", c1: 50 }],
    clusterIds: [1],
    clusterMetaById: { 1: { label: "기존 군집" } },
    correlation: [{ name: "병상포화도", r: 0.5 }],
    regression: { coef: [{ name: "병상", value: 1 }], r2: 0.8, mae: 2, rows: 2 },
  };

  const result = applyDashboardFreshness(data, now).data;

  assert.equal(result.analyticsStale, true);
  assert.deepEqual(
    [
      result.regionsByKey.A.bedDataHospitals,
      result.regionsByKey.B.bedDataHospitals,
      result.regionsByKey.C.bedDataHospitals,
    ],
    [0, 1, 1],
  );
  assert.deepEqual(
    [
      result.regionsByKey.A.totalHospitals,
      result.regionsByKey.B.totalHospitals,
      result.regionsByKey.C.totalHospitals,
    ],
    [2, 2, 1],
  );
  assert.equal(result.regionsByKey.A.bedDataQuality, "결측");
  assert.equal(result.regionsByKey.B.bedDataQuality, "부분응답");
  assert.equal(result.regionsByKey.B.bedDataCoverage, 0.5);
  assert.equal(result.regionsByKey.C.bedDataQuality, "전체응답");
  assert.equal(result.regionIndex["geo-b"].bedDataHospitals, 1);
  assert.deepEqual([...result.clusterProfile], []);
  assert.deepEqual([...result.clusterIds], []);
  assert.deepEqual({ ...result.clusterMetaById }, {});
  assert.deepEqual([...result.correlation], []);
  assert.deepEqual([...result.regression.coef], []);
  assert.equal(result.regression.r2, null);
  assert.equal(result.regionsByKey.C.cluster, null);
  assert.equal(result.ranked.length, 1);
  assert.equal(result.ranked[0].key, "C");
});

test("dashboard version changes with bed freshness policy and build identity", async () => {
  const twelveHours = await loadSnapshotModule({
    BED_SOURCE_MAX_AGE_HOURS: "12",
    DASHBOARD_BUILD_ID: "build-a",
  });
  const twentyFourHours = await loadSnapshotModule({
    BED_SOURCE_MAX_AGE_HOURS: "24",
    DASHBOARD_BUILD_ID: "build-a",
  });
  const nextBuild = await loadSnapshotModule({
    BED_SOURCE_MAX_AGE_HOURS: "12",
    DASHBOARD_BUILD_ID: "build-b",
  });

  assert.notEqual(twelveHours.dashboardVersion(), twentyFourHours.dashboardVersion());
  assert.notEqual(twelveHours.dashboardVersion(), nextBuild.dashboardVersion());
});

test("popup selections are stored as identifiers and missing regions retain coverage UI", () => {
  const mapSource = fs.readFileSync(path.join(ROOT, "src", "components", "MapTab.jsx"), "utf-8");
  const popupSource = fs.readFileSync(path.join(ROOT, "src", "components", "RegionPopup.jsx"), "utf-8");

  assert.match(mapSource, /selectedRegionRef/);
  assert.match(mapSource, /selectedHospitalCode/);
  assert.match(mapSource, /allHospitals\.find/);
  assert.doesNotMatch(mapSource, /setSelectedHospital\(enrichHospital/);
  assert.ok(
    popupSource.indexOf("<BedCoverageDisclosure region={region} />")
      < popupSource.indexOf("const data = COMPONENTS.map"),
    "missing-region early return must render the coverage disclosure",
  );
});
