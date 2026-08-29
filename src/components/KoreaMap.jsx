"use client";
import { useRef, useState, useMemo, useCallback } from "react";
import { Plus, Minus, RotateCcw } from "lucide-react";
import { riskColor, riskTextColor, riskLabel, bedStatusColor, RISK_LEVELS, MISSING_COLOR } from "@/lib/riskScale";
import { computeBounds, makeProjector, geometryPath, centroidOf } from "@/lib/geoProject";
import { hashCode, seededRandom } from "@/lib/mockDetail";
import { mutedText } from "./shared";

const W = 520, H = 620;

export default function KoreaMap({ geo, regionIndex, onSelect, highlightCodes, selectedHospital, hospitalRegionCode }) {
  const containerRef = useRef(null);
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 });
  const dragRef = useRef(null);
  const [hoverInfo, setHoverInfo] = useState(null);

  const mapGeometry = useMemo(() => {
    const bounds = computeBounds(geo);
    const project = makeProjector(bounds, W, H, 14);
    const paths = geo.features.map((f) => {
      const [cx, cy] = centroidOf(f.geometry, project);
      return { code: f.properties.code, name: f.properties.name, d: geometryPath(f.geometry, project), cx, cy };
    });
    return { paths, project };
  }, [geo]);
  const { paths, project } = mapGeometry;
  const highlightedCodeSet = useMemo(() => new Set(highlightCodes ?? []), [highlightCodes]);
  const highlighted = paths.filter((path) => highlightedCodeSet.has(path.code));

  // 병원 상세 패널이 열려있는 동안만 지도에 마커 1개를 띄운다 — 별도 state 없이
  // selectedHospital 하나에서 파생시켜, 패널을 닫으면 마커도 자동으로 사라진다.
  const hospitalMarker = useMemo(() => {
    if (!selectedHospital || !hospitalRegionCode) return null;
    const region = paths.find((p) => p.code === hospitalRegionCode);
    if (!region) return null;
    const latitude = Number(selectedHospital.latitude);
    const longitude = Number(selectedHospital.longitude);
    if (Number.isFinite(latitude) && Number.isFinite(longitude)) {
      const [x, y] = project(longitude, latitude);
      return { ...selectedHospital, x, y };
    }
    const seed = hashCode((selectedHospital.orgCode || selectedHospital.name) + hospitalRegionCode);
    const angle = seededRandom(seed) * Math.PI * 2;
    const dist = 3 + seededRandom(seed + 1) * 4;
    return { ...selectedHospital, x: region.cx + Math.cos(angle) * dist, y: region.cy + Math.sin(angle) * dist };
  }, [paths, project, selectedHospital, hospitalRegionCode]);

  const clampView = (v) => {
    const scale = Math.min(8, Math.max(1, v.scale));
    const maxX = (W * scale - W) / 2 + 40;
    const maxY = (H * scale - H) / 2 + 40;
    return { scale, x: Math.min(maxX, Math.max(-maxX, v.x)), y: Math.min(maxY, Math.max(-maxY, v.y)) };
  };
  const zoomBy = useCallback((factor) => setView((v) => clampView({ ...v, scale: v.scale * factor })), []);
  const onWheel = (e) => { e.preventDefault(); zoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15); };
  const onMouseDown = (e) => { dragRef.current = { sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y }; };
  const onMouseMove = (e) => {
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.sx, dy = e.clientY - dragRef.current.sy;
    setView((v) => clampView({ ...v, x: dragRef.current.ox + dx, y: dragRef.current.oy + dy }));
  };
  const endDrag = () => { dragRef.current = null; };

  return (
    <div ref={containerRef} style={{ position: "relative", flex: 1, overflow: "hidden", borderRadius: 10, background: "#eef2f7", cursor: "grab" }}
      onWheel={onWheel} onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseUp={endDrag} onMouseLeave={endDrag}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "100%", display: "block" }}>
        <g style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`, transformOrigin: "center" }}>
          {paths.map((p) => {
            const r = regionIndex[p.code];
            const fill = r && !r.missing ? riskColor(r.risk) : MISSING_COLOR;
            const isHi = highlightedCodeSet.has(p.code);
            return (
              <path key={p.code} d={p.d} fill={fill} fillOpacity={isHi ? 1 : 0.92}
                stroke={isHi ? "#0f172a" : "#cbd5e1"} strokeWidth={(isHi ? 2.2 : 0.5) / view.scale}
                onClick={() => r && onSelect(r)}
                onMouseEnter={(e) => setHoverInfo({ name: r?.name ?? p.name, risk: r?.missing ? null : r?.risk, x: e.clientX, y: e.clientY })}
                onMouseMove={(e) => setHoverInfo((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))}
                onMouseLeave={() => setHoverInfo(null)}
                style={{ cursor: "pointer" }} />
            );
          })}
          {highlighted.map((path) => (
            <g key={`highlight-${path.code}`} style={{ pointerEvents: "none" }}>
              <circle cx={path.cx} cy={path.cy} r={6 / view.scale} fill="none" stroke="#0f172a" strokeWidth={1.4 / view.scale}>
                <animate attributeName="r" values={`${5 / view.scale};${13 / view.scale};${5 / view.scale}`} dur="1.5s" repeatCount="indefinite" />
                <animate attributeName="opacity" values="0.9;0;0.9" dur="1.5s" repeatCount="indefinite" />
              </circle>
              <circle cx={path.cx} cy={path.cy} r={3.5 / view.scale} fill="#0f172a" stroke="#ffffff" strokeWidth={1.2 / view.scale} />
            </g>
          ))}
          {hospitalMarker && (() => {
            const r = 4.2 / view.scale;
            const m = hospitalMarker;
            return (
              <g
                onMouseEnter={(e) => setHoverInfo({ type: "hospital", name: m.name, status: m.status, x: e.clientX, y: e.clientY })}
                onMouseMove={(e) => setHoverInfo((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))}
                onMouseLeave={() => setHoverInfo(null)}>
                <circle cx={m.x} cy={m.y} r={r * 1.9} fill="none" stroke={bedStatusColor[m.status]} strokeWidth={1.2 / view.scale} opacity={0.5}>
                  <animate attributeName="r" values={`${r * 1.4};${r * 2.6};${r * 1.4}`} dur="1.4s" repeatCount="indefinite" />
                  <animate attributeName="opacity" values="0.55;0;0.55" dur="1.4s" repeatCount="indefinite" />
                </circle>
                <circle cx={m.x} cy={m.y} r={r} fill={bedStatusColor[m.status]} stroke="#ffffff" strokeWidth={1.3 / view.scale} />
                <line x1={m.x - r * 0.5} y1={m.y} x2={m.x + r * 0.5} y2={m.y} stroke="#ffffff" strokeWidth={0.9 / view.scale} />
                <line x1={m.x} y1={m.y - r * 0.5} x2={m.x} y2={m.y + r * 0.5} stroke="#ffffff" strokeWidth={0.9 / view.scale} />
              </g>
            );
          })()}
        </g>
      </svg>

      {hoverInfo && hoverInfo.type === "hospital" && (
        <div style={{ position: "fixed", left: hoverInfo.x + 14, top: hoverInfo.y + 10, background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, boxShadow: "0 2px 6px rgba(15,23,42,0.1)",
          padding: "6px 10px", fontSize: 11.5, pointerEvents: "none", zIndex: 40, whiteSpace: "nowrap" }}>
          <b>{hoverInfo.name}</b> <span style={{ color: bedStatusColor[hoverInfo.status] }}>· {hoverInfo.status}</span>
        </div>
      )}
      {hoverInfo && !hoverInfo.type && (
        <div style={{ position: "fixed", left: hoverInfo.x + 14, top: hoverInfo.y + 10, background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, boxShadow: "0 2px 6px rgba(15,23,42,0.1)",
          padding: "6px 10px", fontSize: 11.5, pointerEvents: "none", zIndex: 40, whiteSpace: "nowrap" }}>
          <b>{hoverInfo.name}</b>{" "}
          {hoverInfo.risk == null ? <span style={{ color: "#64748b" }}>· 미산출</span> : <span style={{ color: riskTextColor(hoverInfo.risk) }}>· {hoverInfo.risk.toFixed(1)}점 ({riskLabel(hoverInfo.risk)})</span>}
        </div>
      )}

      <div style={{ position: "absolute", right: 10, top: 10, display: "flex", flexDirection: "column", gap: 4 }}>
        {[{ icon: Plus, fn: () => zoomBy(1.3) }, { icon: Minus, fn: () => zoomBy(1 / 1.3) }, { icon: RotateCcw, fn: () => setView({ scale: 1, x: 0, y: 0 }) }].map((b, i) => (
          <button key={i} onClick={b.fn} style={{ width: 26, height: 26, borderRadius: 7, background: "#ffffff", border: "1px solid #e2e8f0", color: "#0f172a", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", boxShadow: "0 1px 3px rgba(15,23,42,0.08)" }}>
            <b.icon size={13} />
          </button>
        ))}
      </div>

      <div style={{ position: "absolute", left: 10, bottom: 10, background: "#ffffffe6", border: "1px solid #e2e8f0", borderRadius: 10, padding: "8px 10px", boxShadow: "0 1px 3px rgba(15,23,42,0.08)" }}>
        <div style={{ fontSize: 10, fontWeight: 700, marginBottom: 5, ...mutedText }}>등급 기준 (종합위험도 점수)</div>
        {RISK_LEVELS.map((l) => (
          <div key={l.label} className="flex items-center gap-2" style={{ fontSize: 10, marginBottom: 2 }}>
            <span style={{ width: 10, height: 10, background: l.color, borderRadius: 2, display: "inline-block" }} />
            <span style={{ width: 42 }}>{l.label}</span>
            <span style={{ ...mutedText }}>{l.range}</span>
          </div>
        ))}
        <div className="flex items-center gap-2" style={{ fontSize: 10, marginTop: 2 }}>
          <span style={{ width: 10, height: 10, background: MISSING_COLOR, borderRadius: 2, display: "inline-block", border: "1px solid #94a3b8" }} />
          <span>미산출 (원천데이터부족)</span>
        </div>
        {hospitalMarker && (
          <>
            <div style={{ borderTop: "1px solid #e2e8f0", margin: "6px 0" }} />
            <div className="flex items-center gap-2" style={{ fontSize: 10.5 }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: bedStatusColor[hospitalMarker.status], display: "inline-block" }} />
              <span style={{ fontWeight: 600 }}>{hospitalMarker.name}</span>
              <span style={{ color: bedStatusColor[hospitalMarker.status] }}>· {hospitalMarker.status}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
