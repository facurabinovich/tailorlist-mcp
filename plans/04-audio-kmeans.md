# 04. Audio: KMeans mode in `group_tracks`, and scikit-learn as an extra

Phase 4. Needs 02 and 03. Can run in parallel with 05 and 06 (this plan owns `drafts.py`,
`ui/draft.html`, `pyproject.toml` and the `group_tracks` function in `server.py`).

Original code it follows: `pages/6_My_Clusters.py` (the "By audio similarity (KMeans)" mode: K
selection Auto or Manual, manual slider 2 to 10 default 4, Auto = silhouette), lines 1240-1280
(run: `compute_k_metrics`, `best_k`, `run_kmeans`), `app_state/clusters.py`,
`pages/_Cluster_Results.py` (the callout "Silhouette suggests k=…, Elbow suggests k=…", the two
metric charts, the PCA scatter, the centroid radar), and `analysis/clustering.py` (ported:
`kmeans_cluster`, `k_metrics`, `best_k`, `make_cluster_names`, `verbose_description`).

## Confirm with Facu first

1. Group names are always the descriptive ones ("Euphoric Bangers", "Melancholic"). The original
   switched to "Playlist 1..k" when k > 5 and kept the descriptive name as a subtitle; here Claude
   proposes the final playlist names anyway, and numbered groups say nothing.
2. k stays 2 to 10, like the original.

## Design

### scikit-learn becomes optional (`pyproject.toml`)

- `dependencies`: remove `scikit-learn`. `[project.optional-dependencies]`:
  `audio = ["scikit-learn>=1.3"]`, `dev = ["pytest>=8", "scikit-learn>=1.3"]` (CI installs `.[dev]`,
  so the KMeans tests run there).
- Nothing may import `analysis/clustering.py` at server start. Import it inside the function that
  runs KMeans; on `ImportError` raise `DraftError`: "Grouping by sound needs scikit-learn, which is
  an optional extra: install tailorlist-mcp[audio] (for example `uv pip install
  'tailorlist-mcp[audio]'`), then restart the server. assign_tracks groups by mood without it."
- A test imports `tailorlist_mcp.server` with `sklearn` blocked (`sys.modules["sklearn"] = None`
  in a subprocess or with monkeypatch before a fresh import) and checks the server module loads
  and the KMeans mode gives that message. KMeans tests start with `pytest.importorskip("sklearn")`.
- README: the install line for the extra. Both local venvs already have scikit-learn.

### The mode (`drafts.py`, `server.py`)

`group_tracks(mode="kmeans", filters=..., k: int | None = None, include_done=..., preview_only=...)`.
Accept the aliases the tool already recognises ("audio", "audio similarity", "sound").

`drafts.kmeans_groups(pool, k) -> (groups, unmatched, meta)`:

1. Tracks of the pool with all six `config.CLUSTER_FEATURES` inputs present (energy,
   acousticness, valence, tempo, danceability, speechiness) are clustered; the rest go to
   `unmatched` with reason `NO_AUDIO = "no audio features"`, kind "missing".
2. Fewer than 20 tracks with features: `DraftError` ("Only N tracks in the pool have audio
   features; fetch_audio_features gets them" or "widen the filters").
3. `metrics = clustering.k_metrics(df)`; `best_sil, best_elbow = clustering.best_k(metrics)`.
   `k=None` -> `best_sil` (the original's Auto). A given k must be 2 to 10 and below the number of
   tracks, else `DraftError`.
4. `res = clustering.kmeans_cluster(df, k)`. Group name = the descriptive name: for k <= 5 it is
   `res.name_map`, for k > 5 it is `res.descriptions` (see Confirm 1; names are already made
   unique by `make_cluster_names`).
5. `meta = {"k": k, "k_mode": "auto" | "manual", "best_k_silhouette", "best_k_elbow",
   "metrics": [{"k", "inertia", "silhouette"}], "pc_labels": [label1, label2], "pc_variance":
   [var1, var2], "points": {track_id: [pc1, pc2]}}` with the coordinates rounded to 2 decimals.

`new_draft(...)` gets `mode="kmeans"` and stores `meta` as `d["kmeans"]`. `draft_label`: "By
sound (KMeans, k=5)". `preview_text` works as is with that label; add one line for this mode:
"Silhouette suggests k=4, the elbow suggests k=5" (or "Both agree on k=4"), and when a manual k
is above the silhouette's choice, the original's warning that the groups may be hair-splitting.
`preview_only=true` runs the metrics and returns the counts and that line without saving.

In `server.group_tracks`: remove the "comes in a later phase" refusal, add the `k` argument,
update the docstring (mode list, `k`, "needs fetch_audio_features first"). `by`, `genres` and
`also_by_decade` don't apply to this mode: ignore them.

Timing: `k_metrics` fits nine KMeans and nine silhouettes. Measure it on the real copy (about
2,600 tracks); if it passes 20 seconds, compute the silhouette on a fixed random sample of 2,000
rows (`silhouette_score(..., sample_size=2000, random_state=42)`) and say so in the plan's notes.

### What a group card shows (every draft mode)

Audio per group is computed when the draft is shown, from the tracks the group holds now (so it
stays right after `edit_draft` moves): in `drafts._group_card`, when the frame has features, add
`"audio": {"energy", "valence", "danceability", "acousticness", "tempo", "with_audio": n,
"description": verbose_description(...)}` (means over the group's tracks that have features; omit
the key when none has). Groups by decade or genre get it too.

`drafts.summary` adds to each group line: "Sound: energy 0.82, valence 0.31, 128 BPM (high
energy, electric, dark and melancholic)". That is what Claude names the playlists from.
`result_data` trimming: drop `audio.description` before dropping groups if the result is too big.

### The draft page (`ui/draft.html`)

Read DESIGN.md first. Additions, all with tokens and shared classes:

1. On each card with `audio`: the description sentence in `--muted`, and four mini bars (energy,
   valence, danceability, acousticness; `.bars` style, value at the end) plus a tempo chip.
2. KMeans drafts only, above the cards: a card "How many groups?" with the callout text and a
   column chart of the silhouette per k (`.cols`, the chosen k in the accent, the rest
   `--series-other`). The elbow chart is left out: the callout carries its answer.
3. KMeans drafts only, web page only (`full=True` data): a scatter "Your tracks by sound", inline
   SVG, one circle per track at its two principal components, coloured by the group it is in now
   (`--series-1..6`, then `--series-other`), a legend, axis captions "more <pc_labels[0]> →" and
   "more <pc_labels[1]> ↑" with the variance percentages in a caption. Circles radius 3, opacity
   0.7, a `<title>` per circle with "Track - Artist". Cap at 3,000 points. `draft_data(full=True)`
   passes `kmeans.points` with each track's current group index; the tool result does not carry
   the points.

Step 3 is the last thing to build. If it does not look right after two screenshot rounds, leave
it out and note it in TODO.md; 1 and 2 are required.

## Steps

1. `pyproject.toml`, the lazy import and its test.
2. `kmeans_groups`, `new_draft` and label, the server tool. Tests in `tests/test_drafts.py` and
   `tests/test_server_drafts.py`: auto k, manual k, bad k, too few tracks, tracks without features
   go to unmatched, the draft survives `edit_draft` (move, merge, rename) and `write_draft`
   previews it.
3. Card audio and summary (all modes) with tests.
4. The page: cards, the k card, then the scatter. Screenshots (README rule 10) with the demo
   collection: it has features after 02.
5. Docs: TOOLS.md (`group_tracks` done, the differences from the original), README (status line,
   "grouping by sound" no longer "planned"), glossary entries "k / silhouette" in one short term
   ("groups by sound": what KMeans does, how k is chosen; port the wording from
   `core/glossary.py`).

## Verification

Both suites (3.10 and 3.12). On the copy of Facu's cache: `group_tracks(mode="kmeans",
preview_only=true)`, then a real draft with auto k and one with k=6 on two playlists; time them;
open the page and look at the screenshot; read the group names and three sample tracks of each:
do the groups sound like their names? Report what you see, including a group that looks wrong.

## Out of scope

`split_playlist(by="sound")`. Pre-trained models (dropped, see TOOLS.md "Not replicated").
