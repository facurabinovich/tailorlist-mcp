"""The guided tour of the collection: a script the model follows, served as the `tour` prompt and
the `tour` tool (hosts hide prompts, so the tool is what gets called when the user asks for a tour).

Each chapter names the exact tool call, the chart to draw and what to say. Every tool call written
here as name(...) must be a registered tool (tests/test_tour.py checks it).
"""
from __future__ import annotations

INTRO = """\
Give the user a guided tour of their collection in charts: a short story in the chapters below, in order.

How to run it:
- First call explain("chart style") and follow it for every chart.
- If the host can build a page (an artifact), make the whole tour ONE scrolling page in Tailorlist's style, with the chapters in order. Otherwise go chapter by chapter, one chart per message, and ask "next?" between chapters.
- Skip a chapter whose data is empty. Talk in the user's language. Never invent numbers: every figure comes from a tool result.
- Each chapter's title is the finding ("A third of your library is from the 2000s"), then one or two warm sentences. Don't repeat the table.
"""

CHAPTERS: list[tuple[str, str, str, str]] = [
    ("The size of it",
     "How big is this collection?",
     'Call collection_stats(group_by="artist", limit=1): its header gives the unique tracks and its note '
     '("Showing the 1 biggest of N artist groups") gives the number of artists. For the hours call fun_facts(limit=14) '
     "and read the Hours of music fact.",
     "No chart: three big stat tiles (tracks, artists, hours). Say it in one line, like \"2,811 songs by 1,204 artists: 187 hours of music\"."),
    ("Your decades",
     "When is your music from?",
     'collection_stats(group_by="decade")',
     "Vertical bars in time order, the biggest decade in green and the rest muted. Title: the decade that stands out."),
    ("Your artists",
     "Who do you save the most?",
     'collection_stats(group_by="artist", limit=10)',
     "Horizontal bars, top 10, sorted from most to least, the value at the end of each bar. Title: the number one and how far ahead it is."),
    ("Your genres",
     "What does it sound like?",
     'collection_stats(group_by="genre_family")',
     "Horizontal bars of the genre families. If many artists have no genres (the table notes how many tracks are left out), "
     "say so in one line and offer fill_genres at the very end of the tour, not now."),
    ("How it grew",
     "When did you add all this?",
     'collection_stats(group_by="month_added")',
     "A line in time order (columns if there are few months). Title: the busiest stretch. Skip it if no track has an added date."),
    ("Mainstream or niche",
     "Do you live on hits or deep cuts?",
     'collection_stats(group_by="popularity_bin")',
     "Vertical bars of the popularity bins in order. Then call fun_facts(limit=14) and use the Mainstream meter fact to name "
     "the most popular and the rarest track."),
    ("Your playlists compared",
     "How do your playlists differ?",
     "compare_playlists()",
     "One chart only: tracks per playlist (horizontal bars), or the release era of each playlist if the sizes are similar. "
     "Skip it with a single playlist."),
    ("The curiosities",
     "What is funny about your library?",
     "fun_facts(limit=8)",
     "No chart. Tell the best three as a short story, not a list."),
]

CLOSING = """\
Closing: call what_can_i_do() and offer the three things most worth doing next, one line each. If many artists lacked genres, \
mention fill_genres here."""


def tour_text() -> str:
    out = [INTRO, "Chapters:"]
    for i, (title, question, call, say) in enumerate(CHAPTERS, 1):
        out += [f"\n{i}. {title}",
                f"   Question: {question}",
                f"   Data: {call}",
                f"   Chart and words: {say}"]
    out += ["", CLOSING]
    return "\n".join(out)
