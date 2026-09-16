"use client";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { riskColor, riskTextColor } from "@/lib/riskScale";
import { SIDO_SHORT_LABELS } from "@/lib/sido";
import { cardStyle, mutedText, RiskLegendStrip } from "./shared";

const DEFAULT_SIDO = "경상북도";

const COLS = [
  { key: "bed", label: "병상포화도" },
  { key: "access", label: "접근성" },
  { key: "popBed", label: "인구대비병상" },
  { key: "doc", label: "의료진부족" },
];

const W = 380, H = 320;
const TOOLTIP_WIDTH = 260;
const TOOLTIP_GAP = 12;
const TOOLTIP_EDGE = 8;

function getTooltipPosition({ clientX, clientY }, tooltipSize) {
  const width = Math.min(tooltipSize.width, Math.max(0, window.innerWidth - TOOLTIP_EDGE * 2));
  const height = Math.min(tooltipSize.height, Math.max(0, window.innerHeight - TOOLTIP_EDGE * 2));
  const preferredLeft = clientX + TOOLTIP_GAP + width <= window.innerWidth - TOOLTIP_EDGE
    ? clientX + TOOLTIP_GAP
    : clientX - TOOLTIP_GAP - width;
  const preferredTop = clientY + TOOLTIP_GAP + height <= window.innerHeight - TOOLTIP_EDGE
    ? clientY + TOOLTIP_GAP
    : clientY - TOOLTIP_GAP - height;

  return {
    left: Math.max(TOOLTIP_EDGE, Math.min(preferredLeft, window.innerWidth - width - TOOLTIP_EDGE)),
    top: Math.max(TOOLTIP_EDGE, Math.min(preferredTop, window.innerHeight - height - TOOLTIP_EDGE)),
  };
}

function RiskNodeTooltip({ region, anchor }) {
  const tooltipRef = useRef(null);

  useLayoutEffect(() => {
    if (!region || !anchor || !tooltipRef.current) return;

    const tooltip = tooltipRef.current;
    const bounds = tooltip.getBoundingClientRect();
    const position = getTooltipPosition(anchor, bounds);
    tooltip.style.left = `${position.left}px`;
    tooltip.style.top = `${position.top}px`;
    tooltip.style.visibility = "visible";
  }, [anchor, region]);

  if (!region || !anchor || typeof document === "undefined") return null;

  return createPortal(
    <div
      ref={tooltipRef}
      role="tooltip"
      data-risk-node-tooltip={region.key}
      style={{
        position: "fixed",
        left: 0,
        top: 0,
        zIndex: 1000,
        width: TOOLTIP_WIDTH,
        maxWidth: `calc(100vw - ${TOOLTIP_EDGE * 2}px)`,
        maxHeight: `calc(100vh - ${TOOLTIP_EDGE * 2}px)`,
        overflowY: "auto",
        visibility: "hidden",
        pointerEvents: "none",
        boxSizing: "border-box",
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        fontSize: 11.5,
        lineHeight: 1.55,
        padding: "8px 10px",
        boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
        color: "#0f172a",
      }}
    >
      <b>{region.name}</b><br />
      응급실 {region.hospitalCount}개 · 의료진 {region.doctorCount}명<br />
      인구대비병상 부담 {region.popBed.toFixed(0)}점 · <span style={{ color: riskColor(region.risk) }}>위험도 {region.risk.toFixed(1)}점</span>
      {region.sourcePolicyValidAtCalculation === false ? (
        <><br /><span style={{ color: "#b91c1c" }}>마지막 계산 점수 · 계산 당시 원천시각 기준 미충족</span></>
      ) : region.scoreExpired ? (
        <><br /><span style={{ color: "#b45309" }}>마지막 계산 점수 · 병상 원천 기준시각 경과</span></>
      ) : null}
    </div>,
    document.body,
  );
}

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
export default function TreemapHeatmapPanel({ data, excludedCount = 0, expiredCount = 0 }) {
  const [highlightKey, setHighlightKey] = useState(null);
  const [hoverKey, setHoverKey] = useState(null);
  const [tooltipAnchor, setTooltipAnchor] = useState(null);
  const [province, setProvince] = useState(DEFAULT_SIDO);
  const rowRefs = useRef({});

  // 시도 필터 pill: 데이터에 실제로 존재하는 시도만, 지역 수 많은 순으로 나열.
  const sidoCounts = useMemo(() => {
    const counts = new Map();
    data.forEach((r) => counts.set(r.sido, (counts.get(r.sido) || 0) + 1));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [data]);

  const filtered = province === "전체" ? data : data.filter((r) => r.sido === province);
  const ranked = useMemo(() => [...filtered].sort((a, b) => b.risk - a.risk), [filtered]);
  // 박스 크기·색상 모두 종합위험도로 통일(이중 인코딩) — 응급실 수는 툴팁으로만 노출.
  const cells = useMemo(() => {
    const items = ranked.map((r) => ({ ...r, value: Math.max(r.risk, 3) }));
    return squarify(items, 0, 0, W, H);
  }, [ranked]);

  const hoveredCell = hoverKey ? cells.find((cell) => cell.key === hoverKey) : null;
  const hoveredRegion = hoverKey ? ranked.find((region) => region.key === hoverKey) : null;

  const showNodeTooltip = (event, key) => {
    setHoverKey(key);
    setTooltipAnchor({ clientX: event.clientX, clientY: event.clientY });
  };

  const hideNodeTooltip = () => {
    setHoverKey(null);
    setTooltipAnchor(null);
  };

  const selectProvince = (p) => {
    setProvince(p);
    setHighlightKey(null);
    setHoverKey(null);
    setTooltipAnchor(null);
  };

  useEffect(() => {
    if (highlightKey && rowRefs.current[highlightKey]) {
      rowRefs.current[highlightKey].scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [highlightKey]);

  useEffect(() => {
    if (!tooltipAnchor) return undefined;

    const dismissTooltip = () => {
      setHoverKey(null);
      setTooltipAnchor(null);
    };
    window.addEventListener("scroll", dismissTooltip, true);
    window.addEventListener("resize", dismissTooltip);
    return () => {
      window.removeEventListener("scroll", dismissTooltip, true);
      window.removeEventListener("resize", dismissTooltip);
    };
  }, [tooltipAnchor]);

  return (
    <div style={{ ...cardStyle, padding: 16 }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>지역별 위험도 — 트리맵 · 히트맵</div>
      <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 2 }}>
        {`마지막 계산 ${data.length}개 지역 · 이전값 ${expiredCount}개 · 원천 결측 ${excludedCount}개는 0점 처리 없이 제외 · 박스 크기·색상 = 종합위험도`}
      </div>
      <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 10 }}>
        해당 노드를 클릭하면 어떤 지역인지 살펴볼 수 있어요!
      </div>

      <div className="flex flex-wrap" style={{ gap: 6, marginBottom: 14 }}>
        <button onClick={() => selectProvince("전체")}
          style={{ fontSize: 11, fontWeight: 600, padding: "5px 11px", borderRadius: 999, cursor: "pointer",
            border: province === "전체" ? "1px solid #38bdf8" : "1px solid #e2e8f0",
            background: province === "전체" ? "#38bdf81a" : "#ffffff",
            color: province === "전체" ? "#0284c7" : "#64748b" }}>
          전체 ({data.length})
        </button>
        {sidoCounts.map(([sido, count]) => (
          <button key={sido} onClick={() => selectProvince(sido)}
            style={{ fontSize: 11, fontWeight: 600, padding: "5px 11px", borderRadius: 999, cursor: "pointer",
              border: province === sido ? "1px solid #38bdf8" : "1px solid #e2e8f0",
              background: province === sido ? "#38bdf81a" : "#ffffff",
              color: province === sido ? "#0284c7" : "#64748b" }}>
            {SIDO_SHORT_LABELS[sido] ?? sido} ({count})
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <div style={{ fontSize: 11.5, ...mutedText, padding: "24px 4px", textAlign: "center" }}>해당 시도에 산출된 지역이 없습니다</div>
      ) : (
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.1fr", gap: 16, alignItems: "stretch" }}>
        <div data-treemap-viewport style={{ minWidth: 0 }}>
          <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block", borderRadius: 8, overflow: "hidden" }}>
            {cells.map((c) => {
              const big = c.w > 34 && c.h > 20;
              const isHi = highlightKey === c.key;
              return (
                <g
                  key={c.key}
                  data-treemap-node={c.key}
                  onClick={() => setHighlightKey(c.key)}
                  onMouseEnter={(event) => showNodeTooltip(event, c.key)}
                  onMouseLeave={hideNodeTooltip}
                  aria-label={`${c.name}, 응급실 ${c.hospitalCount}개, 의료진 ${c.doctorCount}명, 인구대비병상 부담 ${c.popBed.toFixed(0)}점, 위험도 ${c.risk.toFixed(1)}점`}
                  style={{ cursor: "pointer" }}
                >
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
            {hoveredCell && (
              <rect
                data-treemap-hover-outline={hoveredCell.key}
                x={hoveredCell.x}
                y={hoveredCell.y}
                width={hoveredCell.w}
                height={hoveredCell.h}
                fill="none"
                stroke="#0f172a"
                strokeWidth={2.5}
                vectorEffect="non-scaling-stroke"
                pointerEvents="none"
              />
            )}
          </svg>
        </div>

        <div
          data-heatmap-viewport
          style={{ display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0, overflow: "hidden" }}
        >
          <div style={{ display: "grid", gridTemplateColumns: `64px repeat(${COLS.length}, 1fr) 50px`, gap: 3, fontSize: 9, ...mutedText,
            padding: "0 2px 6px", borderBottom: "1px solid #e2e8f0", position: "sticky", top: 0, background: "#ffffff", zIndex: 1 }}>
            <span>지역</span>
            {COLS.map((c) => <span key={c.key} style={{ textAlign: "center" }}>{c.label}</span>)}
            <span style={{ textAlign: "right" }}>위험도</span>
          </div>
          <div data-heatmap-scroll style={{ height: 0, flex: "1 1 0", minHeight: 0, overflowY: "auto" }}>
            {ranked.map((r) => {
              const isHi = highlightKey === r.key;
              const isHovered = hoverKey === r.key;
              return (
                <div key={r.key} data-heatmap-row={r.key} ref={(el) => (rowRefs.current[r.key] = el)}
                  onClick={() => setHighlightKey(r.key)}
                  onMouseEnter={(event) => showNodeTooltip(event, r.key)}
                  onMouseLeave={hideNodeTooltip}
                  aria-label={`${r.name}, 응급실 ${r.hospitalCount}개, 의료진 ${r.doctorCount}명, 인구대비병상 부담 ${r.popBed.toFixed(0)}점, 위험도 ${r.risk.toFixed(1)}점`}
                  style={{ display: "grid", gridTemplateColumns: `64px repeat(${COLS.length}, 1fr) 50px`, gap: 3, alignItems: "center",
                    padding: "4px 2px", cursor: "pointer", borderRadius: 6,
                    background: isHi ? riskColor(r.risk) + "1c" : isHovered ? "#f8fafc" : "transparent",
                    outline: isHi ? `1.5px solid ${riskColor(r.risk)}` : isHovered ? "1.5px solid #0f172a" : "none",
                    outlineOffset: -1 }}>
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
      )}

      <RiskNodeTooltip region={hoveredRegion} anchor={tooltipAnchor} />

      <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid #e2e8f0" }}><RiskLegendStrip compact /></div>
    </div>
  );
}
