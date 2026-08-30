# Mass Production Quality Validation

<p align="center">
  <img
    src="docs/assets/mass-production-quality-validation-emblem.png"
    alt="Mass Production Quality Validation emblem"
    width="420"
  />
</p>

Mass Production Quality Validation is a local-first OQC data engine for traceable quality decisions and
specification optimization. The implementation foundation Phase 0 has passed,
and the Phase 1 File Store -> Scanner -> Mapping Preview, persistent Mapping
approval/Audit, canonical manual intake, Long-format persistence, approved
numeric-Master review, and explicit data-status decision framework slices have
passed. The first Korean local browser surface also accepts one `.xlsx`/`.xlsm`,
preserves and scans it asynchronously, and displays Receipt/hash/Sheet evidence
without making an automatic official decision. The same Korean screen now
supports explicit Mapping decisions, pending/held Long confirmation, and
trusted-Admin data-status decisions. A separate first-setup panel creates the
project Model/Part/Item/Supplier identities and records separate Draft,
Reviewer, and Admin decisions for the first numeric Master and Mapping-row
Binding. A Korean synthetic OQC -> Scanner ->
offline AI Mapping-location contract also passes under an unverified
`Qwen3.5-33B` assumption. AI output remains human-review-only and cannot
approve, persist, calculate, or create official values. The database accepts
only `PENDING`/`HELD` evidence during ingestion. It never promotes data
automatically; a trusted Admin must explicitly confirm `VALID`, `SUSPECT`, or
`EXCLUDED`. Approved numeric Master limits are compared by deterministic code,
and Spec `PASS`/`FAIL` remains separate from data trust. The supported Korean
synthetic baseline also
passes File Store -> persisted approved Mapping -> pending Long -> isolated
SQLite restart/replay, while changed and ambiguous forms fail closed. The
versioned Mapping schema now preserves extended identifiers, shipment evidence,
row context, unit, location/cavity, and supplier Spec components without
interpreting them as Master data. A project-isolated canonical
Model -> ModelPart -> InspectionItem hierarchy, independent Supplier axis,
reviewed numeric Master Spec revisions, and exact persistent row bindings now
pass their bounded SQLite framework Gate. Supplier evidence still cannot alter
Master automatically. Actual company Master values and qualitative rules are
still absent, so production judgments are not claimed. The
overall Phase 1 Golden Workbook Gate remains open pending real representative
supplier input and approved criteria.

Phase 2 now has a first usable historical-data slice. From an approved Mapping,
the Korean screen accepts several `.xlsx`/`.xlsm` files as one durable batch,
preserves a distinct Receipt for every submission, resumes after restart, and
separates candidate-ready files from duplicate, changed-layout, identifier,
binding, revision, scan, and system exceptions. After reviewing the immutable
eligible set, the user can issue one explicit batch-wide confirmation; the
background worker then materializes only `PENDING`/`HELD` Long rows from the
stored one-time scan proof. A separate on-demand screen compares two explicit
date windows using stored Receipt, Cell/raw, Mapping, Binding, Master, and
decision evidence. It reports structural counts and revision sets only. Neither
Bulk staging nor comparison approves a revision, marks data `VALID`/`REPLACED`,
or calculates statistics or official results. A separate bounded correction
flow lets an Admin explicitly select one eligible `VALID`/`SUSPECT` predecessor
and one reviewed `PENDING` successor, inspect identity, Master, source, complete
measurement-set digest, and NG/FAIL-to-PASS risk evidence, then confirm one
atomic `REPLACED` -> `VALID` pair with a reason. It records an audited durable
chain and exact replay without rewriting original decisions. Bulk revision
candidates remain review-only, and no replacement, statistic, threshold, or AI
decision occurs automatically. Unresolved exceptions and missing real Golden
evidence keep the Phase 2 Gate open.

The current Windows deliverable is an optional personal extension ZIP. It can
be installed and updated independently of Cloud Scheduler, starts only on
localhost, keeps program files separate from user data, and preserves stored
receipts during an update. Scheduler discovery and shared AI-secret handoff
remain Phase 5 work.

The immutable planning baseline is `MASS_PRODUCTION_QUALITY_VALIDATION_CODEX_HANDOFF_PACKAGE.zip`.
Living implementation documents are under `docs/` and the living requirement
tracker is under `requirements/`.

## Windows commands

All automation uses the explicit Python 3.12 virtual environment interpreter.

```powershell
npm.cmd run bootstrap
npm.cmd run lint
npm.cmd run typecheck
npm.cmd test
npm.cmd run gate
npm.cmd run dev
```

After `npm.cmd run bootstrap`, start the personal local application with
`npm.cmd run dev` and open `http://127.0.0.1:8765/`. The current screen performs
manual raw preservation and Workbook scanning. A new or changed form continues
to paged exact source-cell review; an already approved exact form displays its
full Mapping Preview. For a supported row-oriented schema-v2 form, the user can
assign exact source-cell roles, create the first Draft, record a separate
Reviewer decision, and then record a separate Admin approval. After approval,
the same screen builds an evidence-bound Long candidate, shows loadable and
held rows, and persists only `PENDING`/`HELD` rows after a separate explicit
confirmation. Explicit data-status decisions and canonical first setup are
available from the UI. Later Mapping/Master/Binding revisions and supersession
remain unavailable from the UI. When an exact Mapping is approved, the same
screen also exposes **과거 OQC 일괄 분석**. It submits a bounded
project/supplier-scoped batch, polls its durable ID, and shows server-derived
counts plus safe workbook-logical exception evidence. Candidate-ready files
have no per-file approval button. The same panel can load the server-computed
batch-wide finalization candidate, require an explicit reason and confirmation,
and poll durable PENDING/HELD materialization. It also provides a user-triggered
two-period raw-evidence comparison; no statistics or current-Master rejudgment
is performed. From that historical screen, an Admin may separately select an
eligible predecessor and reviewed successor, inspect bounded source and
measurement-set proof, and confirm an audited paired replacement. The command
does not approve a Bulk revision candidate, infer a replacement, or call AI.

Build and install the personal Windows extension package with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release\Build-Package.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release\Install-Package.ps1 `
  -Action Install `
  -PackagePath .staging\releases\MASS-PRODUCTION-QUALITY-VALIDATION-extension-0.1.0.zip
& "$env:LOCALAPPDATA\Programs\Mass Production Quality Validation\Launch-MassProductionQualityValidation.ps1"
```

`Update` uses the same installer and preserves the separate Data Root. `Remove`
removes program code only and preserves user data. The current ZIP needs
Python 3.12 plus package-index or pre-populated cache access because an embedded
Python runtime and offline wheelhouse are not yet included. The current
personal-v1 installed smoke record is
`reports/acceptance/2026-08-15-personal-v1-installed-smoke.md`; the earlier
update/data-preservation record remains in
`reports/acceptance/2026-08-15-windows-extension-installed-smoke.md`.

Direct Alembic commands intentionally require an explicit
`MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL`; `npm.cmd run bootstrap` supplies the local development
URL safely.

The current implementation is local-only. It does not connect to Outlook,
Cloud Scheduler, a shared database, or a live AI provider. Five Korean
synthetic OQC scenarios are under
`outputs/qwen_mapping_oqc_samples_ko_20260815/`; they are Framework evidence,
not supplier or Golden data.

Current status and evidence are recorded in `docs/IMPLEMENTATION_STATUS.md`,
`reports/gates/PHASE_1_GATE_REPORT.md`, and
`reports/gates/PHASE_2_GATE_REPORT.md`.
