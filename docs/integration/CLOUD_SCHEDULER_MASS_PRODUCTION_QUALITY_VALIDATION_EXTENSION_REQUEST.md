# Cloud Scheduler developer request: Mass Production Quality Validation extension contract

- Status: `CONTRACT_PROPOSAL`
- Implementation: `DEFERRED_BY_PHASE`
- Contract owner: Mass Production Quality Validation
- Target phase: Phase 5
- Date: 2026-08-15

This document is a development request and compatibility proposal. It is not
approval to change Cloud Scheduler. Any Cloud Scheduler workspace change needs
separate user approval, read-only contract discovery, impact maps for both
repositories, compatibility tests, and a rollback plan.

## 1. Confirmed product behavior

- Mass Production Quality Validation is an optional per-user package distributed as
  `Cloud Scheduler extension pack - Mass Production Quality Validation`.
- Users who do not install it must see no Scheduler behavior change.
- Scheduler identifies and classifies OQC mail, then publishes the final Mail
  Locator. It does not parse or judge the workbook.
- Mass Production Quality Validation obtains attachments, determines the project, scans, maps, stores,
  analyzes, and renders results.
- Mail for all personal projects enters one application-level inbox. Project
  routing is owned by Mass Production Quality Validation.
- The AI endpoint, default model, and API key are entered once in Scheduler.
  Mass Production Quality Validation consumes a versioned profile without plaintext secret duplication.

## 2. Permanent ownership boundary

Scheduler must not write Mass Production Quality Validation databases. Mass Production Quality Validation must not read Scheduler
databases or copy Scheduler code. Mass Production Quality Validation must not search the whole mailbox.
Scheduler must not infer the DQ project or evaluate Excel quality. The
extension-pack label does not merge code, data, versioning, or rollback.

## 3. Mail Locator Contract v1

Scheduler publishes one immutable delivery envelope per detected message.

```json
{
  "schema_version": "mass-production-quality-validation.scheduler-mail-locator/1.0",
  "delivery_id": "scheduler-stable-uuid",
  "source_system": "cloud-scheduler",
  "source_instance_id": "scheduler-installation-uuid",
  "provider": "OUTLOOK_COM_MAPI",
  "mail_locator": {
    "entry_id": "opaque-outlook-entry-id",
    "store_id": "opaque-outlook-store-id",
    "internet_message_id": "optional-secondary-id"
  },
  "detected_at": "2026-08-15T10:20:00+09:00",
  "received_at": "2026-08-15T10:18:30+09:00",
  "classification": {
    "kind": "OQC",
    "rule_version": "scheduler-rule-version"
  }
}
```

Rules:

- `delivery_id` remains stable across retries of the same delivery.
- Locator values are opaque and case-preserving. Do not trim or normalize.
- Do not include mail body, attachment bytes, or API keys.
- Scheduler does not need to enumerate attachments or calculate attachment
  hashes. Mass Production Quality Validation opens the message, enumerates each attachment, and hashes
  the bytes it actually received.
- If Scheduler moves or classifies a message, it must finish that action first,
  reacquire the locator from the final location, and only then publish.
- After publication, Scheduler should not move the message again until fetch
  completion, unless a later contract explicitly supports locator refresh.
- `internet_message_id` is secondary audit/deduplication evidence, not the sole
  fetch key.
- When a locator becomes stale, Mass Production Quality Validation reports a fetch-stage error and does
  not scan the entire mailbox to recover it.

For Outlook COM/MAPI, retrieval acceptance must prove that the expected item
can be opened using `EntryID + StoreID`. If the Scheduler uses Microsoft Graph
or new Outlook, the provider and identifier contract must be replaced by the
corresponding versioned adapter rather than pretending it is MAPI.

References:

- https://learn.microsoft.com/en-us/office/vba/outlook/how-to/items-folders-and-stores/working-with-entryids-and-storeids
- https://learn.microsoft.com/en-us/graph/outlook-immutable-id
- https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxprops/c4fd09a7-4491-4ced-ba3f-af8e2b8f85be

## 4. Atomic delivery store

Proposed DQ-owned per-user path:

```text
%LOCALAPPDATA%\Mass Production Quality Validation\integration\scheduler\v1\inbox
```

The DQ installer creates the location and ACL. Scheduler writes only to this
contract inbox, never to an internal DQ database. It writes
`<delivery_id>.tmp`, flushes a complete envelope, and atomically renames it on
the same volume to `<delivery_id>.ready.json`. DQ reads only ready files.

Unknown major schema versions are rejected. Unknown minor fields may be
ignored. Invalid envelopes go to quarantine with a safe error. Successfully
imported envelopes are durably recorded before archival. The actual path
registration mechanism remains blocked until Scheduler extension discovery is
inspected in Phase 5.

## 5. Common inbox and project routing

Scheduler never supplies a trusted `project_id`. Mass Production Quality Validation routes by:

1. Workbook model, part number, drawing number, and revision.
2. Registered project and Master mappings.
3. Sheet and section evidence.
4. Filename and mail subject only as hints.

Multiple attachments are processed independently. A workbook containing
multiple models is split by model only after deterministic evidence exists.
Ambiguous project routing remains outside every project database until user
confirmation, using `MAPPING_REQUIRED / PROJECT_ROUTING_AMBIGUOUS`. An unknown
project uses `REGISTRATION_REQUIRED / PROJECT_NOT_REGISTERED`.

Scheduler delivery success and DQ project-routing success are separate states.

## 6. Idempotency and error separation

- Delivery idempotency: `delivery_id`.
- Message candidate identity:
  `source_instance_id + provider + store_id + entry_id`.
- Secondary message correlation: `internet_message_id`.
- Final attachment identity: DQ-computed SHA-256.
- Business duplicate/retest/revision decisions remain DQ responsibilities.
- Outlook fetch, attachment fetch, workbook parsing, routing, mapping, and
  persistence failures must retain distinct stages.
- Scheduler must not mutate DQ `PENDING/PROCESSING/...` states.

## 7. Shared AI Provider Profile v1

Public, non-secret profile:

```json
{
  "schema_version": "mass-production-quality-validation.ai-provider-profile/1.0",
  "profile_id": "default-ai",
  "provider_type": "OPENAI_COMPATIBLE",
  "base_url": "https://example.internal/v1",
  "default_model": "model-name",
  "credential_reference": "CloudScheduler/AI/default",
  "enabled": true,
  "revision": 1,
  "updated_at": "2026-08-15T10:20:00+09:00"
}
```

- The API key is absent from JSON, Queue, logs, and every DQ database.
- Scheduler owns the per-user Secret Store entry and its settings UI.
- DQ resolves only the approved `credential_reference` for the current user.
- DQ displays neither the key nor an editor for the shared endpoint.
- Scheduler key rotation/deletion is visible without a DQ secret copy.
- Removing DQ does not delete Scheduler's profile or key.
- Missing/disabled/failed AI leaves deterministic ingestion, calculation,
  dashboard, and export operational.

The exact Windows Secret Store mechanism and ACL must be documented and tested
before this contract can be accepted.

## 8. Installation, update, removal, and compatibility

The package declares an extension ID, DQ version, supported Scheduler versions,
and supported contract major versions. Installation checks compatibility before
mutation. Failure restores Scheduler state. Updates preserve Queue envelopes,
project data, configuration, and the original file store. Removal preserves
Scheduler and its AI secret; DQ project data is retained unless the user makes
a separate explicit data-deletion choice.

The extension must not register autostart or a permanent service without a
separate approved requirement. Manual upload remains an offline fallback.

## 9. Information requested from the Scheduler developer

1. Classic Outlook COM/MAPI, Graph, new Outlook, or another provider?
2. Does classification use a Category, folder move, or both?
3. At what exact point can the final locator be read?
4. What extension manifest/discovery/launcher mechanism already exists?
5. How are AI endpoint/model and secrets stored today?
6. Can a versioned credential reference be exposed to the same Windows user?
7. Can Scheduler atomically write a file-drop envelope?
8. Supported Windows, Outlook, and Scheduler versions?
9. Installer, update, removal, and rollback behavior?
10. Phase 5 test installer version and SHA-256?

## 10. Required acceptance tests

- `SCH-EXT-001`: DQ-uninstalled Scheduler behavior is unchanged.
- `SCH-MAIL-001`: locator directly opens the expected message.
- `SCH-MAIL-002`: final locator is captured after move/classification.
- `SCH-MAIL-003`: partial JSON is never consumed.
- `SCH-MAIL-004`: repeated delivery ID creates one inbox record.
- `SCH-MAIL-005`: DQ fetches the referenced mail without a full-mailbox scan.
- `SCH-MAIL-006`: multiple Excel attachments remain separate.
- `SCH-ROUTE-001`: Scheduler does not assign a DQ project.
- `SCH-FAIL-001`: locator/fetch failure is separate from parsing failure.
- `SCH-FAIL-002`: DQ downtime or inbox failure does not stop Scheduler.
- `SCH-AI-001`: one Scheduler setup serves DQ without key duplication.
- `SCH-AI-002`: key rotation, deletion, and disabled state propagate.
- `SCH-AI-003`: AI failure leaves DQ Core operational.
- `SCH-SEC-001`: payload, logs, and databases contain no key or mail body.
- `SCH-INSTALL-001`: optional install/update/remove/failure rollback.
- `SCH-COMPAT-001`: unsupported versions fail closed with a clear reason.
- `SCH-REGRESSION-001`: Scheduler regression passes before and after extension.

## 11. Acceptance boundary

This proposal remains `DEFERRED_BY_PHASE`. Outlook provider details, extension
discovery, Secret Store, installer, and real Queue evidence are
`BLOCKED_BY_INPUT`. No Phase 0 or Phase 1 mock can close the Phase 5 gate.

