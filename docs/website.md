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

Check narrow and wide layouts, keyboard tab selection, command copying and its failure message. All content is present in the original HTML; JavaScript enhances the backend tabs and copy button. Without scripts, all three backend sections stay visible. Reduced-motion preferences disable smooth scrolling and transitions. The small-screen diagram is a separate, readable vertical layout.

`check-site.py` validates local files, supported Markdown anchors, repository documentation targets, SVG syntax, image metadata and duplicate HTML IDs. It is a local checker, not an external-link crawler or a browser/accessibility audit. It intentionally does not run USB operations.

## Publish

[The Pages workflow](../.github/workflows/pages.yml) checks pull requests and deploys only from `main`. It uploads only `website/`; research logs and the rest of the repository are not part of the website artifact. Actions are pinned to verified upstream commit IDs. The deployment job alone has Pages/OIDC write permissions.

The intended public address is `https://tiansongyu.github.io/usbip-client/`. A successful source commit is not proof of deployment: inspect the workflow conclusion and retrieve the live HTML and assets. Repository Pages settings must use GitHub Actions. The setup follows [GitHub's custom-workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Reusable global skill

The user-requested `readme-site` skill is installed globally at `~/.agents/skills/readme-site`, with a Codex discovery symlink at `~/.codex/skills/readme-site`. Invoke it with `$readme-site` for README work or a README plus a matching static site. It uses verified repository facts and adapts the reference's information hierarchy without copying branding, assets or unsupported product promises.

Example requests:

```text
Use $readme-site to rewrite this repository's README in English.
Use $readme-site to create an English README and matching GitHub Pages showcase.
```

Its README-only mode does not add a website. Publishing, commits and pushes still follow the authorization in the current task. Skill snapshots and validation records are kept with this delivery's evidence so the global installation is reproducible.
