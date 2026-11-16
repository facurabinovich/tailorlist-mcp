# 10. Demo video (2 minutes) and post

Phase 5, last. Needs 08 (so the install line in the video and post is real). The executor writes
the script and the drafts; Facu records and publishes.

## Confirm with Facu first

- The video uses his real library (more convincing: real artists, real mess) or the demo (no
  personal data, invented artists). Proposal: real library for the story, with the playlists he
  is comfortable showing.
- Where the post goes: LinkedIn (his earlier posts are in `spotify-streamlit/linkedin post.docx`
  and the `medium*.docx` files: read them for his voice), and which communities (r/ClaudeAI,
  r/mcp, Hacker News "Show HN", the MCP servers lists).
- Language: the product is English-only; the posts in English, a Spanish LinkedIn version if he
  wants one.

## The video script (`docs/launch/video-script.md`)

Target 2:00, screen recording of Claude Desktop with voice or captions. Every beat is one prompt
typed by the user and what appears; rehearse each on the real data first and write down the
exact prompt that produces the good result.

| Time | Beat | Prompt (to refine by rehearsing) |
|---|---|---|
| 0:00 | The problem, one line on screen: "2,900 saved songs, 15 playlists, no order." | - |
| 0:08 | Load: pick playlists, the overview opens | "Load my playlists Alt7 and People7" |
| 0:25 | One surprising chart | "When is my music from?" (decades), or a fun fact |
| 0:40 | What is off | "What's wrong with my library?" (`library_health`) |
| 0:55 | Group by sound or mood into a draft, the draft page | "Group these by sound into 5 playlists" |
| 1:20 | Adjust by talking | "Move the live tracks out and call that one 'Sunday morning'" |
| 1:35 | The preview and the explicit yes | "Save them to Spotify" -> preview -> "yes" |
| 1:50 | The playlists in the Spotify app | - |
| 1:55 | Install line and repo URL | - |

Rules for the recording: the write shown is real (new playlists only, nothing edited); hide
anything private (email, other chats); use the unified design pages full screen for two seconds
each; no speed-ups that hide the confirmation step, since "it always asks first" is a selling
point.

## The post (`docs/launch/post-linkedin.md`, `post-reddit.md`, `show-hn.md`)

One story, three lengths. Facts to draw on, all in PLAN.md and TODO.md:

- Why it exists: the official Spotify connector in Claude can't read a playlist's contents or
  create one from exact tracks (five tools, measured 2026-11-09).
- What it does that is unusual: reads the whole library into a local cache, finds duplicates and
  different versions of a song, dates songs by original release (the "Hey Jude counts as 2000"
  example), groups by genre, decade, mood or sound, writes back with a preview, a confirmation
  and an undo, never deletes.
- Honest limits: you register your own Spotify app (free, needs Premium, 3 minutes with the
  guided setup); Spotify's developer terms have a clause about AI that is unclear (the README
  states it); demo and import modes need no account.
- It is open source, MIT, runs locally.
- Numbers from Facu's own library make the story concrete (724 tracks moved to an earlier year,
  195 replaced versions found): ask him which he is happy to share.

Style: his voice (read his earlier posts), plain sentences, no hype words, one screenshot or the
video, the install line, the repo link. Show HN title: "Show HN: Tailorlist – an MCP server
that lets Claude reorganize your Spotify library" and a first comment with the technical notes
(the two-step write with a code, the content hash instead of the snapshot ID, MusicBrainz rate
limits).

## Directories to list it in (free; decided 2026-11-16)

Facu decided: the MCP stays free and open source (no paid listing: the directories don't sell
local stdio servers, the code is MIT and public, and Spotify's terms ask for non-commercial use).
On launch day, register it in the MCP directories. For each one, check on the day how a server is
submitted (a form, a pull request, a metadata file in the repo) and what it requires; the list
below is from memory and may be out of date:

- the official MCP registry (registry.modelcontextprotocol.io; it reads a `server.json` in the
  repo and the PyPI package: check the current publishing guide);
- Smithery, Glama, mcp.so, PulseMCP;
- the "awesome MCP servers" lists on GitHub (a pull request each);
- Claude Desktop's extensions directory, if the `.mcpb` of plan 09 was built (check its
  submission rules).

Write `docs/launch/directories.md`: one row per directory with the submission link, what it
asked for, the date submitted and the resulting page. Submitting is Facu's action (his accounts);
the executor prepares the texts (one-line and one-paragraph descriptions, tags, the install
line, a screenshot from the demo) and any metadata file the repo needs.

Donations: add an optional link in the README ("Support") and a `.github/FUNDING.yml`, once Facu
has created the account he prefers (GitHub Sponsors or Ko-fi). Using the MCP never depends on it.

## Other MCP clients (a short check before the posts say "works with ...")

The server is a local stdio MCP server, so any client that runs local stdio servers can use it,
not only Claude. Before the posts claim it, try the demo (`load_demo`, `show_overview`,
`collection_stats`, `group_tracks`) in one or two other clients Facu has (for example Cursor,
VS Code's agent mode, Codex CLI, Gemini CLI) and note for each: do the tools list, do the server
instructions arrive, do the dashboards open in the browser, does the model draw charts. Clients
that only accept remote MCP servers over HTTP (ChatGPT's connectors, as far as known on
2026-11-16: verify) can't run it as is. Record the results in the README ("Works with") and
only name clients that were tried. Tool texts say "Claude" in places: if another client works
well, propose to Facu rewording them to "the assistant" (it is a wide text change; not part of
this plan).

## Done when

The three drafts, the script and `directories.md` are in `docs/launch/`, Facu has read them, and
TODO.md lists what is published and where.
