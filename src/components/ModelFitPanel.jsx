"use client";
import { useState } from "react";
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { ChevronDown } from "lucide-react";
import { cardStyle, mutedText } from "./shared";

const tooltipStyle = { background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: 12 };
const axisTick = { fill: "#64748b", fontSize: 10 };

export default function ModelFitPanel({ avgRisk, regression }) {
  const [expanded, setExpanded] = useState(false);
  const { r2, mae, rows, scatter } = regression;
  const accuracyPct = Math.round(r2 * 100);
  const errorPct = Math.round((mae / avgRisk) * 100);
  const bandLeft = Math.max(avgRisk - mae, 0);
  const bandRight = Math.min(avgRisk + mae, 100);
  const bandWidth = bandRight - bandLeft;

  return (
    <div style={{ ...cardStyle, padding: 16 }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>회귀모델 위험도 재현 정도</div>
      <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 14 }}>병상포화율·거리·병상비율·전문의부족비율 원천값만으로 위험도 산식을 재현해본 결과</div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
        <div style={{ textAlign: "center", padding: "14px 8px", background: "#f8fafc", borderRadius: 10 }}>
          <div style={{ fontSize: 30, fontWeight: 800, color: "#8b5cf6" }}>{accuracyPct}%</div>
          <div style={{ fontSize: 10.5, ...mutedText, marginTop: 4, lineHeight: 1.4 }}>재현율<br />실제 위험도 패턴과 일치하는 정도</div>
        </div>
        <div style={{ textAlign: "center", padding: "14px 8px", background: "#f8fafc", borderRadius: 10 }}>
          <div style={{ fontSize: 30, fontWeight: 800, color: "#f59e0b" }}>±{mae.toFixed(1)}점</div>
          <div style={{ fontSize: 10.5, ...mutedText, marginTop: 4, lineHeight: 1.4 }}>평균 오차<br />100점 만점 기준, 평균 위험도 대비 약 {errorPct}%</div>
        </div>
      </div>

      <div style={{ fontSize: 10.5, fontWeight: 600, marginBottom: 6 }}>평균 위험도 기준 예측 오차 범위</div>
      <div style={{ position: "relative", height: 26, background: "#f1f5f9", borderRadius: 6, marginBottom: 8, overflow: "hidden" }}>
        <div style={{ position: "absolute", left: `${bandLeft}%`, width: `${bandWidth}%`, top: 0, bottom: 0, background: "#8b5cf62a" }} />
        <div style={{ position: "absolute", left: `${avgRisk}%`, top: 0, bottom: 0, width: 2, background: "#8b5cf6" }} />
      </div>
      <div style={{ fontSize: 10.5, ...mutedText, lineHeight: 1.5 }}>
        평균 위험도 <b style={{ color: "#0f172a" }}>{avgRisk.toFixed(1)}점</b>을 예측하면, 실제로는 대략{" "}
        <b style={{ color: "#0f172a" }}>{bandLeft.toFixed(1)}~{bandRight.toFixed(1)}점</b> 사이일 가능성이 높아요.
      </div>

      <button onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5" style={{ marginTop: 16, paddingTop: 14, width: "100%",
          background: "none", border: "none", borderTop: "1px solid #e2e8f0", cursor: "pointer", fontSize: 11, fontWeight: 600, color: "#64748b" }}>
        <ChevronDown size={13} style={{ transform: expanded ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
        상세 산점도 (실제값 vs 예측값) {expanded ? "접기" : "펼치기"}
      </button>
      {expanded && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 8 }}>R² = {r2.toFixed(3)} · MAE = {mae.toFixed(2)} · {rows}개 지역</div>
          <ResponsiveContainer width="100%" height={200}>
            <ScatterChart margin={{ left: 0, right: 20, top: 10, bottom: 0 }}>
              <CartesianGrid stroke="#e2e8f0" />
              <XAxis type="number" dataKey="actual" name="실제값" domain={[0, 95]} tick={axisTick} />
              <YAxis type="number" dataKey="predicted" name="예측값" domain={[0, 95]} tick={axisTick} />
              <Tooltip contentStyle={tooltipStyle} formatter={(v) => v.toFixed(1)} />
              <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 95, y: 95 }]} stroke="#cbd5e1" strokeDasharray="4 4" />
              <Scatter data={scatter} fill="#38bdf8" fillOpacity={0.8} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
