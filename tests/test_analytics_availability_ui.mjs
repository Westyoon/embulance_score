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
  assert.doesNotMatch(analytics, /마지막 수집·계산된 위험도 점수를 표시 중입니다/);
  assert.doesNotMatch(analytics, /산출된 위험도 점수는 계속 표시합니다/);
  assert.match(analytics, /마지막 계산값을 포함합니다/);
  assert.match(analytics, /기준시각 경과·결측·원천기준 주의 지역 보기/);
  assert.match(analytics, /averageRisk == null \? "-" : averageRisk\.toFixed\(1\)/);
  assert.match(treemap, /마지막 계산 \$\{data\.length\}개 지역/);
  assert.match(treemap, /이전값 \$\{expiredCount\}개 · 원천 결측 \$\{excludedCount\}개는 0점 처리 없이 제외/);
  assert.match(treemap, /박스 크기·색상 = 종합위험도/);
  assert.match(treemap, /해당 노드를 클릭하면 어떤 지역인지 살펴볼 수 있어요!/);
  assert.match(treemap, /c\.sourcePolicyValidAtCalculation === false/);
});

test("treemap and heatmap nodes share a visible hover outline", () => {
  assert.match(treemap, /const \[hoverKey, setHoverKey\] = useState\(null\)/);
  assert.match(treemap, /data-treemap-node=\{c\.key\}/);
  assert.match(treemap, /onMouseEnter=\{\(\) => setHoverKey\(c\.key\)\}/);
  assert.match(treemap, /data-treemap-hover-outline=\{hoveredCell\.key\}/);
  assert.match(treemap, /stroke="#0f172a"/);
  assert.match(treemap, /pointerEvents="none"/);
  assert.match(treemap, /data-heatmap-row=\{r\.key\}/);
  assert.match(treemap, /onMouseEnter=\{\(\) => setHoverKey\(r\.key\)\}/);
  assert.match(treemap, /const isHovered = hoverKey === r\.key/);
  assert.match(treemap, /isHovered \? "1\.5px solid #0f172a"/);
});

test("dashboard and details clearly label retained values instead of hiding them", () => {
  assert.match(dashboard, /최종 업데이트:/);
  assert.match(dashboard, /color: "#2563eb"/);
  assert.match(dashboard, /pipeline\?\.finishedAt/);
  assert.match(dashboard, /timeZone: "Asia\/Seoul"/);
  assert.doesNotMatch(dashboard, /마지막 업데이트/);
  assert.match(analytics, /label="최종 업데이트"/);
  assert.doesNotMatch(analytics, /점수 기준 시각/);
  assert.doesNotMatch(dashboard, /다음 업데이트 시각은/);
  assert.doesNotMatch(dashboard, /마지막 성공 수집값을 유지하고 있습니다/);
  assert.doesNotMatch(dashboard, /최근 갱신 실패/);
  assert.doesNotMatch(dashboard, /숨겼습니다/);
  assert.match(mapTab, /h\.bedDataStale \? " · 이전값"/);
  assert.match(regionPopup, /region\.bedRiskStale/);
  assert.match(regionPopup, /마지막 계산값 · 병상 원천 기준시각 경과/);
  assert.match(hospitalPopup, /hospital\.bedDataStale/);
  assert.match(hospitalPopup, /마지막 수집 응급실 병상/);
});

test("only the selected analytics notice content remains", () => {
  assert.doesNotMatch(dashboard, /updateNoticeExpanded/);
  assert.doesNotMatch(dashboard, /update-notice-details/);
  assert.match(analytics, /아래 평균·순위·차트는/);
  assert.match(analytics, /기준시각 경과·결측·원천기준 주의 지역 보기/);
});

test("missing region guidance names both missing and available component data", () => {
  assert.match(regionPopup, /const availableComponents = COMPONENTS/);
  assert.match(regionPopup, /\.filter\(\(\{ key \}\) => isFiniteNumber\(region\[key\]\)\)/);
  assert.match(regionPopup, /\{missingComponents\} 데이터 결측/);
  assert.match(regionPopup, /미산출 지역/);
  assert.match(regionPopup, /\{availableComponents\} 데이터/);
  assert.match(regionPopup, /원하신다면/);
  assert.match(regionPopup, /데이터<\/b>를 살펴봐 주세요/);
  assert.doesNotMatch(regionPopup, /0점이 아니라/);
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
