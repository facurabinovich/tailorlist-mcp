# Tailorlist design system

One look for everything the MCP produces: the three dashboards, the setup page, the login result
pages and the charts Claude draws. It matches the original Tailorlist app. The stylesheet is
`src/tailorlist_mcp/ui/theme.css`, the code is `src/tailorlist_mcp/theme.py`.

## Tokens

Raw colours live only in `:root` of `theme.css` (and as constants in `theme.py`). Everywhere else use `var(--token)`.

| Token | Value | Use |
|---|---|---|
| `--bg` | `#121212` | page |
| `--panel` | `#181818` | sticky table headers, notices, bar-track alternative |
| `--card` | `#282828` | cards |
| `--line` | `#333333` | 1px borders, bar tracks |
| `--text` / `--muted` / `--dim` | `#FFFFFF` / `#B3B3B3` / `#888888` | text, labels, quiet text |
| `--accent` / `--accent-hover` | `#1DB954` / `#25D163` | buttons, links, single-series bars, a few highlight values |
| `--brand` | `#8FBF7A` | only the dot of the wordmark |
| `--series-1` ... `--series-6` | `#1DB954 #3987E5 #D95926 #9085E9 #C98500 #D55181` | chart series, fixed order, never cycled |
| `--series-other` | `#7A7A7A` | the 7th series and later |
| `--danger` / `--warn` | `#E66767` / `#F5A623` | errors, warnings |

Shape and type: cards 14px radius, 1px `--line` border; pill buttons (50px radius, accent
background, `--bg` text, weight 700); font `"Plus Jakarta Sans", system-ui, -apple-system, "Segoe UI", sans-serif`
(embedded in every page from `ui/fonts/`, nothing is loaded from the network); headings weight 800 with
slight negative letter-spacing; labels uppercase `.72rem` weight 600 letter-spacing `.06em` in `--muted`;
numbers `tabular-nums`.

## Components (all in theme.css)

Brand header (`.brand`, `.wordmark` + `.dot`, `.tagline`, `.brand-title`), footer (`.site-footer`),
`.card` (`.accent`, `.danger`), KPI tile (`.card` + `.icon .label .value .sub`), `.grid` with `.kpis` / `.two`,
highlight cards (`.hl`), `.stats`, bar list (`.bars`), column chart (`.cols`), `.rank` + `.meter`, area line
(`.timeline`, `.tl-area`, `.tl-line`), `.chips` (`.heat` for the green ramp), `.badge` (`.ok`, `.warn`),
tables (`.scroll`, `td.n`), `.btn` / `button`, form inputs, `code`, `.notice` (`.danger`), `.error`,
`.step`, `details`. Page files keep only layout that is truly theirs, written with the tokens.

## Page skeleton

```html
<!doctype html>
<html lang="en">
<head>
<!--TAILORLIST_HEAD-->     <!-- charset, viewport, <title>, favicon, embedded font, theme.css inlined -->
</head>
<body>                      <!-- <body class="narrow"> for single-column pages (setup, login) -->
<!--TAILORLIST_BRAND-->    <!-- wordmark + tagline + page title, 1px rule -->
... page content ...
<!--TAILORLIST_FOOTER-->   <!-- one quiet line: small wordmark + "Runs on your computer..." -->
</body>
</html>
```

`theme.inline(html, title)` fills the three placeholders; `theme.template("overview")` reads and fills a
`ui/*.html` template; `theme.simple_page(title, heading, body_html, tone)` builds a one-card page.
`<title>` is always "Tailorlist · <Page>". Every HTML producer goes through theme.py: `browser.write_page`,
the MCP App resources in `server.py`, `sources/spotify_setup.py`, `sources/spotify_auth.py`.

## Chart rules

For charts Claude draws, the rules are the `explain("chart style")` glossary entry (built from the constants
in `theme.py`; a test checks that glossary, theme.py and theme.css agree). In short: dark card surface
whatever the host's theme; one series `#1DB954`, several series in the fixed order, 7th on folds into Other;
magnitude is one green ramp; rankings are horizontal bars sorted most to least with the value at the end,
decades/years/months in time order; no pies for more than 5 parts, no dual axes, no 3D; 4px rounded bar ends;
value on each bar when 12 bars or fewer; the title says the finding, a caption says the scope; text is never
painted in a series colour; signature "Tailorlist" with a `--brand` dot at bottom right.

In-page charts follow the same rules: bar lists are sorted descending with the value in `--muted` at the end;
column charts keep time order and hide value labels above 12 bars (a tooltip still has them); bar tracks are
`--line`; ends are rounded 4px; group cards in a draft take `--series-1..6` in order, then `--series-other`.
The accent on text is for links and a few highlight values the original app also paints green (the year of the
oldest and newest track, highlight values), not for ordinary labels.

## Checklist for a new page or chart

- Built through theme.py (`theme.template`, `theme.inline` or `theme.simple_page`).
- No raw hex or `rgb()` outside `theme.css` (`tests/test_theme.py` checks the templates and Python page strings).
- Brand header and footer present; `<title>` "Tailorlist · <Page>"; `lang="en"`; favicon.
- Shared classes reused, not re-declared in the page.
- Loads nothing from the network: the font is embedded (`ui/fonts/`, SIL Open Font License); no CDN links, scripts or images from other hosts.
- Screenshot reviewed at desktop width and about 400px.
