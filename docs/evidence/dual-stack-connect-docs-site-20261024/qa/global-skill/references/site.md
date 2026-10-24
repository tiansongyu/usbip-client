# Static project showcase paired with a README

## Keep the two artifacts coherent

Use one small factual content plan for both surfaces: identity, audience, verified capabilities, first command, proof, material limits, and next links. Write the README as the repository entry point and the website as a concise visual explanation. Cross-link them. A showcase is not a new application dashboard and does not imply live telemetry or a hosted backend.

If the repository has no site, a small structure such as `website/dist/index.html`, `styles.css`, optional `app.js`, and `assets/` is sufficient. The exact directory is a repository choice. Add a brief site-maintenance note only if preview, asset regeneration, or publishing needs explanation. Do not introduce a package manager/build step solely to generate a static landing page.

An adaptable reading path is: clear hero and primary action → authentic demonstration → a few actual user tasks/capabilities → evidence and limits → install/start links → documentation/repository/license. A reference's theme picker or multi-platform selector belongs only when the target product actually supports it. Use the target project's own visual direction, not an identical clone of the reference.

## Content and runtime behavior

- Put the title, description, navigation, actual command text, feature explanations, limits, and documentation links in initial HTML. With JavaScript disabled or failed, readers must still reach the essential content and instructions. Hiding essential sections behind JavaScript-generated tabs plus a noscript notice is insufficient.
- Use actual product assets or clearly labeled diagrams/demo data. Do not present invented output as a measured result. Explicitly distinguish a recorded capture from live interaction.
- Keep runtime assets local unless the project has a reason to use a trusted external source. Size images, defer noncritical loading, and avoid unnecessary fonts, trackers, autoplay video, or large dependencies.
- Use semantic landmarks/headings and a skip link; native links/buttons; labels and visible focus. Implement keyboard state correctly if tabs are warranted, and announce copy results without blocking content. Motion must not be required to understand the page and must respect reduced-motion preferences.
- Support narrow screens and zoom without clipping essential copy. Keep code/large product captures scrollable within their containers and provide descriptive text.
- Provide truthful document title/description. Canonical/social URLs and preview images must match the intended real destination; do not advertise a live URL before publishing is verified.

## GitHub Pages and project subpaths

Project sites commonly live at `https://OWNER.github.io/REPOSITORY/`, not the domain root. Resolve local CSS/JS/images relatively (for example `./styles.css`, `./assets/diagram.svg`) or use the existing generator's configured base path. Root `/assets/...` paths usually break project sites. Do not assume arbitrary repository docs are included in the deployed artifact: link to their actual deployed location or canonical GitHub paths.

For plain files, preview with a loopback-only static server, for example:

```sh
python3 -m http.server 4173 --bind 127.0.0.1 --directory website/dist
```

Test the expected `/REPOSITORY/` prefix as well as the root preview, using a temporary local directory/mount or existing site tooling. A root-only preview is not proof of project-subpath correctness. Check navigation hashes, assets, and documentation links from both entry paths.

Inspect the repository's actual Pages setup. Use existing build/publish conventions. If deployment changes are requested, verify current official Pages instructions/action versions and keep permissions narrowly scoped. A new website request does not automatically authorize changing domains, secrets, organization settings, or unrelated workflows. If publishing is outside scope, hand off tested static files and describe the remaining deployment step without marking it deployed.

## Verification to report

Check local links and anchors, desktop and mobile layout, keyboard/focus behavior, no-JavaScript readability, reduced motion, copy fallback, and a repository-prefix preview. For any publishing run, distinguish source generation, successful artifact deployment, and successful live HTTP/render checks. Do not imply checks ran when only a plan or static source review exists.

## Reference examined, not bundled

The [rstop live site](https://fan-ziqi.github.io/rstop/) and pinned [HTML](https://github.com/fan-ziqi/rstop/blob/f2abbaac3ea4b44c77a926195e5f847e3ce43a6e/website/dist/index.html), [CSS](https://github.com/fan-ziqi/rstop/blob/f2abbaac3ea4b44c77a926195e5f847e3ce43a6e/website/dist/styles.css), [JavaScript](https://github.com/fan-ziqi/rstop/blob/f2abbaac3ea4b44c77a926195e5f847e3ce43a6e/website/dist/app.js), and [site-maintenance note](https://github.com/fan-ziqi/rstop/blob/f2abbaac3ea4b44c77a926195e5f847e3ce43a6e/website/README.md) were examined at commit `f2abbaac3ea4b44c77a926195e5f847e3ce43a6e`. On 2026-10-23, live HTML matched that pinned source byte-for-byte. Useful ideas are restrained navigation, strong typographic hierarchy, authentic app-rendered examples labeled demo data, responsive local assets, keyboard affordances, and a small no-build footprint. Its installer commands, theme collection, and illustrated TUI views are not transferable product capabilities. No upstream implementation or assets are included here.
