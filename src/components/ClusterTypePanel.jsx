"use client";
import { useState } from "react";
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { Search, X } from "lucide-react";
import { cardStyle, mutedText } from "./shared";

const axisTick = { fill: "#64748b", fontSize: 9.5 };

function ClusterScatterTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: 11, padding: "7px 10px", boxShadow: "0 4px 12px rgba(0,0,0,0.08)" }}>
      <b>{r.name}</b><br />접근성 {r.access.toFixed(0)}점 · 의료진부족 {r.doc.toFixed(0)}점
    </div>
  );
}

// 레이더차트(4축 전부)는 축이 많아 어떤 요인이 진짜 유형을 가르는지 한눈에
// 안 들어와서, 상위 2개 요인만 문장으로 요약하는 카드 + 실제 분포 산점도로
// 교체했다. clusterProfile은 {subject, c0, c1, ...}[] 형태라 유형(cN) 키로
// 정렬해 상위 2개를 뽑는다.
function topFactors(clusterProfile, id) {
  return [...clusterProfile].sort((a, b) => b[`c${id}`] - a[`c${id}`]).slice(0, 2);
}

export default function ClusterTypePanel({ ranked, clusterProfile, clusterIds, clusterMetaById }) {
  const clustered = ranked.filter((r) => clusterMetaById[r.cluster]);
  const [query, setQuery] = useState("");
  const q = query.trim();
  const hasMatch = q ? clustered.some((r) => r.name.includes(q)) : true;

  return (
    <div style={{ ...cardStyle, padding: 16 }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>지역 유형 비교</div>
      <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 12 }}>K-Means (k={clusterIds.length}) · {ranked.length}개 지역 — 지도 팝업과 동일한 유형 기준</div>

      <div style={{ display: "grid", gridTemplateColumns: `repeat(${clusterIds.length}, 1fr)`, gap: 10, marginBottom: 14 }}>
        {clusterIds.map((id) => {
          const meta = clusterMetaById[id];
          const rows = ranked.filter((r) => r.cluster === id);
          const avgRisk = rows.reduce((s, r) => s + r.risk, 0) / (rows.length || 1);
          const [f1, f2] = topFactors(clusterProfile, id);
          return (
            <div key={id} style={{ background: meta.color + "12", border: `1px solid ${meta.color}33`, borderRadius: 10, padding: 12 }}>
              <span style={{ fontSize: 10.5, fontWeight: 700, color: meta.color, background: meta.color + "22", padding: "3px 8px", borderRadius: 999 }}>
                {meta.label}
              </span>
              <div style={{ fontSize: 10.5, ...mutedText, marginTop: 8 }}>{meta.count}개 지역 · 평균 위험도</div>
              <div style={{ fontSize: 22, fontWeight: 800, color: meta.color }}>{avgRisk.toFixed(2)}</div>
              <div style={{ fontSize: 10, marginTop: 6, lineHeight: 1.5, color: "#475569" }}>
                {f1.subject} {f1[`c${id}`].toFixed(0)}점 · {f2.subject} {f2[`c${id}`].toFixed(0)}점이 주요 요인
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ fontSize: 10.5, fontWeight: 600, marginBottom: 4 }}>지역별 실제 분포</div>
      <div className="flex items-center" style={{ position: "relative", marginBottom: 10 }}>
        <Search size={13} color="#94a3b8" style={{ position: "absolute", left: 9 }} />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="지역명 검색 (예: 안동시)"
          style={{ width: "100%", fontSize: 12.5, padding: "8px 10px 8px 28px", borderRadius: 8, border: "1px solid #e2e8f0", outline: "none", color: "#0f172a", background: "#f8fafc" }} />
        {query && (
          <button onClick={() => setQuery("")} style={{ position: "absolute", right: 8, background: "none", border: "none", cursor: "pointer", color: "#94a3b8" }}>
            <X size={13} />
          </button>
        )}
      </div>
      {q && (
        <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 6 }}>
          {hasMatch ? `"${q}" 포함 지역을 진하게 강조 표시했어요` : "일치하는 지역이 없습니다"}
        </div>
      )}
      <ResponsiveContainer width="100%" height={150}>
        <ScatterChart margin={{ left: 0, right: 10, top: 4, bottom: 0 }}>
          <CartesianGrid stroke="#e2e8f0" />
          <XAxis type="number" dataKey="access" name="접근성" domain={[0, 100]} tick={axisTick} />
          <YAxis type="number" dataKey="doc" name="의료진부족" domain={[0, 100]} tick={axisTick} />
          <Tooltip cursor={{ strokeDasharray: "3 3" }} content={<ClusterScatterTooltip />} />
          <Scatter data={clustered}>
            {clustered.map((r, i) => {
              const isMatch = q && r.name.includes(q);
              const dim = q && !isMatch;
              return (
                <Cell key={i} fill={clusterMetaById[r.cluster].color} fillOpacity={dim ? 0.12 : isMatch ? 1 : 0.65}
                  stroke={isMatch ? "#0f172a" : "none"} strokeWidth={isMatch ? 2 : 0} />
              );
            })}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
      <div style={{ fontSize: 9.5, ...mutedText, marginTop: 2 }}>x=접근성점수 · y=의료진부족점수 — 위에서 본 두 유형 카드와 동일 색상</div>
    </div>
  );
}
