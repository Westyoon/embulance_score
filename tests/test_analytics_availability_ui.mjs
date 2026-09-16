import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const ROOT = path.resolve(import.meta.dirname, "..");
const readComponent = (name) => fs.readFileSync(
  path.join(ROOT, "src", "components", name),
  "utf-8",
);

const dashboard = readComponent("Dashboard.jsx");
const analytics = readComponent("AnalyticsTab.jsx");
const contribution = readComponent("ContributionPanel.jsx");
const treemap = readComponent("TreemapHeatmapPanel.jsx");
const mapTab = readComponent("MapTab.jsx");
const regionPopup = readComponent("RegionPopup.jsx");
const hospitalPopup = readComponent("HospitalPopup.jsx");

test("stale analytics no longer blocks the entire analytics tab", () => {
  assert.doesNotMatch(dashboard, /\{data\.analyticsStale\s*\?\s*\(/);
  assert.match(dashboard, /<AnalyticsTab data=\{data\} \/>/);
});

test("last-known analysis snapshot keeps risk scores visible safely", () => {
  assert.match(analytics, /const analysisData = analysisSnapshot \?\? data;/);
  assert.match(analytics, /const isHistoricalSnapshot = expiredCount > 0/);
  assert.match(analytics, /ranked: rankedRows = \[\]/);
  assert.match(analytics, /rankedRows\.filter\(\(region\) => Number\.isFinite\(region\.risk\)\)/);
  assert.match(analytics, /마지막 수집·계산된 위험도 점수를 표시 중입니다/);
  assert.match(analytics, /마지막 계산값을 포함합니다/);
  assert.match(analytics, /기준시각 경과·결측·원천기준 주의 지역 보기/);
  assert.match(analytics, /averageRisk == null \? "-" : averageRisk\.toFixed\(1\)/);
  assert.match(treemap, /마지막 계산 \$\{data\.length\}개 지역/);
  assert.match(treemap, /c\.sourcePolicyValidAtCalculation === false/);
});

test("dashboard and details clearly label retained values instead of hiding them", () => {
  assert.match(dashboard, /마지막 수집값을 표시 중입니다/);
  assert.match(dashboard, /마지막 성공 수집값과 계산 점수를 유지합니다/);
  assert.match(dashboard, /자동 갱신이 성공하면 최신값으로 교체됩니다/);
  assert.doesNotMatch(dashboard, /숨겼습니다/);
  assert.match(mapTab, /h\.bedDataStale \? " · 이전값"/);
  assert.match(regionPopup, /region\.bedRiskStale/);
  assert.match(regionPopup, /마지막 계산값 · 병상 원천 기준시각 경과/);
  assert.match(hospitalPopup, /hospital\.bedDataStale/);
  assert.match(hospitalPopup, /마지막 수집 응급실 병상/);
});

test("snapshot metadata distinguishes expired scores from source missing regions", () => {
  assert.match(analytics, /analysisSnapshot\?\.missingRegions \?\? \[\]/);
  assert.match(analytics, /ranked\.filter\(\(region\) => region\.scoreExpired\)/);
  assert.match(analytics, /analysisSnapshot\?\.sourceComplete/);
  assert.match(analytics, /analysisSnapshot\?\.currentComplete/);
  assert.match(analytics, /analysisSnapshot\?\.sourceMissing/);
  assert.match(analytics, /analysisSnapshot\?\.expiredRegions/);
  assert.match(analytics, /analysisSnapshot\?\.sourcePolicyInvalid/);
});

test("available snapshot analyses render while genuinely absent panels get notices", () => {
  assert.match(analytics, /correlation\.length === 0 \?/);
  assert.doesNotMatch(analytics, /analyticsStale \|\| correlation\.length === 0/);
  assert.match(analytics, /clusterIds\.length === 0 \|\| clusterProfile\.length === 0/);
  assert.match(analytics, /<ContributionPanel/);
  assert.match(contribution, /historical && regression\.coef\.length > 0/);
  assert.match(contribution, /마지막 계산값 기준이며 현재 실시간 값이 아닙니다/);
});
