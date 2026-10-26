# Controller enrollment evidence

The [validation report](../../research/controller-enrollment-validation.md) describes actual product controller management on both Ubuntu x86_64 versions at clean `26faa790`. Two pairings pass, with eight normal QEMU exits, eight actual helper exit0 waits, 74 recorded CLI invocations and two separate stock Native read-only data transfers. Final software records contain 1,193 core and 18 GUI test executions.

The archive preserves development failures, both complete software gates, guest records, methods, fixed Git sources and two component source reviews. The first raw-record audit misclassified an `install` argument as a CLI invocation; the corrected audit passes. The first archive scan matched its own private-key marker literal; the corrected anchored check found no private key. Both method failures remain recorded and do not alter the original test verdicts.

`manifest.json` records the origin, size and SHA256 of each included member. All 580 members were read back after compression; their raw size is 21,164,079 bytes and the archive is 2,176,657 bytes. Its SHA256 is `712b555b4f1deb80e722c3fd9c406485da2d67515bfbf8ab3b68ce9d0e494092`.

Private keys, disk/backing images, ELF uploads and `.private` trees are excluded. Nested guest archives contain only the fixture's collected evidence. This is not a storage export permit, a safe media-release proof, another USB execution or a general security audit.
