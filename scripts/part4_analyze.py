import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

import numpy as np
import pandas as pd

from common import DATA_DIR, read_csv, save_csv, save_json

HISTORY = DATA_DIR / "bed_status_history.csv"
FINAL = DATA_DIR / "region_risk_final.csv"


def build_heatmap() -> None:
    history = read_csv(HISTORY)
    history["수집시각"] = pd.to_datetime(history["수집시각"], errors="coerce")
    history["포화율"] = pd.to_numeric(history["포화율"], errors="coerce")
    history = history.dropna(subset=["수집시각", "포화율"])
    day_names = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
    history["요일번호"] = history["수집시각"].dt.dayofweek
    history["요일"] = history["요일번호"].map(day_names)
    history["시간"] = history["수집시각"].dt.hour
    matrix = history.pivot_table(index=["요일번호", "요일"], columns="시간", values="포화율", aggfunc="mean")
    matrix = matrix.reindex(columns=range(24)).reset_index().sort_values("요일번호").drop(columns="요일번호")
    save_csv(matrix, DATA_DIR / "heatmap_matrix.csv")


def build_regression() -> None:
    frame = read_csv(FINAL)
    frame = frame.merge(
        read_csv(DATA_DIR / "accessibility_score.csv")[["시군구코드", "직선거리_km"]],
        on="시군구코드", how="left",
    ).merge(
        read_csv(DATA_DIR / "population_bed_score.csv")[["시군구코드", "인구대비병상비율"]],
        on="시군구코드", how="left",
    )
    frame = frame.rename(columns={"병상포화도점수": "포화율_원천"})
    features = ["포화율_원천", "직선거리_km", "인구대비병상비율", "의료진부족점수"]
    model_data = frame.dropna(subset=features + ["regionRisk"])
    output = DATA_DIR / "regression_result.csv"
    metrics_path = DATA_DIR / "regression_metrics.json"
    if len(model_data) < 10:
        save_csv(pd.DataFrame(columns=["변수명", "회귀계수"]), output)
        save_json({"status": "insufficient_data", "rows": len(model_data)}, metrics_path)
        print(f"Regression skipped: only {len(model_data)} complete regions")
        return

    from sklearn.linear_model import LinearRegression
    from sklearn.metrics import mean_absolute_error, r2_score
    from sklearn.model_selection import train_test_split

    train, test = train_test_split(model_data, test_size=0.2, random_state=42)
    model = LinearRegression().fit(train[features], train["regionRisk"])
    predicted = model.predict(test[features])
    coefficients = pd.DataFrame({"변수명": features, "회귀계수": model.coef_})
    save_csv(coefficients, output)
    metrics = {
        "status": "complete",
        "rows": len(model_data),
        "r2": float(r2_score(test["regionRisk"], predicted)),
        "mae": float(mean_absolute_error(test["regionRisk"], predicted)),
        "intercept": float(model.intercept_),
    }
    save_json(metrics, metrics_path)


def build_correlation_and_vif() -> None:
    frame = read_csv(FINAL)
    features = ["병상포화도점수", "접근성점수", "인구대비병상점수", "의료진부족점수"]
    data = frame[features + ["regionRisk"]].dropna()
    save_csv(data.corr().reset_index(names="변수명"), DATA_DIR / "correlation_matrix.csv")
    rows = []
    for target in features:
        others = [x for x in features if x != target]
        if len(data) < 10:
            vif = np.nan
        else:
            from sklearn.linear_model import LinearRegression
            r2 = LinearRegression().fit(data[others], data[target]).score(data[others], data[target])
            vif = np.inf if r2 >= 1 else 1 / (1 - r2)
        rows.append({"변수명": target, "VIF": vif})
    save_csv(pd.DataFrame(rows), DATA_DIR / "vif_result.csv")


def build_clusters() -> None:
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    frame = read_csv(FINAL)
    features = ["병상포화도점수", "접근성점수", "인구대비병상점수", "의료진부족점수"]
    data = frame.dropna(subset=features).copy()
    evaluation_columns = ["k", "실루엣점수"]
    result_columns = ["시군구코드", "시군구명", *features, "regionRisk", "클러스터"]
    profile_columns = ["클러스터", *features, "regionRisk", "지역수"]
    if len(data) < 3:
        save_csv(pd.DataFrame(columns=evaluation_columns), DATA_DIR / "cluster_k_evaluation.csv")
        save_csv(pd.DataFrame(columns=result_columns), DATA_DIR / "cluster_result.csv")
        save_csv(pd.DataFrame(columns=profile_columns), DATA_DIR / "cluster_profile.csv")
        print(f"Clustering skipped: only {len(data)} complete regions")
        return
    scaled = StandardScaler().fit_transform(data[features])
    unique_vectors = len(np.unique(scaled, axis=0))
    max_k = min(8, len(data) - 1, unique_vectors)
    if max_k < 2:
        save_csv(pd.DataFrame(columns=evaluation_columns), DATA_DIR / "cluster_k_evaluation.csv")
        save_csv(pd.DataFrame(columns=result_columns), DATA_DIR / "cluster_result.csv")
        save_csv(pd.DataFrame(columns=profile_columns), DATA_DIR / "cluster_profile.csv")
        print(f"Clustering skipped: only {unique_vectors} unique feature vector(s)")
        return
    evaluations = []
    for k in range(2, max_k + 1):
        labels = KMeans(n_clusters=k, random_state=42, n_init=20).fit_predict(scaled)
        if len(np.unique(labels)) >= 2:
            evaluations.append({"k": k, "실루엣점수": silhouette_score(scaled, labels)})
    evaluation = pd.DataFrame(evaluations, columns=evaluation_columns)
    save_csv(evaluation, DATA_DIR / "cluster_k_evaluation.csv")
    if evaluation.empty:
        save_csv(pd.DataFrame(columns=result_columns), DATA_DIR / "cluster_result.csv")
        save_csv(pd.DataFrame(columns=profile_columns), DATA_DIR / "cluster_profile.csv")
        return
    best_k = int(evaluation.loc[evaluation["실루엣점수"].idxmax(), "k"])
    data["클러스터"] = KMeans(n_clusters=best_k, random_state=42, n_init=20).fit_predict(scaled)
    save_csv(data[["시군구코드", "시군구명", *features, "regionRisk", "클러스터"]], DATA_DIR / "cluster_result.csv")
    profile = data.groupby("클러스터", as_index=False)[features + ["regionRisk"]].mean()
    profile["지역수"] = data.groupby("클러스터").size().to_numpy()
    save_csv(profile, DATA_DIR / "cluster_profile.csv")


def main() -> None:
    print("Building heatmap...", flush=True)
    build_heatmap()
    print("Building correlation and VIF...", flush=True)
    build_correlation_and_vif()
    print("Building regression...", flush=True)
    build_regression()
    print("Building clusters...", flush=True)
    build_clusters()
    print("Saved PART 4 outputs")


if __name__ == "__main__":
    main()
