# GLib scoped backport

Published glib 0.18.5 (MIT), crate SHA256
`233daaf6e83ae6a12a52055f568f9d7cf4671dabb78ff9560ab6da230ce00ee5`.
Only src/variant_iter.rs differs: upstream PR1343 commit
05dff0ee696f9bcd8617cd48c4b812d046d440cb, mutable C out-pointer fix for
RUSTSEC-2024-0429. Entire patched file SHA256
`a0f5ee8acb8faa089bcdfbc9a57372609fce7654026ccef7d9a224d05a654ccc`
matches that merge. Public API, version and license are unchanged.

The adjacent exact patch is SHA256
`8eb6674027f126bdfac118d8644308d390c511e1cf88cf2857794c93a72a2e6f`.
No version spoofing or advisory suppression; version scanners may still report
0.18.5. This is not a complete GTK dependency safety review.
