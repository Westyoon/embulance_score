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

test("stale analytics no longer blocks the entire analytics tab", () => {
  assert.doesNotMatch(dashboard, /analyticsStale\s*\?/);
  assert.match(dashboard, /<AnalyticsTab data=\{data\} \/>/);
});

test("last-known analysis snapshot keeps risk scores visible safely", () => {
  assert.match(analytics, /const analysisData = analysisSnapshot \?\? data;/);
  assert.match(analytics, /analysisSnapshot != null && expiredCount > 0/);
  assert.match(analytics, /ranked: rankedRows = \[\]/);
  assert.match(analytics, /rankedRows\.filter\(\(region\) => Number\.isFinite\(region\.risk\)\)/);
  assert.match(analytics, /최근 계산된 위험도 점수를 참고용으로 표시 중입니다/);
  assert.match(analytics, /현재 유효한 지역은/);
  assert.match(analytics, /만료·결측·원천기준 주의 지역 보기/);
  assert.match(analytics, /averageRisk == null \? "-" : averageRisk\.toFixed\(1\)/);
  assert.match(treemap, /최근 계산 \$\{data\.length\}개 지역/);
  assert.match(treemap, /c\.sourcePolicyValidAtCalculation === false/);
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
  assert.match(contribution, /최근 계산값 기준이며 현재 실시간 값이 아닙니다/);
});
