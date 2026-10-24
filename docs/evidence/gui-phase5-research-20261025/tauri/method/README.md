# Retrieval method

Source research on2026-10-25; no repository mutation or installation. Public HTTPS bodies were retrieved with Python stdlib urllib using User-Agent RustUSB-readonly-Tauri-research/1.0; independent source fetches used ThreadPoolExecutor with4workers. Every *fetch.json records URL, final URL, response status/headers, retrieval UTC, bytes and SHA-256; failures retain their error. None of the downloaded content was executed.

`fetch-public.py` is the locally authored main retrieval script. Initial, supplemental and final one-shot retrievals use the same urllib/hash/JSON method; their complete response records are in the respective JSON files. The crate was read in memory using tarfile.extractfile for only selected named members; no archive paths were extracted to the filesystem, installed or executed. `published-crate-fetch.json` records member hashes and checksum comparison. All upstream files are pinned to993038c; docs/registry latest responses are time-stamped snapshots.

Derived docs-*.txt are main-element text extracted from the corresponding raw HTML with Python html.parser, ignoring script/style content and adding newlines at paragraph/header/list/pre boundaries. Raw HTML remains authoritative. The text extraction is for reading, not an independent source.

Local-context.json records read-only Git HEAD/status and SHA-256 of selected local instructions, E48 scope, work-log and manifests. No local device inventory, credential, ELF, target directory or private archive was collected. The sole source archive is the public crates.io tauri2.12.5 package.

manifest.json hashes every saved file except itself and SHA256SUMS. SHA256SUMS additionally hashes manifest.json and can be checked with sha256sum --check SHA256SUMS from the research directory. No product test counts or release acceptance result is assigned to this research.
