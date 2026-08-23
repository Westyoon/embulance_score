"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { riskColor, riskTextColor } from "@/lib/riskScale";
import { cardStyle, mutedText, RiskLegendStrip } from "./shared";

const COLS = [
  { key: "bed", label: "병상포화도" },
  { key: "access", label: "접근성" },
  { key: "popBed", label: "인구대비병상" },
  { key: "doc", label: "의료진부족" },
];

const W = 380, H = 320;

// 외부 라이브러리(d3-hierarchy 등) 없이 직접 구현한 squarified treemap —
// 배포 환경에 특정 서브모듈이 없어서 깨지는 걸 피하려고 순수 JS로 짰다.
// items: [{ value, ... }], 반환값: 각 item에 x,y,w,h(같은 좌표계) 추가된 배열.
function squarify(items, x, y, w, h) {
  const results = [];
  if (!items.length || w <= 0 || h <= 0) return results;
  const total = items.reduce((s, d) => s + d.value, 0);
  if (total <= 0) return results;
  const scale = (w * h) / total;
  const scaled = items.map((d) => ({ ...d, area: Math.max(d.value * scale, 0.0001) }));

  const worstRatio = (row, length) => {
    const sum = row.reduce((s, r) => s + r.area, 0);
    const max = Math.max(...row.map((r) => r.area));
    const min = Math.min(...row.map((r) => r.area));
    const l2 = length * length, s2 = sum * sum;
    return Math.max((l2 * max) / s2, s2 / (l2 * min));
  };
  const layoutStrip = (row, rx, ry, rw, rh) => {
    const sum = row.reduce((s, r) => s + r.area, 0);
    if (rw >= rh) {
      const stripW = sum / rh;
      let cy = ry;
      row.forEach((r) => {
        const ch = r.area / stripW;
        results.push({ ...r, x: rx, y: cy, w: stripW, h: ch });
        cy += ch;
      });
      return { x: rx + stripW, y: ry, w: rw - stripW, h: rh };
    }
    const stripH = sum / rw;
    let cx = rx;
    row.forEach((r) => {
      const cw = r.area / stripH;
      results.push({ ...r, x: cx, y: ry, w: cw, h: stripH });
      cx += cw;
    });
    return { x: rx, y: ry + stripH, w: rw, h: rh - stripH };
  };

  let remaining = scaled;
  let rect = { x, y, w, h };
  while (remaining.length) {
    const length = rect.w >= rect.h ? rect.h : rect.w;
    let row = [remaining[0]];
    let idx = 1;
    while (idx < remaining.length) {
      const testRow = [...row, remaining[idx]];
      if (worstRatio(testRow, length) <= worstRatio(row, length)) { row = testRow; idx++; }
      else break;
    }
    remaining = remaining.slice(row.length);
    rect = layoutStrip(row, rect.x, rect.y, rect.w, rect.h);
  }
  return results;
}

// 트리맵 + 히트맵을 하나의 섹션으로 결합. 트리맵에서 지역을 클릭하면 별도
// 상세 카드를 띄우는 대신, 오른쪽 히트맵 표에서 해당 행을 강조하고 그
// 위치로 스크롤한다 — highlightKey 하나를 양쪽이 같이 읽고 쓴다.
export default function TreemapHeatmapPanel({ data }) {
  const [highlightKey, setHighlightKey] = useState(null);
  const rowRefs = useRef({});
  const ranked = useMemo(() => [...data].sort((a, b) => b.risk - a.risk), [data]);
  const cells = useMemo(() => {
    const items = ranked.map((r) => ({ ...r, value: Math.max(r.hospitalCount, 1) }));
    return squarify(items, 0, 0, W, H);
  }, [ranked]);

  useEffect(() => {
    if (highlightKey && rowRefs.current[highlightKey]) {
      rowRefs.current[highlightKey].scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [highlightKey]);

  return (
    <div style={{ ...cardStyle, padding: 16 }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>지역별 위험도 — 트리맵 · 히트맵</div>
      <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 10 }}>박스 크기=응급실 수 · 색상=종합위험도 · 어느 쪽에서 클릭해도 서로 강조돼요</div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.1fr", gap: 16 }}>
        <div>
          <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block", borderRadius: 8, overflow: "hidden" }}>
            {cells.map((c) => {
              const big = c.w > 34 && c.h > 20;
              const isHi = highlightKey === c.key;
              return (
                <g key={c.key} onClick={() => setHighlightKey(c.key)} style={{ cursor: "pointer" }}>
                  <rect x={c.x} y={c.y} width={c.w} height={c.h} fill={riskColor(c.risk)}
                    stroke={isHi ? "#0f172a" : "#ffffff"} strokeWidth={isHi ? 2.5 : 1} />
                  {big && (
                    <>
                      <text x={c.x + c.w / 2} y={c.y + c.h / 2 - 2} textAnchor="middle" fontSize="9.5" fontWeight="700"
                        fill="#ffffff" style={{ textShadow: "0 1px 2px rgba(0,0,0,.45)" }}>
                        {c.name.length > 5 ? c.name.replace(/(시|군|구)$/, "") : c.name}
                      </text>
                      <text x={c.x + c.w / 2} y={c.y + c.h / 2 + 10} textAnchor="middle" fontSize="8.5"
                        fill="#ffffffd9" style={{ textShadow: "0 1px 2px rgba(0,0,0,.45)" }}>
                        {c.risk.toFixed(0)}
                      </text>
                    </>
                  )}
                </g>
              );
            })}
          </svg>
        </div>

        <div>
          <div style={{ display: "grid", gridTemplateColumns: `64px repeat(${COLS.length}, 1fr) 50px`, gap: 3, fontSize: 9, ...mutedText,
            padding: "0 2px 6px", borderBottom: "1px solid #e2e8f0", position: "sticky", top: 0, background: "#ffffff", zIndex: 1 }}>
            <span>지역</span>
            {COLS.map((c) => <span key={c.key} style={{ textAlign: "center" }}>{c.label}</span>)}
            <span style={{ textAlign: "right" }}>위험도</span>
          </div>
          <div style={{ maxHeight: 320, overflowY: "auto" }}>
            {ranked.map((r) => {
              const isHi = highlightKey === r.key;
              return (
                <div key={r.key} ref={(el) => (rowRefs.current[r.key] = el)} onClick={() => setHighlightKey(r.key)}
                  style={{ display: "grid", gridTemplateColumns: `64px repeat(${COLS.length}, 1fr) 50px`, gap: 3, alignItems: "center",
                    padding: "4px 2px", cursor: "pointer", borderRadius: 6, background: isHi ? riskColor(r.risk) + "1c" : "transparent",
                    outline: isHi ? `1.5px solid ${riskColor(r.risk)}` : "none", outlineOffset: -1 }}>
                  <span style={{ fontSize: 11, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
                  {COLS.map((c) => (
                    <span key={c.key} style={{ fontSize: 10, fontWeight: 700, textAlign: "center", borderRadius: 5, padding: "3px 0",
                      color: riskTextColor(r[c.key]), background: riskColor(r[c.key]) + "30" }}>
                      {Math.round(r[c.key])}
                    </span>
                  ))}
                  <span style={{ fontSize: 11.5, fontWeight: 800, textAlign: "right", color: riskTextColor(r.risk) }}>{r.risk.toFixed(1)}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid #e2e8f0" }}><RiskLegendStrip compact /></div>
    </div>
  );
}
