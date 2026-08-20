"use client";
import { riskColor, riskTextColor } from "@/lib/riskScale";
import { cardStyle, mutedText, RiskLegendStrip } from "./shared";

const COLS = [
  { key: "bed", label: "병상포화도" },
  { key: "access", label: "접근성" },
  { key: "popBed", label: "인구대비병상" },
  { key: "doc", label: "의료진부족" },
];

export default function RiskHeatmapTable({ ranked }) {
  return (
    <div style={{ ...cardStyle, padding: 16 }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>지역별 위험도 히트맵</div>
      <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 12 }}>구성점수 4개를 색으로 표시 — 어떤 요인이 위험을 키웠는지 지역을 하나씩 열지 않아도 한눈에 비교</div>

      <div style={{ display: "grid", gridTemplateColumns: `72px repeat(${COLS.length}, 1fr) 56px`, gap: 4, fontSize: 9.5, ...mutedText,
        padding: "0 2px 6px", borderBottom: "1px solid #e2e8f0", position: "sticky", top: 0, background: "#ffffff", zIndex: 1 }}>
        <span>지역</span>
        {COLS.map((c) => <span key={c.key} style={{ textAlign: "center" }}>{c.label}</span>)}
        <span style={{ textAlign: "right" }}>종합위험도</span>
      </div>

      <div style={{ maxHeight: 280, overflowY: "auto" }}>
        {ranked.map((r) => (
          <div key={r.key} style={{ display: "grid", gridTemplateColumns: `72px repeat(${COLS.length}, 1fr) 56px`, gap: 4, alignItems: "center", padding: "4px 2px" }}>
            <span style={{ fontSize: 11.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
            {COLS.map((c) => (
              <span key={c.key} style={{ fontSize: 10.5, fontWeight: 700, textAlign: "center", borderRadius: 5, padding: "3px 0",
                color: riskTextColor(r[c.key]), background: riskColor(r[c.key]) + "30" }}>
                {Math.round(r[c.key])}
              </span>
            ))}
            <span style={{ fontSize: 12, fontWeight: 800, textAlign: "right", color: riskTextColor(r.risk) }}>{r.risk.toFixed(1)}</span>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid #e2e8f0" }}>
        <RiskLegendStrip compact />
      </div>
    </div>
  );
}
