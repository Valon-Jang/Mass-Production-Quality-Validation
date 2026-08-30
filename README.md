# Mass Production Quality Validation

An evidence-driven validation and release-gate core for deciding whether a product, component, material, or process is ready for mass production.

[한국어 README](README_KO.md)

## Why

Mass-production readiness is rarely one status. A component can pass a test while quality review is still open, evidence is incomplete, required approval is pending, or a high-risk issue remains unresolved.

This project keeps those states separate and evaluates them explicitly.

The public version is generic. It contains no company, customer, product, supplier, site, lot, or production data.

## Core model

Each item independently tracks:

- validation/test status,
- quality review status,
- required approval status,
- evidence completeness,
- risk level and open/closed state,
- owner and target date,
- next action.

A test PASS does **not** automatically mean quality approval or mass-production release.

## Release gate

An item blocks the mass-production release gate when any required condition is unresolved, including:

- validation not passed,
- quality review not approved,
- required approval not approved,
- incomplete evidence,
- unassessed risk,
- open HIGH/CRITICAL risk.

The engine returns a deterministic `READY` or `NOT_READY` verdict plus exact blockers by item.

## Quick start

Python 3.10+; standard library only at runtime.

```bash
python -m mass_production_quality_validation examples/synthetic_portfolio.json --as-of 2026-08-31 --pretty
```

Or install the CLI:

```bash
python -m pip install -e .
mass-production-quality-validation examples/synthetic_portfolio.json --pretty
```

## Example output structure

```json
{
  "summary": {
    "total_items": 2,
    "validation_incomplete": 1,
    "quality_not_approved": 1,
    "approval_pending": 0,
    "evidence_incomplete": 1,
    "high_risk_open": 1,
    "release_ready": false
  },
  "release_gate": {
    "verdict": "NOT_READY"
  }
}
```

## What makes this different from a checklist

The project treats validation, evidence, approval, risk, and release permission as separate state dimensions. That matters in real production work because a newer document or a single PASS result should not silently overwrite unrelated readiness conditions.

See [Design notes](docs/DESIGN.md).

## Scope of v0.1

Included:

- deterministic item evaluation,
- validation / quality / approval separation,
- evidence completeness gate,
- HIGH/CRITICAL risk gate,
- due-date warnings,
- portfolio summary,
- mass-production release gate,
- JSON CLI,
- synthetic examples,
- automated tests.

Not yet included:

- web UI,
- database/history layer,
- supplier or ERP integrations,
- statistical quality control,
- FMEA automation,
- control-plan generation,
- CAPA workflow,
- document signing,
- AI recommendation layer.

Those are future extensions, not claims about the current implementation.

## Background

This public implementation grew from real-world experiments in turning engineering validation, quality review, approval, evidence, schedule, and risk tracking into a repeatable system rather than a collection of disconnected spreadsheets and status messages.
