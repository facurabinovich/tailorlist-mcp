"""Shared constants."""

# Features used for KMeans clustering (speechiness is log-transformed first).
CLUSTER_FEATURES = ["energy", "acousticness", "valence", "tempo", "danceability", "speechiness_log"]

AUDIO_FEATURES = [
    "energy", "acousticness", "valence", "danceability",
    "speechiness", "instrumentalness", "liveness", "loudness", "tempo",
]

# Features on a 0-1 scale (vs loudness in dB, tempo in BPM).
NORMALIZED_FEATURES = [
    "energy", "acousticness", "valence", "danceability",
    "speechiness", "instrumentalness", "liveness",
]
