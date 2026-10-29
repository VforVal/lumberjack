# Lumberjack website

Static product site for the Lumberjack plugin. `dist/` contains the complete site:
HTML, CSS, JavaScript, the repository's original SVG logo, a static forest illustration,
PNG icons and the social preview image, and locally bundled Rokkitt and Special Elite
fonts (WOFF2, subset to Latin) with their licenses. No build step, package installation,
external font requests, analytics, or server runtime is needed to serve it.

## Preview

From this directory:

```sh
python -m http.server 8765 --directory dist --bind 127.0.0.1
```

Open http://127.0.0.1:8765. The examples are illustrative simulations. They do not
access or change the visitor's repository, and no settings persist between reloads.

## The examples

**The sweep** is one simulated, squash-merging repository over seven weeks: 17 worktrees
(11 already merged, one dirty, one prototype), 3 leftover `node_modules` folders and 22 doc
bundles on install day, with a new worktree and new merges between session starts (day 0,
2, 5, 9, 16, 30 and 47). `script.js` replays the whole run from the settings (`cleanup`,
`doc_strategy`, `scope`, `grace_days`) with the sweep's rules: fresh and dirty blocks,
kept branches counted as landed, docs staying live until the sweep after their worktree
goes, grace counted from the first sweep that sees them linked, and a 30-day purge. It
prints the sweep's real line formats (`sweep: …`, `sweep would: …`, `dependent …`,
`cleaned …`, `cleaned orphan …`, `blocked …`, `landed …: would remove`, `orphan …`,
`doc …`, `cleared …`, `purged …`). The board shows the worktrees as a forest, the docs in
three lanes (docs/work, trash, purged), counters and a disk chart against "without
Lumberjack". Play advances one sweep every 2.6 seconds and starts once when the example
first scrolls into view; felling, lane moves and counter tweens are off under reduced
motion. On phones a switch shows worktrees or documents. **The Stop gate** example quotes
the gate's two reasons. Keep both in step with `scripts/sweep_worktrees.py`; the setup
panel quotes `scripts/setup_lumberjack.py`.

The Settings button opens a nonmodal popover, dismissed with Done, Escape, the close
button, or a click outside. Browsers without the Popover API get the same open and close
from `script.js`. Without JavaScript the sweep shows a note instead of the board.

`dist/assets/forest.svg` is a static illustration using the logo's outlined
shapes and colors: seven bare trees and three green pines.

## Hosting

**GitHub Pages is the canonical home.** The workflow at
`../.github/workflows/site.yml` deploys `website/dist` on relevant pushes to
`main`, and also supports manual runs. To turn it on, open the repository's
**Settings → Pages → Build and deployment → Source** and select **GitHub Actions**,
then run the **Deploy website to GitHub Pages** workflow.

The page's canonical link, `og:url`, `og:image` and `sitemap.xml` point to
https://vforval.github.io/lumberjack/, and `404.html` links back to `/lumberjack/`.
Change them together if the site moves to a custom domain. `robots.txt` only takes
effect at a domain root, so it matters once a custom domain is used.

All local asset links are relative so the site works under `/lumberjack/`.
The `.nojekyll` file also supports hosts serving these files without Jekyll.
The page sets no Content-Security-Policy: a strict one stopped the examples in previews
that serve or inject scripts from another origin.

`.openai/hosting.json` binds this source to the Codex Sites preview project
(https://lumberjack-plugin.rainylarch14.chatgpt.site/). Keep its project ID for
previews; don't link the preview address from the repository.

**Alternative: Cloudflare Pages.** Connect `VforVal/lumberjack`, select `main`,
use no framework or build command, and set the build output directory to
`website/dist` (repository root stays the root). No source changes are needed to
switch hosts, apart from the canonical URLs above.

[GitHub Pages documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)

## Content and validation

Product claims and installation commands follow the repository README; terms follow
GLOSSARY.md. Update this page when those commands, the cleanup rule or the Stop
gate change. The version appears twice in `index.html` (hero footnote and footer):
bump it with `VERSION`.

Validate with `node --check dist/script.js` and check desktop and mobile layouts;
every frame under prune, report and keep; Markdown-only scope; 7, 3 and 0 days of
grace; an unsaved document strategy; Play, the slider and tile details; the phone switch; each Stop gate case and the toggle;
installation tabs and each copy button; settings popover opening, dismissal and
keyboard behavior; and an axe run with no violations. Keep text at 12 px or larger.
