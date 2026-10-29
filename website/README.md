# Lumberjack website

Static product site for the Lumberjack plugin. `dist/` contains the complete site:
HTML, CSS, JavaScript, the repository's original SVG logo, a static forest illustration, and
locally bundled Rokkitt and Special Elite fonts with their licenses. No build
step, package installation, external font requests, analytics, or server runtime
is needed to serve it.

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
is removed; its working documents wait through grace, move to trash, and are
purged after retention. Dirty and unverified worktrees keep their docs live.
Report and keep modes also leave the worktree present and its docs live.

The Example settings buttons open the same nonmodal popover beside either
example. Settings take effect immediately without changing the timeline. The
popover can be dismissed with Done, Escape, the close button, or a click outside.
The config block is a view of the same shared settings.

`dist/assets/forest.svg` is a static illustration using the logo's outlined
shapes and colors: seven bare trees and three green pines.

## Sites

`.openai/hosting.json` binds this source to its Sites project and declares
`dist/` as the static output. Keep its existing project ID for future edits.
The initial Sites publication is private to the owner.

## Free public hosting

**Default: GitHub Pages.** It hosts a public repository's static site for free,
without a separate hosting account. The workflow at
`../.github/workflows/site.yml` deploys `website/dist` on relevant pushes to
`main`, and also supports manual runs.

After merging the website into `main`, open the repository's **Settings →
Pages → Build and deployment → Source** and select **GitHub Actions**. Run the
**Deploy website to GitHub Pages** workflow, or let the next matching push run
it. The expected default address is https://vforval.github.io/lumberjack/;
this address is not evidence of a completed GitHub deployment.

All local asset links are relative so the site works under `/lumberjack/`.
The `.nojekyll` file also supports hosts serving these files without Jekyll.

[GitHub Pages documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)

**Alternative: Cloudflare Pages.** Connect `VforVal/lumberjack`, select `main`,
use no framework or build command, and set the build output directory to
`website/dist` (repository root stays the root). The Free plan includes 500
builds per month and a `pages.dev` subdomain. Custom domain registration is
separate from hosting. No source changes are needed to switch hosts.

[Cloudflare Pages limits](https://developers.cloudflare.com/pages/platform/limits/)

## Content and validation

Product claims and installation commands follow the repository README.
Update this page when those commands or the cleanup rule change. Validate
with `node --check dist/script.js` and check desktop and mobile layouts;
merged, dirty, and unverified worktree scenarios; report and keep modes; all
document lifecycle stages; Markdown-only scope; zero grace; an unsaved document
strategy; the Stop gate toggle; installation tabs and copying; and settings popover opening, dismissal, keyboard behavior, and synchronization
from both examples.
