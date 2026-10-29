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

Open http://127.0.0.1:8765. The worktree scenarios, document lifecycle, and setup
controls are illustrative simulations. They do not access or change the
visitor's repository. Cleanup mode, a saved document strategy, scope, and grace
days update the worktree and document examples. The Stop gate toggle updates
the configuration and its explanation. No settings persist between reloads.

## Shared examples

The worktree and document diagrams share one lifecycle and task (`eng-42`).
Moving either slider updates both examples. After landing, an eligible worktree
is removed; its work documents wait through grace, move to trash, and are
purged after retention. Dirty and unverified worktrees keep their docs live.
Report and keep modes also leave the worktree present and its docs live.

The worktree example prints the sweep's real line formats (`sweep: cleaned …`,
`cleaned …`, `blocked …: dirty`, `landed …: would remove`, `cleared …`, `purged …`)
and the Stop gate section quotes the gate's two reasons. Keep both in step with
`scripts/sweep_worktrees.py`.

The Example settings buttons open the same nonmodal popover beside either
example. Settings take effect immediately without changing the timeline. The
popover can be dismissed with Done, Escape, the close button, or a click outside.
Browsers without the Popover API get the same open and close from `script.js`.
The config block is a view of the same shared settings.

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
The page sets a Content-Security-Policy meta tag (same-origin scripts, styles, fonts
and images only): keep JavaScript and CSS in their files, never inline.

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
merged, dirty, and unverified worktree scenarios; report and keep modes; all
document lifecycle stages; Markdown-only scope; zero grace; an unsaved document
strategy; the Stop gate toggle; installation tabs and each copy button; settings
popover opening, dismissal, keyboard behavior, and synchronization from both
examples; the page with JavaScript off (it shows the first stage); and an axe run
with no violations. Keep text at 12 px or larger.
