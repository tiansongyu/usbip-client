# RustUSB dry run of readme-site

This is a content/behavior outline, not a rewritten repository README, generated website, screenshot test, or deployment. It applies the installed skill to actual project sources without modifying the repository or executing product/build/device commands.

## Request A: English README only

Chosen mode: README only. Proposed changed artifact: README.md. No website, JavaScript, workflow, core source, package, release, or Pages-settings changes.

Proposed opening: **RustUSB — Rust-managed USB/IP device redirection for Linux.** State development-preview maturity and real-hardware compatibility limits nearby.

Proposed reading order:

1. One-line purpose, real CI/license links, documentation navigation.
2. Accurate explanatory diagram of ordinary CLI → restricted helper → Native kernel path, with optional userspace virtual/physical paths. Label it architecture; there is no shipped GUI/TUI screenshot to invent.
3. Source-checkout quick start (Rust >=1.97, from Cargo.toml), then safe diagnostics:

   ```sh
   cargo build --workspace --release --locked
   target/release/rustusb doctor --json
   target/release/rustusb devices --json
   ```

   Explain that diagnostic completion is not overall readiness. Device JSON can contain identifiers; readers should redact shared output. No release installer has been verified for this dry run, so no download/curl installer is invented.
4. Three concise routes: Native USB/IP; optional virtual profiles; experimental USBFS. Link operational prerequisites instead of compressing administrator setup into an apparently unprivileged one-liner. Preserve externally managed trusted network and restricted-helper boundaries.
5. Brief capability/validation status. Separate software CI, x86 QEMU USB evidence, actual hardware gaps, and roadmap. Link acceptance/compatibility/current journal report instead of repeating historical test counts throughout the opening.
6. Development entry point, security constraints, roadmap, MIT license.

Keep these facts explicit: persistent grants survive helper restart and block unresolved regrant; normal cooperative finish and verified administrator reconciliation exist; automatic crash-driver restoration and FD revocation do not. No claims of Windows support, native TLS, unrestricted storage sharing, or real-hardware acceptance.

## Request B: English README plus static project website

Chosen mode: paired artifacts. Reuse the README factual plan; propose website/dist/index.html, styles.css, optional app.js, and only necessary local assets. No framework or package manager is needed. The actual parent task owns any repository implementation and publishing decision; this exercise creates none of those artifacts.

Website reading path:

- Hero: precise Linux USB/IP purpose and development-preview status. Primary action links to the verified source-build section, secondary action to real documentation.
- Diagram/demo: implementation-grounded data path or safe captured CLI output with provenance. Do not display a fictional graphical control panel or claim a live device session.
- Capabilities: concise Native, virtual-profile and experimental USBFS sections; no inherited rstop GPU pages, theme switches, installers, or platform promises.
- Evidence and boundaries: current verified reports, retained crash negative evidence, explicit distinction between the journal guard and automatic recovery.
- Start/development links, repository, compatibility, security and MIT footer.

The initial HTML contains all essential copy, commands and links. JavaScript may enhance copy buttons only; no-JS readers still have the text. Include semantic headings, skip link, keyboard-visible focus, meaningful diagram alt/caption, mobile code overflow, readable contrast, and reduced-motion CSS.

For an expected project base https://tiansongyu.github.io/usbip-client/, use ./styles.css and ./assets/architecture.svg, not /assets/architecture.svg. Link repository-only documents to actual GitHub blob/main paths unless they are explicitly copied into the deployed artifact. Test the /usbip-client/ prefix separately from a root preview. Treat this as a planned URL until deployment/live verification exists.

## Claim map used in both modes

| Proposed fact | Repository basis | Limit retained |
| --- | --- | --- |
| Linux USB/IP management | crates/rustusb-cli/src/main.rs; docs/architecture.md | Development preview, no Windows parity |
| Rust >=1.97 and optional backend feature names | Cargo.toml; crates/rustusb-cli/Cargo.toml | Default source build does not enable every experimental command |
| JSON diagnostics/devices | CLI global --json and Command definitions | Not a proof of readiness or actual remote I/O |
| Ordinary server with restricted helper | docs/security.md; docs/rust-physical-server.md | Whole-device FD is trusted authority, not revocable per-request sandbox |
| Journal guard and explicit reconciliation | docs/research/physical-journal-recovery.md | No automatic recovery or proof all external dup references ended |
| Testing/evidence | docs/acceptance.md; docs/compatibility.md; current report | QEMU/CI do not replace real-device G1/G2 |
| License | LICENSE | External Linux components retain their own licenses |

## Dry-run acceptance

The two modes preserve scope; no unverified installer is suggested; commands match manifest/parser facts; claims retain privilege and validation boundaries; both surfaces share factual terminology; project-prefix assets resolve correctly; no-JS/a11y requirements are in the outline; publication is distinct from file generation. Rendering, actual no-JS/browser behavior, link deployment, and live publication are intentionally not claimed by this outline exercise.
