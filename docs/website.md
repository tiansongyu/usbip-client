# Maintaining the README and project website

The English [README](../README.md) is the repository entry point. The [Chinese README](../README.zh-CN.md) preserves the previous detailed overview; the [Native guide](native-guide.md) retains deployment, diagnostics and ownership rules. Update capability claims against the implementation and its recorded acceptance evidence.

The static site lives in `website/`: plain HTML, CSS, small optional JavaScript and original SVG diagrams. There is no package installation or build step. The site is a project showcase, not the RustUSB GUI. The reference and visual decisions are in [the design note](research/readme-website-design.md).

## Preview and check

From the repository root:

```sh
python3 scripts/check-site.py
node --check website/app.js
python3 -m http.server 8765 --bind 127.0.0.1 --directory website
```

Open `http://127.0.0.1:8765/`. Stop the preview with Ctrl+C. Also test a directory prefix such as `/usbip-client/`, since the public site is a GitHub project site. Assets use relative paths; documentation links point to the GitHub repository rather than assuming Markdown files are deployed.

Check narrow and wide layouts, keyboard tab selection, direct `?backend=virtual#panel-virtual` links, Back/Forward, command copying and its failure message. All content is present in the original HTML; JavaScript enhances the backend tabs and copy button. Without scripts, all three backend sections stay visible. Reduced-motion preferences disable smooth scrolling and transitions. The small-screen diagram is a separate, readable vertical layout.

`check-site.py` validates local files, supported Markdown anchors, repository documentation targets, SVG syntax, image metadata and duplicate HTML IDs. It is a local checker, not an external-link crawler or a browser/accessibility audit. It intentionally does not run USB operations.

## Publish

[The Pages workflow](../.github/workflows/pages.yml) checks pull requests and deploys only from `main`. It uploads only `website/`; research logs and the rest of the repository are not part of the website artifact. Actions are pinned to verified upstream commit IDs. The deployment job alone has Pages/OIDC write permissions.

The public site is [RustUSB](https://tiansongyu.github.io/usbip-client/). The first deployment and live verification are recorded in [the original publication evidence](evidence/readme-site-publication-f87b3ad-20261023.json); the earlier data-link update has [separate publication receipts](evidence/data-link-publication-f095ff5-20261024/manifest.json). A successful source commit is not proof of deployment: inspect the workflow conclusion and retrieve the live HTML and assets. Repository Pages settings must use GitHub Actions. The setup follows [GitHub's custom-workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Reusable global skill

The user-requested `readme-site` skill is installed globally at `~/.agents/skills/readme-site`, with a Codex discovery symlink at `~/.codex/skills/readme-site`. Invoke it with `$readme-site` for README work or a README plus a matching static site. It uses verified repository facts and adapts the reference's information hierarchy without copying branding, assets or unsupported product promises.

Example requests:

```text
Use $readme-site to rewrite this repository's README in English.
Use $readme-site to create an English README and matching GitHub Pages showcase.
```

Its README-only mode does not add a website. Publishing, commits and pushes still follow the authorization in the current task. Skill snapshots and validation records are kept with this delivery's evidence so the global installation is reproducible.


## Current evidence updates

README/site counts link to the [diagnostic-export report](research/diagnostic-export-validation.md): 797 software executions, 13 local diagnostic groups / 19 children and four native CI combinations. Nine diagnostic groups run per CI platform; the four default-only groups were checked locally on x86_64. One focused installed Native QEMU run at clean `d9ebe2e` has one epoch, two I/O rounds and two successful guest exits. The original process exit-101 and tracer-launch failures remain preserved. Transport errors and verified cleanup have separate results.

The earlier [rotating-log report](research/rotating-logs-validation.md) retains 723 executions, six logging scenarios and one focused installed-path run at clean `48c9794` with three epochs. The [preparation-stop report](research/prepare-recording-shutdown-validation.md) retains 23 paired runs at clean `1582f06` with that report's release artifacts. Do not combine these into a single-source matrix or relabel prior software checks.

The preparation-stop update is published at `ac4e802`, with [separate exact-head deployment and live-byte receipts](evidence/prepare-recording-publication-ac4e802-20261024/manifest.json). Its later workflow status does not replace the detailed CI at `5ffdc6c` or the clean QEMU results at `1582f06`.

The rotating-log update is published at `7640efb`, with [separate deployment and live-byte receipts](evidence/rotating-logs-publication-7640efb-20261024/manifest.json). Its software workflow and Pages check/deploy completed successfully; all six canonical HTTPS resources match that content commit exactly. Live desktop/mobile layout, responsive images and keyboard state were checked separately. The detailed 723-execution CI remains at `89ef420`, and the focused installed QEMU scenario remains at clean `48c9794`.

The diagnostic-export update is published at `94e0d07`, with [separate exact-content workflow and live-byte receipts](evidence/diagnostic-export-publication-94e0d07-20261024/manifest.json). All four native software jobs and Pages check/deploy completed successfully. All six canonical HTTPS resources match that content commit; desktop/mobile layout, images and keyboard state were observed on the live page. Detailed 797-execution CI and the focused QEMU scenario remain assigned to `d9ebe2e`.

The tab URL/history, selected indicator and inline link cues are verified separately from USB behavior. A local CSP `script-src 'none'` response exercises initial-HTML readability; a temporary `Permissions-Policy: clipboard-write=()` response exercises the real copy-denial fallback. Those test policies are not published site settings. Record unavailable browser capabilities accurately; a DOM/layout check is not a pixel screenshot or reduced-motion emulation.

The [authentication-refusal update](research/secure-negative-qemu-validation.md) adds six paired runs / 12 guest exits at clean `25870c6`, with two startup credential epochs per pair. Its original four failed host verifications remain preserved. Keep this raw-v1 result separate from the older all-positive security, framed-v2 health and diagnostic matrices. New source text requires its own Pages/live verification; the `94e0d07` receipt above describes the preceding published site.

That update is published at `52627d8`, with [exact-content deployment and live-byte receipts](evidence/authentication-publication-52627d8-20261024/manifest.json). All four native software jobs and Pages check/deploy succeeded. Six canonical HTTPS resources match its Git blobs; actual live desktop/mobile layout, responsive images and keyboard state were checked. Detailed 797-execution software remains at clean `3d7e126`, and all six new QEMU runs remain at clean `25870c6`.

The [authentication-admission update](research/authentication-admission-validation.md)
adds the 824-execution clean software and thirteen-paired-QEMU scope separately
from the earlier results. Six local project-prefix resources match source bytes;
actual desktop/narrow-screen layout, loaded images and tab keyboard behavior
pass. A local CSP `script-src 'none'` response retains all initial instructions.
The screenshot API remains unavailable, so this is DOM/layout verification.
Runtime CSS/JS/images and the four global `readme-site` files are unchanged.
Publication is recorded against its actual pushed content revision.

This update is published at `5845b14`, with
[exact-content receipts](evidence/authentication-admission-publication-5845b14-20261024/manifest.json).
Four native CI jobs and Pages check/deploy succeeded. Six canonical HTTPS
resources match that commit; live desktop/mobile checks observe the new link,
responsive images and no overflow. The 824 local executions remain at clean47f,
and thirteen paired QEMU runs remain at clean d619.

## Hostname fallback evidence refresh (2026-10-24)

The English README and page now lead with the scoped 842/14/28 evidence in the
[hostname fallback report](research/dual-stack-connect-validation.md). Earlier
capabilities retain their original reports and acceptance entries. The global
`readme-site` skill and five runtime assets are unchanged.
[Local documentation/site records](evidence/dual-stack-connect-docs-site-20261024/manifest.json)
include desktop/mobile DOM checks, keyboard navigation and a CSP no-script
preview; screenshot capture was unavailable. Publication has its own receipt.

Content `2aa9337` is live: software workflow 40091124317 passed on four native
platforms, and Pages workflow 40091124270 passed check/deploy. The six canonical
HTTPS resources match its Git blobs. Public desktop/mobile DOM and keyboard
checks passed; no pixel review is claimed. See the
[publication receipt](evidence/dual-stack-connect-publication-2aa9337-20261024/manifest.json).
