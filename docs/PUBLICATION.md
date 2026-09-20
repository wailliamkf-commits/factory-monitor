# Public engineering preview

This repository publishes a sanitized source snapshot of the local engineering
preview. The initial public history starts here; prior local Git history is not
included. Historical reports retain their measurements, but personal filesystem
paths are replaced with `<PROJECT>` or `<HOME>`. Referenced `artifacts/`, models,
local configuration, database files and videos are deliberately not published.
Historical hashes identify the original local measurement inputs, not every
subsequent documentation or portability change. Consult Actions for checks of
the exact public commit.

The public package includes source, synthetic test generators, tests, deployment
instructions and a Windows handoff skill. It contains no trained weights,
production camera footage, API credentials or preconfigured site layout.

Run site tests into `reports/local/`, `field-data/`, `data/`, or `artifacts/`,
which are ignored by Git. Review any report before sharing it: it can contain
window titles, local paths, camera layout information and evidence references.
Ignoring a directory does not remove previously tracked files.

No project-wide open-source license has been selected in this preview. A public
repository alone is not a license grant. Dependencies and separately downloaded
models retain their respective license terms; check those terms before any
redistribution or commercial packaging.

Repository publication and hosted CI do not establish field acceptance. The
real-client switching adapter, concurrent model-review latency, labeled field
evaluation and per-platform endurance gates remain unresolved in STATUS.md.
