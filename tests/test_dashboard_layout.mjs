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
const mapTab = readComponent("MapTab.jsx");
const koreaMap = readComponent("KoreaMap.jsx");
const regionPopup = readComponent("RegionPopup.jsx");
const hospitalPopup = readComponent("HospitalPopup.jsx");

test("map layout follows content instead of a fixed dashboard height", () => {
  assert.doesNotMatch(dashboard, /height:\s*tab === "map"/);
  assert.doesNotMatch(mapTab, /height:\s*"100%"/);
  assert.match(
    mapTab,
    /grid-cols-1 items-start lg:grid-cols-\[minmax\(0,1fr\)_320px\]/,
  );
});

test("lists and detail panels leave vertical scrolling to the page", () => {
  for (const [name, source] of [
    ["MapTab", mapTab],
    ["RegionPopup", regionPopup],
    ["HospitalPopup", hospitalPopup],
  ]) {
    assert.doesNotMatch(source, /overflowY:\s*"auto"/, `${name} has a nested vertical scroller`);
    assert.doesNotMatch(source, /maxHeight:\s*\d+/, `${name} still caps content height`);
  }
});

test("map keeps a responsive viewport and its pan and zoom clipping", () => {
  assert.match(koreaMap, /height:\s*"clamp\(320px, min\(62vw, 72svh\), 620px\)"/);
  assert.match(koreaMap, /overflow:\s*"hidden"/);
  assert.match(koreaMap, /onWheel=\{onWheel\}/);
  assert.match(koreaMap, /onMouseDown=\{onMouseDown\}/);
  assert.match(koreaMap, /onMouseMove=\{onMouseMove\}/);
  assert.match(koreaMap, /onMouseUp=\{endDrag\}/);
});

test("ordinary wheel scrolls the page while modifier wheel zooms the map", () => {
  assert.match(koreaMap, /if \(!e\.ctrlKey && !e\.metaKey\) return;/);
  assert.match(
    koreaMap,
    /if \(!e\.ctrlKey && !e\.metaKey\) return;\s*e\.preventDefault\(\);\s*zoomBy\(/,
  );
});

test("desktop map stays visible and compact controls have accessible names", () => {
  assert.match(mapTab, /className="lg:sticky lg:top-4"/);
  assert.doesNotMatch(mapTab, /className="sticky/);
  assert.match(koreaMap, /aria-label=\{b\.label\}/);
  assert.match(mapTab, /aria-label=\{`\$\{placeholder\} 검색어 지우기`\}/);
});
