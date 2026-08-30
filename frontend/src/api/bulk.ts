export type BulkBatchStatus =
  | "STAGED"
  | "PROCESSING"
  | "COMPLETED"
  | "COMPLETED_WITH_EXCEPTIONS"
  | "FAILED";

export type BulkEntryStatus = "STAGED" | "PROCESSING" | "TERMINAL";

export type BulkEntryOutcome =
  | "CANDIDATE_READY"
  | "DUPLICATE_CANDIDATE"
  | "MAPPING_REQUIRED"
  | "SCAN_FAILED"
  | "IDENTIFIER_HOLD"
  | "BINDING_HOLD"
  | "VARIATION_REVIEW_REQUIRED"
  | "REVISION_REVIEW_REQUIRED"
  | "ERROR";

export type BulkIssueCategory =
  | "SCAN"
  | "MAPPING"
  | "IDENTIFIER"
  | "BINDING"
  | "VARIATION"
  | "REVISION"
  | "DUPLICATE"
  | "SYSTEM";

export type BulkIssueSeverity = "INFO" | "WARNING" | "BLOCKING";

export interface BulkIssue {
  code: string;
  category: BulkIssueCategory;
  severity: BulkIssueSeverity;
  message: string;
  location: string | null;
  evidence_path: string | null;
  baseline_entry_id: string | null;
  expected_json: unknown | null;
  observed_json: unknown | null;
}

export interface BulkReceiptProof {
  receipt_id: string;
  content_sha256: string;
  original_filename: string;
  received_at: string;
  size_bytes: number;
}

export interface BulkMappingProof {
  template_id: string;
  revision: number;
  template_sha256: string;
  effective_from: string;
  effective_to: string | null;
  history_row_version: number;
  revision_row_version: number;
}

export interface BulkCandidateProof {
  state: string;
  candidate_digest: string;
  loadable_row_count: number;
  held_row_count: number;
  revision_identity_sha256: string;
  revision_evidence_sha256: string;
}

export interface BulkEntrySnapshot {
  entry_id: string;
  ordinal: number;
  filename: string;
  mime_type: string;
  size_bytes: number;
  upload_sha256: string;
  status: BulkEntryStatus;
  outcome: BulkEntryOutcome | null;
  status_label: string;
  message: string;
  attempt_count: number;
  row_version: number;
  receipt: BulkReceiptProof | null;
  mapping: BulkMappingProof | null;
  candidate: BulkCandidateProof | null;
  duplicate_of_entry_id: string | null;
  revision_baseline_entry_id: string | null;
  issues: BulkIssue[];
}

export interface BulkSummary {
  total: number;
  staged: number;
  processing: number;
  candidate_ready: number;
  duplicate: number;
  variation: number;
  mapping_required: number;
  scan_failed: number;
  identifier_hold: number;
  binding_hold: number;
  revision_review_required: number;
  error: number;
}

export interface BulkLimits {
  max_files: number;
  max_file_bytes: number;
  max_batch_bytes: number;
}

export interface BulkCapabilities {
  durable_staging: boolean;
  approved_template_reuse: boolean;
  per_file_approval: boolean;
  finalize_available: boolean;
  auto_long: boolean;
  auto_valid: boolean;
  auto_replaced: boolean;
  auto_revision: boolean;
  ai_used: boolean;
}

export interface BulkBatchSnapshot {
  batch_id: string;
  project_key: string;
  supplier_scope: string;
  idempotency_key: string;
  status: BulkBatchStatus;
  status_label: string;
  message: string;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  terminal: boolean;
  poll_after_ms: number | null;
  replayed: boolean;
  limits: BulkLimits;
  summary: BulkSummary;
  entries: BulkEntrySnapshot[];
  capabilities: BulkCapabilities;
}

export interface CreateBulkBatchInput {
  projectKey: string;
  supplierScope: string;
  idempotencyKey: string;
  workbooks: File[];
}

export interface BulkApi {
  createBatch(input: CreateBulkBatchInput, signal?: AbortSignal): Promise<BulkBatchSnapshot>;
  getBatch(batchId: string, projectKey: string, signal?: AbortSignal): Promise<BulkBatchSnapshot>;
}

const SAFE_FALLBACK_MESSAGE = "요청을 처리하지 못했습니다. 다시 시도해 주세요.";

export class BulkApiError extends Error {
  readonly code: string;
  readonly statusLabel: string;

  constructor(message: string, code = "BULK_REQUEST_FAILED", statusLabel = "요청 오류") {
    super(message);
    this.name = "BulkApiError";
    this.code = code;
    this.statusLabel = statusLabel;
  }
}

export const bulkApi: BulkApi = {
  async createBatch(input, signal) {
    const body = new FormData();
    body.append("project_key", input.projectKey);
    body.append("supplier_scope", input.supplierScope);
    body.append("idempotency_key", input.idempotencyKey);
    for (const workbook of input.workbooks) {
      body.append("workbooks", workbook, workbook.name);
    }
    return requestSnapshot("/api/v1/bulk/batches", { method: "POST", body, signal });
  },

  async getBatch(batchId, projectKey, signal) {
    const query = new URLSearchParams({ project_key: projectKey });
    return requestSnapshot(
      `/api/v1/bulk/batches/${encodeURIComponent(batchId)}?${query.toString()}`,
      { method: "GET", signal },
    );
  },
};

async function requestSnapshot(input: string, init: RequestInit): Promise<BulkBatchSnapshot> {
  let response: Response;
  try {
    response = await fetch(input, {
      ...init,
      headers: { Accept: "application/json" },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new BulkApiError(SAFE_FALLBACK_MESSAGE);
  }

  if (!response.ok) {
    throw await safeApiError(response);
  }

  try {
    return (await response.json()) as BulkBatchSnapshot;
  } catch {
    throw new BulkApiError(SAFE_FALLBACK_MESSAGE);
  }
}

async function safeApiError(response: Response): Promise<BulkApiError> {
  if (response.status < 400 || response.status >= 500) {
    return new BulkApiError(SAFE_FALLBACK_MESSAGE);
  }

  try {
    const payload: unknown = await response.json();
    if (!isObject(payload) || !isObject(payload.detail)) {
      return new BulkApiError(SAFE_FALLBACK_MESSAGE);
    }
    const { code, message, status_label: statusLabel } = payload.detail;
    if (
      typeof code !== "string" ||
      typeof message !== "string" ||
      typeof statusLabel !== "string" ||
      !code ||
      !message ||
      !statusLabel
    ) {
      return new BulkApiError(SAFE_FALLBACK_MESSAGE);
    }
    return new BulkApiError(message, code, statusLabel);
  } catch {
    return new BulkApiError(SAFE_FALLBACK_MESSAGE);
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
