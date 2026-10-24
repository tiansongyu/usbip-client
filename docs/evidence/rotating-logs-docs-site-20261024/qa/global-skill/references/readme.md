# README: a useful project entry point

## Gather the minimum evidence

Confirm the project audience and current maturity from code and maintained docs. Verify supported commands in parser/help or API definitions; distinguish a successful build from actual I/O or deployment. For releases and changing platform claims, check the current primary source rather than assuming the reference project's setup applies.

Prefer a working claim map over a new documentation system. For example, a cross-platform capability needs the actual supported targets and validation scope; an automated recovery promise needs a separately verified implementation. A CI pass proves the checks it ran, and a staged screenshot does not prove a production deployment.

## Shape the page to the project

Use the following as an adaptable order, not mandatory headings:

1. Project name, precise one-sentence purpose, a few working badges, and useful navigation.
2. One authentic product visual or an accurate architecture diagram, with provenance/demo labeling where needed.
3. Short install/build path with prerequisites and one successful first action. If no release package exists, lead with the source build rather than an imaginary download button.
4. A few representative tasks or examples. Explain expected behavior and any privilege/network/device setup that materially affects success.
5. Compact capability/platform status and meaningful limitations, linking to maintained details and evidence.
6. Development/contribution entry points and the real license.

Small libraries may need only purpose, minimal example, documentation, and license. Larger systems should use progressive disclosure: keep the first screen clear, then link to tutorials, deployment guides, references, troubleshooting, and research. Do not delete important constraints merely to shorten the page or reproduce a reference's section count.

## Commands and claims

- Use the actual executable/API name, feature flags, paths, and supported argument order. Label commands intended for an installed binary versus a source checkout.
- Verify required language/toolchain versions from manifests or pinned tooling. Do not replace project checks with arbitrary generic commands.
- Keep ordinary-user commands and administrator prerequisites distinct. An example must not silently elevate the entire application or mutate a real device to obtain a screenshot.
- Point badges at real CI/release/license sources. A static “passing” badge or a total copied from an older commit can mislead; attach substantive acceptance claims to their actual date/revision and scope.
- Separate available features, simulated validation, real hardware validation, experimental behavior, and planned work. Avoid claiming all devices or all platforms from one fixture.
- Link evidence and limitations near the relevant statement; put exhaustive historical test counts in maintained reports. Retain known negative results when they explain a current boundary.

## Visuals and links

Reuse permitted product assets before creating new ones. Prefer real captures from a safe fixture; redact personal data without changing the represented result. A hand-authored diagram may explain actual architecture, but label future components and do not make it look like a shipped GUI. Use meaningful image alt text and a caption/source link when provenance matters.

Use repository-relative links in README. Check both target existence and heading anchors; GitHub Markdown differs from site URLs. Deep links should identify their purpose (“physical server setup”, “tested compatibility”), not require reading an entire work log. Keep image assets local when practical; do not add analytics or external tracking as decoration.

## Reference examined, not a template to copy

The user-provided [rstop README](https://github.com/fan-ziqi/rstop/blob/f2abbaac3ea4b44c77a926195e5f847e3ce43a6e/README.md) was examined at commit `f2abbaac3ea4b44c77a926195e5f847e3ce43a6e` (2026-10-23). Useful patterns are direct positioning, CI/license links, a product demonstration, runnable examples, and detailed coverage links. Its monitoring pages, keyboard controls, update mechanism, installers, and platform promises are specific to rstop. Its long controls reference is not a universal README requirement. These notes summarize structure; no upstream text, implementation, or imagery is bundled.
