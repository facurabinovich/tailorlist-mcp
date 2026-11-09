"""KMeans clustering of tracks by audio features: pure functions, no I/O, no framework.

Features used (``config.CLUSTER_FEATURES``): energy, acousticness, valence, tempo,
danceability and speechiness_log (log1p of speechiness; derived here when only the raw
``speechiness`` column is present). Features are standardised (StandardScaler) before
KMeans (random_state=42, n_init=10), and a 2-component PCA is computed for plotting.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from tailorlist_mcp.config import CLUSTER_FEATURES


class ClusterNames(NamedTuple):
    """Result of naming clusters.

    name_map: {cluster index -> display name}. For k <= 5 these are descriptive names
        such as "Euphoric Bangers"; for k > 5 they are "Playlist 1".."Playlist k".
    descriptions: {cluster index -> descriptive name}; only filled when k > 5 (where
        name_map holds numbered names), otherwise empty.
    """

    name_map: dict
    descriptions: dict


class KMeansResult(NamedTuple):
    """Result of kmeans_cluster.

    df_clust: input tracks plus columns cluster (int), cluster_name, PC1, PC2.
    name_map: {cluster index -> display name}.
    centroids_scaled: DataFrame (k x CLUSTER_FEATURES) of centroids in StandardScaler space.
    var1, var2: percent of variance explained by PCA components 1 and 2.
    scaler: the fitted StandardScaler.
    label1, label2: the feature that loads most strongly on PC1 / PC2.
    descriptions: descriptive cluster names when k > 5, else empty (see ClusterNames).
    """

    df_clust: pd.DataFrame
    name_map: dict
    centroids_scaled: pd.DataFrame
    var1: float
    var2: float
    scaler: StandardScaler
    label1: str
    label2: str
    descriptions: dict


def make_cluster_names(centroids_raw: pd.DataFrame, k: int) -> ClusterNames:
    """Name k clusters from their centroids (in original feature units, not scaled).

    centroids_raw has one row per cluster (index 0..k-1) and CLUSTER_FEATURES columns.
    Energy tier (high > 0.65, low < 0.45, else mid) is the fixed primary axis; the most
    z-score-distinctive non-energy feature picks the differentiator (e.g. "Chill & Happy",
    "Hip-Hop Hype"). Name collisions are extended with a second differentiator or the
    energy value. k <= 5 returns descriptive names; k > 5 returns "Playlist n" names and
    puts the descriptive ones in ``descriptions``.
    """
    global_mean = centroids_raw.mean()
    global_std  = centroids_raw.std().replace(0, 1)
    z_scores    = (centroids_raw - global_mean) / global_std

    def _energy_tier(val: float) -> str:
        if val > 0.65: return "high"
        if val < 0.45: return "low"
        return "mid"

    # Human-readable names keyed by (energy_tier, differentiator)
    _VOCAB: dict[tuple, str] = {
        ("high", "upbeat"):   "Euphoric Bangers",
        ("high", "dark"):     "Intense Drive",
        ("high", "acoustic"): "Energetic Acoustic",
        ("high", "groovy"):   "Club Anthems",
        ("high", "hip-hop"):  "Hip-Hop Hype",
        ("high", "fast"):     "Full Throttle",
        ("high", "subdued"):  "Raw Energy",
        ("high", None):       "High Energy",
        ("mid",  "upbeat"):   "Feel-Good Vibes",
        ("mid",  "dark"):     "Moody Tunes",
        ("mid",  "acoustic"): "Acoustic Sessions",
        ("mid",  "groovy"):   "Groove Mix",
        ("mid",  "hip-hop"):  "Hip-Hop Flow",
        ("mid",  "fast"):     "Mid-Tempo Rush",
        ("mid",  "subdued"):  "Understated Cool",
        ("mid",  None):       "Everyday Mix",
        ("low",  "upbeat"):   "Chill & Happy",
        ("low",  "dark"):     "Melancholic",
        ("low",  "acoustic"): "Acoustic Ballads",
        ("low",  "groovy"):   "Smooth Grooves",
        ("low",  "hip-hop"):  "Lo-Fi Hip-Hop",
        ("low",  "fast"):     "Slow Burn",
        ("low",  "subdued"):  "Introspective",
        ("low",  None):       "Mellow",
    }

    def _differentiators(i: int) -> list[str]:
        """Ordered list of differentiator keys for cluster i by |z-score| (energy excluded)."""
        row    = centroids_raw.iloc[i]
        feat_z = z_scores.iloc[i].drop("energy", errors="ignore").abs().sort_values(ascending=False)
        out: list[str] = []
        for feat in feat_z.index:
            val = float(row[feat])
            key: str | None = None
            if   feat == "valence":
                if val > 0.6:   key = "upbeat"
                elif val < 0.4: key = "dark"
            elif feat == "acousticness":
                if val > 0.5:   key = "acoustic"
            elif feat == "danceability":
                if val > 0.65:  key = "groovy"
                elif val < 0.4: key = "subdued"
            elif feat == "speechiness_log":
                if val > 0.25:  key = "hip-hop"
            elif feat == "tempo":
                if val > 130:   key = "fast"
                elif val < 90:  key = "slow"
            if key and key not in out:
                out.append(key)
        return out

    tiers = [_energy_tier(float(centroids_raw.iloc[i]["energy"])) for i in range(k)]
    diffs = [_differentiators(i) for i in range(k)]
    names = [
        _VOCAB.get((tiers[i], diffs[i][0] if diffs[i] else None),
                   _VOCAB[(tiers[i], None)])
        for i in range(k)
    ]

    # Pass 1: collision → extend with next available differentiator
    for i in range(k):
        if names.count(names[i]) > 1:
            for extra in diffs[i][1:]:
                candidate = f"{names[i]} · {extra.title()}"
                if names.count(candidate) == 0:
                    names[i] = candidate
                    break

    # Pass 2: still colliding → append energy value as numeric tiebreaker
    for i in range(k):
        if names.count(names[i]) > 1:
            names[i] = f"{names[i]} ({float(centroids_raw.iloc[i]['energy']):.2f})"

    if k <= 5:
        return ClusterNames({i: names[i] for i in range(k)}, {})
    else:
        return ClusterNames(
            {i: f"Playlist {i + 1}" for i in range(k)},
            {i: names[i] for i in range(k)},
        )


def verbose_description(energy: float, valence: float, danceability: float,
                        acousticness: float = 0, tempo: float = 0) -> str:
    """Turn audio-feature values into one human-readable sentence.

    Inputs are Spotify-style values: energy, valence, danceability and acousticness in
    0..1, tempo in BPM. Example output: "Songs that are high energy, electric, happy and
    uplifting and very danceable." Thresholds: energy > 0.65 high / < 0.45 low;
    acousticness > 0.5 acoustic; valence > 0.6 happy / < 0.4 dark; danceability > 0.65
    very danceable / < 0.4 not particularly danceable; tempo > 130 fast / < 90 slow.
    """
    parts = []

    # Energy
    if energy > 0.65:
        parts.append("high energy")
    elif energy < 0.45:
        parts.append("low energy")
    else:
        parts.append("mid energy")

    # Acousticness
    if acousticness > 0.5:
        parts.append("acoustic")
    else:
        parts.append("electric")

    # Valence
    if valence > 0.6:
        parts.append("happy and uplifting")
    elif valence < 0.4:
        parts.append("dark and melancholic")
    else:
        parts.append("emotionally neutral")

    # Danceability
    if danceability > 0.65:
        parts.append("very danceable")
    elif danceability < 0.4:
        parts.append("not particularly danceable")

    # Tempo
    if tempo > 130:
        parts.append("fast tempo")
    elif tempo < 90:
        parts.append("slow tempo")

    if not parts:
        return "Mixed audio profile."

    # Build sentence
    return "Songs that are " + ", ".join(parts[:-1]) + (
        f" and {parts[-1]}." if len(parts) > 1 else f"{parts[0]}."
    )


def kmeans_cluster(df: pd.DataFrame, k: int) -> KMeansResult:
    """Cluster tracks into k groups by audio similarity (KMeans on standardised features).

    df: one row per track to cluster, with CLUSTER_FEATURES columns (energy, acousticness,
    valence, tempo, danceability, speechiness_log; speechiness_log is derived with log1p
    from a ``speechiness`` column when absent). Rows must have no NaN in those columns.
    k: number of clusters (2 or more, at most the number of rows). Results are
    deterministic (random_state=42). Returns a KMeansResult: the tracks with cluster,
    cluster_name, PC1, PC2 columns, the {index -> name} map, scaled centroids, PCA
    variance percentages, the fitted scaler and the PC axis labels. The input frame is
    not modified.
    """
    df_sub = df.copy()
    if "speechiness" in df_sub.columns and "speechiness_log" not in df_sub.columns:
        df_sub["speechiness_log"] = np.log1p(df_sub["speechiness"])
    X        = df_sub[CLUSTER_FEATURES].values
    scaler   = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    km                = KMeans(n_clusters=k, random_state=42, n_init=10)
    df_sub["cluster"] = km.fit_predict(X_scaled)

    # PCA for visualization (always 2 components)
    pca        = PCA(n_components=2, random_state=42)
    coords     = pca.fit_transform(X_scaled)
    # Flip axes so the dominant feature always loads positively — makes labels intuitive
    if pca.components_[0][np.abs(pca.components_[0]).argmax()] < 0:
        coords[:, 0] *= -1
    if pca.components_[1][np.abs(pca.components_[1]).argmax()] < 0:
        coords[:, 1] *= -1
    df_sub["PC1"] = coords[:, 0]
    df_sub["PC2"] = coords[:, 1]
    var1 = float(pca.explained_variance_ratio_[0] * 100)
    var2 = float(pca.explained_variance_ratio_[1] * 100)
    label1 = CLUSTER_FEATURES[int(np.abs(pca.components_[0]).argmax())]
    label2 = CLUSTER_FEATURES[int(np.abs(pca.components_[1]).argmax())]

    # Inverse-transform ALL features back to original space for accurate naming
    centroids_raw = pd.DataFrame(
        scaler.inverse_transform(km.cluster_centers_),
        columns=CLUSTER_FEATURES,
    )

    names = make_cluster_names(centroids_raw, k)
    name_map = names.name_map
    df_sub["cluster_name"] = df_sub["cluster"].map(name_map)

    # Centroids in StandardScaler space (for radar — radar features only)
    centroids_scaled = pd.DataFrame(km.cluster_centers_, columns=CLUSTER_FEATURES)

    return KMeansResult(df_sub, name_map, centroids_scaled, var1, var2, scaler,
                        label1, label2, names.descriptions)


def k_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Score candidate cluster counts k = 2..10 for the given tracks.

    df: tracks with CLUSTER_FEATURES columns (speechiness_log derived from speechiness
    when absent). Returns one row per k (k runs from 2 to min(10, len(df) - 1)) with
    columns k, inertia (lower = tighter; use for the elbow) and silhouette (higher =
    better separated). Pass the result to best_k.
    """
    df_sub = df.copy()
    if "speechiness" in df_sub.columns and "speechiness_log" not in df_sub.columns:
        df_sub["speechiness_log"] = np.log1p(df_sub["speechiness"])
    X = df_sub[CLUSTER_FEATURES].values
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    rows = []
    k_range = range(2, min(11, len(df_sub)))
    for k_i in k_range:
        km_i = KMeans(n_clusters=k_i, random_state=42, n_init=10)
        labels = km_i.fit_predict(X_scaled)
        sil = silhouette_score(X_scaled, labels) if k_i > 1 else 0.0
        rows.append({"k": k_i, "inertia": km_i.inertia_, "silhouette": sil})
    return pd.DataFrame(rows)


def best_k(metrics_df: pd.DataFrame) -> tuple[int, int]:
    """Pick the best cluster count from a k_metrics table.

    metrics_df: output of k_metrics (columns k, inertia, silhouette). Returns
    (best_k_silhouette, best_k_elbow): the k with the highest silhouette, and the k at the
    largest second derivative of inertia (needs at least 4 rows, otherwise it falls back
    to the silhouette choice).
    """
    best_sil = int(metrics_df.loc[metrics_df["silhouette"].idxmax(), "k"])

    # Elbow: largest second derivative of inertia
    # Requires at least 4 points (k=2..5) to compute second derivative
    inertias = metrics_df["inertia"].values
    if len(inertias) >= 4:
        deltas = np.diff(inertias)
        second_deltas = np.diff(deltas)
        # second_deltas[j] is centred on row j + 1
        best_elbow = int(metrics_df["k"].iloc[second_deltas.argmax() + 1])
    else:
        best_elbow = best_sil  # fall back to silhouette when not enough points

    return best_sil, best_elbow
