# README-only forward-test outline

Request: “为这个 RustUSB 仓库只重做英文 README，借鉴 rstop 的结构，别扩展产品功能”。

## Selected delivery scope

The eventual repository change would be README.md only. This forward-test writes an English opening sample and review notes under /tmp/readme-site-forward-test/; it does not apply the change. Existing docs, Chinese README, the separately developed website, product code and workflows remain owned by their current authors.

No website, framework, deployment workflow, new image asset, installer, release, package, backend change, or publication is part of this request. An existing documentation link is enough; do not turn a missing visual into a website project. The opening sample intentionally has no product screenshot or site dependency.

## Proposed page

1. RustUSB name, purpose, real CI/toolchain/license badges, navigation, and development-preview status close to the promise.
2. One source-build route with Linux/Rust prerequisites and explicit checkout-relative binary paths; first actions are --help, devices and doctor.
3. A compact Native workflow: the ordinary CLI user, administrator prerequisites, approved fingerprint/endpoint, export/discover/attach, verify actual I/O, then detach/unexport. Put details in docs/native-guide.md.
4. A three-row backend table: Native default, optional fixed virtual profiles, experimental USBFS. Link the two maintained service guides for feature builds and controls.
5. What is verified: link the dated, revision-bound report. Distinguish 250 test executions, four native software CI combinations and five QEMU cases from hardware compatibility. Keep exhaustive history out of the opening.
6. Boundaries: external network protection, storage/hub refusal, USBFS ISO limits, no automatic SIGKILL driver recovery, physical G1/G2 and TUI/GUI/Windows still unfinished. Preserve current diagnostics and security guidance through links.
7. Contributing through AGENTS.md and scripts/check.sh, then actual MIT license and third-party notices.

## Reference adaptation

The inspected rstop README leads with name, genuine badges, a short value statement, navigation, demonstration, installation/source build and representative commands. RustUSB can use that information order. rstop's released installers, update flow, terminal pages and cross-platform monitoring claims do not apply. No text, screenshot, code or brand asset is copied.

The skill also requested reference-site inspection. Reading rstop's website source confirmed a simple purpose → demonstration → install hierarchy, but did not change this README-only deliverable. That extra read was not necessary to satisfy the user's stated README comparison; making it conditional would improve focus.

## Validation performed

Read the skill and its README reference; inspected repository instructions, current README, manifests, pin, workflow, license, deployment guide, work log and current evidence. Read the supplied rstop README through the GitHub connector, checked RustUSB Releases (empty), and inspected rstop site source read-only. Ran only existing executable help. Checked sample file links and its local anchor.

No build, test suite, QEMU, installation, USB state change, repository write, commit, push, deployment or network write was performed. This is a behavioral authoring test with a partial sample, not a rendered full-README/site acceptance test.
