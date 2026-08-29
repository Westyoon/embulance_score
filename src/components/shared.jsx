"use client";
import { useState } from "react";
import { RISK_LEVELS, MISSING_COLOR } from "@/lib/riskScale";

export const cardStyle = { background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 14 };
export const pageBg = { background: "#f8fafc", minHeight: "100%", color: "#0f172a" };
export const mutedText = { color: "#64748b" };

// 탭 그룹(상단 탭, 사이드 패널 토글 등)에 공용으로 쓰는 컴포넌트.
// hover는 Tailwind :hover 클래스가 아니라 JS state로 직접 추적한다 — 인라인
// style로 배경색을 지정하면 인라인 스타일이 항상 클래스보다 우선하기 때문에
// hover: 클래스 자체가 먹히지 않는 문제가 있었다.
export function TabGroup({ options, active, onChange }) {
  const [hovered, setHovered] = useState(null);
  return (
    <div style={{ background: "#f1f5f9", border: "1px solid #e2e8f0", borderRadius: 14, padding: 4, display: "flex" }}>
      {options.map((opt) => {
        const isActive = active === opt.key;
        const isHover = hovered === opt.key && !isActive;
        return (
          <button key={opt.key} onClick={() => onChange(opt.key)}
            onMouseEnter={() => setHovered(opt.key)} onMouseLeave={() => setHovered(null)}
            className="flex items-center justify-center gap-1.5"
            style={{ flex: 1, padding: "8px 16px", fontSize: 12.5, fontWeight: 600, borderRadius: 10, border: "none", cursor: "pointer", whiteSpace: "nowrap",
              background: isActive ? "#ffffff" : isHover ? "#e2e8f0" : "transparent",
              color: isActive || isHover ? "#0f172a" : "#64748b",
              boxShadow: isActive ? "0 1px 3px rgba(15,23,42,0.1)" : "none",
              transition: "background .12s, color .12s" }}>
            {opt.icon && <opt.icon size={14} />} {opt.label}
          </button>
        );
      })}
    </div>
  );
}

// 위험도 색상이 쓰이는 모든 화면(지도, 랭킹 차트 등)에 항상 함께 노출되는 기준표.
export function RiskLegendStrip({ compact }) {
  return (
    <div className="flex items-center flex-wrap" style={{ gap: compact ? 10 : 14 }}>
      {RISK_LEVELS.map((l) => (
        <div key={l.label} className="flex items-center gap-1.5" style={{ fontSize: 10.5 }}>
          <span style={{ width: 10, height: 10, background: l.color, borderRadius: 3, display: "inline-block" }} />
          <span style={{ fontWeight: 600 }}>{l.label}</span>
          <span style={mutedText}>{l.range}</span>
        </div>
      ))}
      <div className="flex items-center gap-1.5" style={{ fontSize: 10.5 }}>
        <span style={{ width: 10, height: 10, background: MISSING_COLOR, borderRadius: 3, display: "inline-block", border: "1px solid #cbd5e1" }} />
        <span style={mutedText}>미산출</span>
      </div>
    </div>
  );
}

export function KpiCard({ label, value, sub, accent, icon: Icon }) {
  return (
    <div style={{ ...cardStyle, padding: "16px 18px", position: "relative", overflow: "hidden" }}>
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: accent }} />
      <div className="flex items-center justify-between">
        <span style={{ ...mutedText, fontSize: 12, fontWeight: 600, letterSpacing: 0.3 }}>{label}</span>
        <div style={{ width: 30, height: 30, borderRadius: 8, background: accent + "22", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Icon size={15} color={accent} />
        </div>
      </div>
      <div style={{ fontSize: 26, fontWeight: 700, marginTop: 10, fontVariantNumeric: "tabular-nums" }}>{value}</div>
      {sub && <div style={{ fontSize: 11, marginTop: 4, ...mutedText }}>{sub}</div>}
    </div>
  );
}
