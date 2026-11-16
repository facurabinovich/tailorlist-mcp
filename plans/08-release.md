# 08. Release: version, changelog, PyPI, `uvx`, README walkthrough

Phase 5. Do it when phase 4 is done (02-06) or Facu decides what ships without it. The executor
prepares everything; **tagging, pushing and publishing are Facu's actions** (README rule 12).

## Confirm with Facu first

1. Version number of the first public release: 0.1.0 (proposed).
2. Publishing through GitHub Actions "trusted publishing" (no API token stored). He has to
   create the PyPI account and register the publisher: steps below.
3. Whether phase 4 must be complete before the release.
4. The line at the end of README.md with an "M8ven Score" badge: it is outside the License
   section's flow; keep, move under the title, or remove.

## Steps

### 1. One version source

`pyproject.toml`: `dynamic = ["version"]` and `[tool.hatch.version] path =
"src/tailorlist_mcp/__init__.py"`; remove the static `version`. Set `__version__ = "0.1.0"` there
(the MusicBrainz User-Agent and `MCPServer(version=...)` already read it). Add `authors`,
`keywords`, `classifiers` (Python 3.10-3.13, MIT, "Topic :: Multimedia :: Sound/Audio"),
`[project.urls]` (Homepage, Repository, Issues, Changelog). Add 3.13 to the CI matrix if the suite
passes there.

### 2. The wheel has everything

`uv build`, then list the wheel (`python -m zipfile -l dist/*.whl`): it must contain
`tailorlist_mcp/ui/*.html`, `ui/theme.css`, `ui/fonts/PlusJakartaSans-latin.woff2`,
`ui/fonts/OFL.txt`. Hatch includes non-Python files under `packages`; if any is missing, add
`[tool.hatch.build.targets.wheel.force-include]`. The sdist must not carry `.claude/`, `plans/`
is fine either way; exclude `tests/fixtures` only if large.

Install the wheel in a fresh venv outside the repo and run: `tailorlist-mcp` starts (send an MCP
`initialize` or just check it waits on stdin without a traceback), and a Python one-liner that
calls `theme.template("overview")` and `demo.load` on a temp home. This catches files read by a
path that only exists in the source tree.

### 3. `uvx`

`uvx --from dist/tailorlist_mcp-0.1.0-py3-none-any.whl tailorlist-mcp setup` on a temp
`TAILORLIST_HOME`. Then write the config snippets for the README:

- Claude Code: `claude mcp add -s user tailorlist -- uvx tailorlist-mcp`
  (with the audio extra: `uvx --from 'tailorlist-mcp[audio]' tailorlist-mcp`).
- Claude Desktop: the `mcpServers` JSON block with `"command": "uvx", "args": ["tailorlist-mcp"]`,
  and the note for Windows users without WSL (uv installs natively; say which file to edit).

Check on Facu's Windows side that `uvx tailorlist-mcp` works natively (not through `wsl.exe`):
`browser.py` has WSL-specific opening; the native path must open the dashboard too. If uv is not
installed on Windows, say so and leave it as "not verified on native Windows".

### 4. Changelog

`CHANGELOG.md`, Keep a Changelog format, one entry `0.1.0` grouped Added / Notes, written from
TODO.md "Done" and PLAN.md phases (reading, analysis, drafts, writing with preview and undo,
genres and years from MusicBrainz, demo, guided setup, audio if shipped). Link it from the README.

### 5. Release workflow

`.github/workflows/release.yml`: on a tag `v*`: run the tests, `uv build`, publish with
`pypa/gh-action-pypi-publish` using `permissions: id-token: write` and an environment named
`pypi`, then create the GitHub release with the changelog entry. Add a manual
`workflow_dispatch` input to publish to TestPyPI first.

What Facu does (write these in the report, in order):
1. Check the name `tailorlist-mcp` is free on pypi.org (the executor checks
   `https://pypi.org/pypi/tailorlist-mcp/json` returns 404 and says so).
2. Create a PyPI account with 2FA; under "Publishing" add a pending trusted publisher: owner
   `facurabinovich`, repository `tailorlist-mcp`, workflow `release.yml`, environment `pypi`. Same
   on test.pypi.org for the dry run.
3. Push `main`, run the workflow manually against TestPyPI, try
   `uvx --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ tailorlist-mcp`.
4. `git tag v0.1.0 && git push --tags`.

### 6. README for a stranger

Rewrite the top of the README for someone who has never seen the project:

- What it does in three lines and one screenshot of the overview (from the demo, so no personal
  data: `.claude/design-review/` has the tooling; put images in `docs/img/`).
- "Try it in two minutes": install with uvx, ask Claude for the demo (`load_demo`). Then import
  mode if 07 shipped.
- "Use your own library": the Spotify app walkthrough, numbered, with what each screen asks
  (app name and description: anything; Redirect URI exactly `http://127.0.0.1:8888/callback`;
  API: "Web API"; then the Client ID). It mirrors `setup_guide.py`: keep one source of truth, the
  README points to the setup page for the details. Premium is required for the app owner; "use a
  friend's app" (up to 5 people) stays.
- Status line updated; the tool list stays grouped; "Writing is safe" and "Spotify's terms" stay.
- Privacy: what leaves the machine (Spotify, MusicBrainz, ReccoBeats: what is sent to each).

Screenshots of the Spotify dashboard are Facu's to take (his account); the README works without
them.

## Verification

Both suites; the fresh-venv install; the `uvx` run from the wheel; the workflow file checked with
`actionlint` if available (or a dry read). Report what Facu must do and what was not verified
(native Windows, the real publish).

## Out of scope

The `.mcpb`, the plugin and skill (09). The video and post (10).
