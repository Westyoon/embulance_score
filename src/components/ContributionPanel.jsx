"use client";
import { useState, Fragment } from "react";
import { ChevronDown } from "lucide-react";
import { cardStyle, mutedText } from "./shared";

// 원천값 회귀계수는 변수마다 단위(km, %, 비율)가 달라 막대 길이로 비교하면
// 오해를 준다 — 막대그래프 대신 산식 한 줄 + 표로만 값을 그대로 보여준다.
function formatFormulaCoef(v) {
  return Math.abs(v) < 0.01 ? v.toFixed(4) : v.toFixed(3);
}
function formatTableCoef(v) {
  return Math.abs(v) < 0.001 ? v.toFixed(6) : v.toFixed(4);
}
function shortLabel(name) {
  return name.replace(/\(.*?\)/, "");
}

// 위험도 산식의 가중치. 0~100으로 이미 정규화된 점수에 곱해지므로 단위 문제
// 없이 그대로 비교 가능하다 (원천값 회귀계수와 달리).
const WEIGHT_ITEMS = [
  { key: "bed", label: "병상포화도", weight: 0.35, color: "#f59e0b" },
  { key: "access", label: "접근성", weight: 0.30, color: "#8b5cf6" },
  { key: "popBed", label: "인구대비병상", weight: 0.20, color: "#14b8a6" },
  { key: "doc", label: "의료진부족", weight: 0.15, color: "#fb7185" },
];

export default function ContributionPanel({ avgBed, avgAccess, avgPopBed, avgDoc, avgRisk, regression, sampleCount, historical }) {
  const [expanded, setExpanded] = useState(false);
  const avgByKey = { bed: avgBed, access: avgAccess, popBed: avgPopBed, doc: avgDoc };
  const contributions = WEIGHT_ITEMS.map((w) => ({ ...w, points: w.weight * avgByKey[w.key] }));

  return (
    <div style={{ ...cardStyle, padding: 16 }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>위험도를 구성하는 요인별 영향력</div>
      <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 14 }}>{historical ? "최근 계산값" : "현재 유효 점수"} {sampleCount}개 지역 평균 · 원천 결측 지역 제외</div>

      <div style={{ fontSize: 10.5, fontWeight: 600, marginBottom: 6 }}>산식 가중치</div>
      <div style={{ display: "flex", height: 20, borderRadius: 6, overflow: "hidden", marginBottom: 6 }}>
        {WEIGHT_ITEMS.map((w) => (
          <div key={w.key} style={{ width: `${w.weight * 100}%`, background: w.color, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <span style={{ fontSize: 9.5, fontWeight: 700, color: "#ffffff" }}>{Math.round(w.weight * 100)}%</span>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap" style={{ gap: 10, marginBottom: 18 }}>
        {WEIGHT_ITEMS.map((w) => (
          <span key={w.key} className="flex items-center gap-1.5" style={{ fontSize: 10.5 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: w.color, display: "inline-block" }} />
            {w.label}
          </span>
        ))}
      </div>

      <div style={{ fontSize: 10.5, fontWeight: 600, marginBottom: 6 }}>
        평균 지역(위험도 {avgRisk.toFixed(1)}점) 기준 실제 기여 점수
      </div>
      <div style={{ display: "flex", height: 20, borderRadius: 6, overflow: "hidden", background: "#f1f5f9", width: `${Math.min(avgRisk, 100)}%`, minWidth: 60 }}>
        {contributions.map((c) => (
          <div key={c.key} style={{ width: `${(c.points / avgRisk) * 100}%`, background: c.color }} title={`${c.label}: ${c.points.toFixed(1)}점`} />
        ))}
      </div>
      <div className="flex flex-wrap" style={{ gap: 10, marginTop: 8 }}>
        {contributions.map((c) => (
          <span key={c.key} style={{ fontSize: 10.5 }}>
            <b style={{ color: c.color }}>{c.label}</b> <span style={mutedText}>{c.points.toFixed(1)}점</span>
          </span>
        ))}
      </div>
      <div style={{ fontSize: 10, ...mutedText, marginTop: 8 }}>
        네 조각을 더하면 평균 위험도 {avgRisk.toFixed(1)}점이 정확히 나옵니다 — 왜곡 없이 직접 비교할 수 있어요.
      </div>

      <button onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5" style={{ marginTop: 16, paddingTop: 14, width: "100%",
          background: "none", border: "none", borderTop: "1px solid #e2e8f0", cursor: "pointer", fontSize: 11, fontWeight: 600, color: "#64748b" }}>
        <ChevronDown size={13} style={{ transform: expanded ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
        전문가용 원천값 회귀계수 {expanded ? "접기" : "펼치기"}
      </button>
      {expanded && (
        <div style={{ marginTop: 12 }}>
          {historical && regression.coef.length > 0 && (
            <div style={{ fontSize: 11, color: "#92400e", marginBottom: 8 }}>아래 회귀계수는 최근 계산값 기준이며 현재 실시간 값이 아닙니다.</div>
          )}
          {regression.coef.length === 0 ? (
            <div style={{ fontSize: 11, ...mutedText }}>회귀계수 산출 데이터가 아직 없습니다.</div>
          ) : (
            <>
              <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 8 }}>종속변수 regionRisk · {regression.rows}개 지역 · R² = {regression.r2?.toFixed(3)} · MAE = {regression.mae?.toFixed(2)}</div>
              <div style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 11, background: "#f8fafc", border: "1px solid #e2e8f0",
                borderRadius: 8, padding: "10px 12px", lineHeight: 1.7, color: "#334155", overflowX: "auto", whiteSpace: "nowrap" }}>
                regionRisk ≈ {regression.coef.map((c) => `${formatFormulaCoef(c.value)}×${shortLabel(c.name)}`).join(" + ")}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 90px", gap: 0, marginTop: 10, fontSize: 11.5, border: "1px solid #e2e8f0", borderRadius: 8, overflow: "hidden" }}>
                <div style={{ fontWeight: 700, padding: "6px 10px", background: "#f1f5f9", color: "#475569" }}>변수</div>
                <div style={{ fontWeight: 700, padding: "6px 10px", background: "#f1f5f9", color: "#475569", textAlign: "right" }}>계수</div>
                {regression.coef.map((c) => (
                  <Fragment key={c.name}>
                    <div style={{ padding: "7px 10px", borderTop: "1px solid #e2e8f0" }}>{c.name}</div>
                    <div style={{ padding: "7px 10px", borderTop: "1px solid #e2e8f0", textAlign: "right", fontWeight: 700, color: "#0f172a" }}>{formatTableCoef(c.value)}</div>
                  </Fragment>
                ))}
              </div>
              <div style={{ fontSize: 10, ...mutedText, marginTop: 8 }}>
                * 변수마다 단위가 달라(km, %, 비율) 계수 크기를 직접 비교하면 안 됩니다. 위쪽 &quot;산식 가중치&quot;가 실제로 비교 가능한 값이에요.
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
