import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const ROOT = path.resolve(import.meta.dirname, "..");
const source = fs.readFileSync(
  path.join(ROOT, "src", "components", "KoreaMap.jsx"),
  "utf-8",
);

test("region hover draws a non-interactive outline above base paths", () => {
  assert.match(source, /setHoverInfo\(\{ code: p\.code,/);
  assert.match(source, /const hoveredPath =/);
  assert.match(source, /data-map-hover-outline=\{hoveredPath\.code\}/);
  assert.match(source, /strokeWidth=\{1\.8 \/ view\.scale\}/);
  assert.match(source, /pointerEvents="none"/);
});

test("selected-region styling takes precedence over hover styling", () => {
  assert.match(
    source,
    /hoveredPath && !highlightedCodeSet\.has\(hoveredPath\.code\)/,
  );
  assert.match(source, /stroke=\{isHi \? "#0f172a" : "#cbd5e1"\}/);
  assert.match(source, /strokeWidth=\{\(isHi \? 2\.2 : 0\.5\) \/ view\.scale\}/);
});

test("existing click and drag handlers remain in place", () => {
  assert.match(source, /onClick=\{\(\) => r && onSelect\(r\)\}/);
  assert.match(source, /onMouseDown=\{onMouseDown\}/);
  assert.match(source, /onMouseMove=\{onMouseMove\}/);
  assert.match(source, /onMouseUp=\{endDrag\}/);
});
