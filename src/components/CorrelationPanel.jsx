"use client";
import { CORR_LEVELS } from "@/lib/correlationScale";
import { cardStyle, mutedText } from "./shared";

export default function CorrelationPanel({ correlation }) {
  return (
    <div style={{ ...cardStyle, padding: 16 }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>위험도에 영향을 주는 요인 순위</div>
      <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 14 }}>각 구성점수가 종합위험도와 얼마나 함께 움직이는지 (숫자가 아닌 강도 단계로 표시)</div>

      {/* x축 = 범례 */}
      <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", marginBottom: 4 }}>
        {CORR_LEVELS.map((l) => <div key={l.label} style={{ flex: 1, background: l.color }} />)}
      </div>
      <div style={{ display: "flex", fontSize: 9, ...mutedText, marginBottom: 18 }}>
        {CORR_LEVELS.map((l) => <div key={l.label} style={{ flex: 1, textAlign: "center" }}>{l.label}</div>)}
      </div>

      {correlation.map((item) => (
        <div key={item.name} style={{ marginBottom: 14 }}>
          <div className="flex items-center justify-between" style={{ marginBottom: 4 }}>
            <span style={{ fontSize: 12.5, fontWeight: 600 }}>{item.name}</span>
            <span style={{ fontSize: 12, fontWeight: 700, color: item.color }}>{item.level} · {item.pct}%대 일치</span>
          </div>
          <div style={{ background: "#e2e8f0", height: 10, borderRadius: 5, overflow: "hidden" }}>
            <div style={{ width: `${item.pct}%`, height: "100%", background: item.color, borderRadius: 5 }} />
          </div>
          <div style={{ fontSize: 11, ...mutedText, marginTop: 4, lineHeight: 1.45 }}>{item.insight}</div>
        </div>
      ))}
      <div style={{ fontSize: 10, ...mutedText, marginTop: 4 }}>
        * 위험도 산식 자체가 이 네 점수의 가중합이라, 강한 상관관계가 곧 "원인 입증"을 의미하지는 않습니다.
      </div>
    </div>
  );
}
