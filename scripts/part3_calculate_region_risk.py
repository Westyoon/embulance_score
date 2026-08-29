import numpy as np
import pandas as pd

from common import DATA_DIR, read_csv, save_csv

RISK_BINS = [-np.inf, 20, 35, 50, 65, np.inf]
RISK_GRADES = [1, 2, 3, 4, 5]
RISK_GRADE_NAMES = ["매우낮음", "낮음", "보통", "높음", "매우높음"]


def classify_risk(values: pd.Series, labels: list) -> pd.Series:
    return pd.cut(values, RISK_BINS, labels=labels, right=True, include_lowest=True)


def main() -> None:
    bed = read_csv(DATA_DIR / "bed_status.csv")
    bed["시군구코드"] = bed["시도"].fillna("").str.strip() + "|" + bed["시군구"].fillna("").str.strip()
    bed_component = bed.groupby("시군구코드", as_index=False).agg(
        시군구명=("시군구", "first"),
        병상포화도점수=("포화율", "mean"),
        병상데이터기관수=("포화율", "count"),
    )

    final = bed_component.merge(
        read_csv(DATA_DIR / "accessibility_score.csv")[["시군구코드", "접근성점수"]],
        on="시군구코드", how="outer",
    )
    optional = [
        (DATA_DIR / "population_bed_score.csv", "인구대비병상점수"),
        (DATA_DIR / "doctor_score.csv", "의료진부족점수"),
    ]
    for path, column in optional:
        if path.exists():
            source = read_csv(path)
            # 이전 placeholder의 일반적인 단일 50점 파일은 실제 자료로 인정하지 않는다.
            if column in source and not (source[column].nunique(dropna=True) == 1 and source[column].dropna().eq(50).all()):
                final = final.merge(source[["시군구코드", column]], on="시군구코드", how="left")
            else:
                final[column] = np.nan
        else:
            final[column] = np.nan

    score_cols = ["병상포화도점수", "접근성점수", "인구대비병상점수", "의료진부족점수"]
    final["완성항목수"] = final[score_cols].notna().sum(axis=1)
    complete = final[score_cols].notna().all(axis=1)
    final["regionRisk"] = np.nan
    final.loc[complete, "regionRisk"] = (
        0.35 * final.loc[complete, "병상포화도점수"]
        + 0.30 * final.loc[complete, "접근성점수"]
        + 0.20 * final.loc[complete, "인구대비병상점수"]
        + 0.15 * final.loc[complete, "의료진부족점수"]
    )
    # 프론트와 동일: <=20, <=35, <=50, <=65, >65.
    final["위험등급"] = classify_risk(final["regionRisk"], RISK_GRADES)
    final["위험등급명"] = classify_risk(final["regionRisk"], RISK_GRADE_NAMES)
    final["산출상태"] = np.where(complete, "완료", "원천데이터부족")
    save_csv(final.sort_values("시군구코드"), DATA_DIR / "region_risk_final.csv")
    print(f"Saved {len(final):,} regions; complete={int(complete.sum()):,}")


if __name__ == "__main__":
    main()

