"use client";
import {
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ScatterChart, Scatter, ZAxis, RadarChart, PolarGrid,
  PolarAngleAxis, PolarRadiusAxis, Radar, Legend, Cell,
} from "recharts";
import { Activity, AlertTriangle, Users, LayoutDashboard } from "lucide-react";
import { riskColor } from "@/lib/riskScale";
import { facilityCounts } from "@/lib/mockDetail";
import { cardStyle, mutedText, KpiCard } from "./shared";
import CorrelationPanel from "./CorrelationPanel";
import RiskHeatmapTable from "./RiskHeatmapTable";
import ContributionPanel from "./ContributionPanel";
import ModelFitPanel from "./ModelFitPanel";

function BubbleTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: 11.5, padding: "8px 10px", boxShadow: "0 4px 12px rgba(0,0,0,0.08)" }}>
      <b>{r.name}</b><br />
      응급실 {r.hospitalCount}개 · 의료진 {r.doctorCount}명<br />
      인구대비병상 부담 {r.popBed.toFixed(0)}점 · <span style={{ color: riskColor(r.risk) }}>위험도 {r.risk.toFixed(1)}점</span>
    </div>
  );
}

const tooltipStyle = { background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: 12, boxShadow: "0 4px 12px rgba(0,0,0,0.08)" };
const axisTick = { fill: "#64748b", fontSize: 10 };

function formatAsOf(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}.${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function average(rows, key) {
  return rows.reduce((s, r) => s + r[key], 0) / (rows.length || 1);
}

export default function AnalyticsTab({ data }) {
  const { ranked, kpi, clusterProfile, clusterIds, clusterMetaById, correlation, regression } = data;
  const bubbleData = ranked.map((r) => ({ ...r, ...facilityCounts(r.key, r.name, r.doc) }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
        <KpiCard label="평균 위험도" value={kpi.avg.toFixed(1)} sub={`전국 ${kpi.complete}개 산출 지역`} accent="#38bdf8" icon={Activity} />
        <KpiCard label="고위험 지역" value={kpi.high} sub="50점 이상" accent="#ef4444" icon={AlertTriangle} />
        <KpiCard label="위험도 산출 완료" value={`${kpi.complete} / ${kpi.total}`} sub={`${kpi.missing}개 지역 원천데이터부족`} accent="#22c55e" icon={Users} />
        <KpiCard label="기준 시각" value={formatAsOf(kpi.asOf)} sub="KST · 병상 API 최신 수집" accent="#a78bfa" icon={LayoutDashboard} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 14 }}>
        <RiskHeatmapTable ranked={ranked} />

        <div style={{ ...cardStyle, padding: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>클러스터 프로필 비교</div>
          <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 6 }}>K-Means (k=2) · {kpi.complete}개 지역</div>
          <ResponsiveContainer width="100%" height={230}>
            <RadarChart data={clusterProfile}>
              <PolarGrid stroke="#e2e8f0" />
              <PolarAngleAxis dataKey="subject" tick={axisTick} />
              <PolarRadiusAxis tick={false} axisLine={false} domain={[0, 100]} />
              {clusterIds.map((id) => {
                const meta = clusterMetaById[id];
                return (
                  <Radar key={id} name={`${meta.label} (${meta.count}개)`} dataKey={`c${id}`}
                    stroke={meta.color} fill={meta.color} fillOpacity={0.28} />
                );
              })}
              <Legend wrapperStyle={{ fontSize: 10.5 }} />
            </RadarChart>
          </ResponsiveContainer>
          <div className="flex justify-between" style={{ fontSize: 11, marginTop: 4, ...mutedText }}>
            {clusterIds.map((id) => {
              const rows = ranked.filter((r) => r.cluster === id);
              const avg = rows.reduce((s, r) => s + r.risk, 0) / (rows.length || 1);
              return (
                <span key={id}>평균 위험도: <b style={{ color: clusterMetaById[id].color }}>{avg.toFixed(2)}</b></span>
              );
            })}
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <CorrelationPanel correlation={correlation} />

        <div style={{ ...cardStyle, padding: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>지역별 의료 취약도 비교 (버블차트)</div>
          <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 8 }}>x=응급실 수 · y=의료진 수 · 크기=인구대비병상 부담 · 색상=위험도 · 전국 {bubbleData.length}개 지역</div>
          <ResponsiveContainer width="100%" height={230}>
            <ScatterChart margin={{ left: 0, right: 20, top: 10, bottom: 0 }}>
              <CartesianGrid stroke="#e2e8f0" />
              <XAxis type="number" dataKey="hospitalCount" name="응급실 수" tick={axisTick} />
              <YAxis type="number" dataKey="doctorCount" name="의료진 수" tick={axisTick} />
              <ZAxis type="number" dataKey="popBed" range={[40, 260]} name="인구대비병상 부담" />
              <Tooltip cursor={{ strokeDasharray: "3 3" }} content={<BubbleTooltip />} />
              <Scatter data={bubbleData} fillOpacity={0.7}>
                {bubbleData.map((r, i) => <Cell key={i} fill={riskColor(r.risk)} />)}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
          <div style={{ fontSize: 10, ...mutedText, marginTop: 4 }}>* 응급실 수·의료진 수는 hospital_master.csv 지역 집계 연동 전 추정치입니다.</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <ContributionPanel
          avgBed={average(ranked, "bed")}
          avgAccess={average(ranked, "access")}
          avgPopBed={average(ranked, "popBed")}
          avgDoc={average(ranked, "doc")}
          avgRisk={kpi.avg}
          regression={regression}
        />

        <ModelFitPanel avgRisk={kpi.avg} regression={regression} />
      </div>
    </div>
  );
}
