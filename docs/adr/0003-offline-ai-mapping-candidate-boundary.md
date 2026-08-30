# ADR 0003: offline AI Mapping candidate boundary

- Status: Accepted for the bounded Phase 1 framework
- Date: 2026-08-15
- Requirements: GOV-004, GOV-005, GOV-008, ARC-014, ARC-015, ARC-016,
  ING-008, ING-009, ING-010, ING-018, ING-019, ING-024, ING-025, CFG-004,
  CFG-017

## Context

The eventual model selected by the user is a cloud-hosted `Qwen3.5-33B`
service with an OpenAI-compatible interface. This workspace has no reachable
endpoint or secret, and Phase 5 shared-profile integration with Cloud Scheduler
is intentionally deferred. The representative supplier Golden Workbook and
approved Master Spec are also unavailable.

Mass Production Quality Validation can still prove the provider-neutral request, response-validation,
failure-isolation, and human-review boundaries without pretending to measure
the real model's accuracy. AI must never become a second calculation or
approval engine, and untrusted workbook text must not be allowed to issue
instructions to the application or provider.

## Decision

- The Phase 1 implementation exposes an offline, provider-neutral Mapping
  location-candidate contract. `Qwen3.5-33B` is recorded only as an unverified
  compatibility assumption; no HTTP adapter, endpoint, API key, credential
  reference, or secret-resolution code is added.
- Requests contain a bounded structural projection of Scanner evidence. They
  exclude workbook bytes, paths, file names, mail content, images, internal
  project keys, endpoints, and credentials. Source identifiers are opaque and
  resolve only to existing Sheet/Cell evidence.
- Responses use a strict versioned JSON schema, reject unknown or duplicate
  fields, and must echo the exact request digest. Every candidate must resolve
  to an allowed source identifier and review grouping from the same request.
- Candidate output is advisory location evidence only. It remains
  `REVIEW_REQUIRED` or an explicit hold/unavailable/invalid state and cannot
  create, approve, supersede, or persist a Mapping Template.
- Specification values, tolerance changes, unit conversion, statistics,
  PASS/FAIL, system judgment, database commands, and approval actions are not
  representable in the accepted response schema.
- Prompt-injection-like workbook text is treated as untrusted data and causes
  a fail-closed review hold without invoking the provider.
- Disabled AI, timeout, provider error, malformed output, and rejected
  candidates do not alter File Store, Scanner, canonical Mapping, or Long
  persistence outcomes. An already approved exact Template bypasses AI.
- Korean synthetic OQC workbooks cover normal reuse, structural change,
  ambiguity, and formula/business-error paths. They are Framework evidence,
  never Golden evidence.

## Consequences and transition boundaries

This decision verifies only deterministic offline contract behavior under a
Qwen-shaped response. It does not verify actual `Qwen3.5-33B` accuracy,
latency, context limits, OpenAI-compatible endpoint behavior, or secret
handoff. `ARC-027` remains `DEFERRED_BY_PHASE`; `GOV-013` and `ING-051` remain
`BLOCKED_BY_INPUT`.

A future Phase 5 adapter must consume the approved versioned Scheduler profile
and current-user Secret Store reference without copying the key or reading the
Scheduler database. Live acceptance will require a segregated endpoint,
redacted evidence, explicit timeout/cancellation limits, AI-off regression, and
comparison against user-approved Golden mappings. None of those future steps
may promote an AI candidate without the existing review and Audit workflow.
