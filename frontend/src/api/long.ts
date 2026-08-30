import type { TaggedSourceValue } from "./mapping";

export interface LongScopeRequest {
  project_key: string;
  receipt_id: string;
  content_sha256: string;
  supplier_scope: string;
}

export interface LongConfirmationRequest extends LongScopeRequest {
  candidate_digest: string;
  confirmed: true;
}

export interface LongCandidateIssue {
  code: string;
  scope: string;
  message: string;
  row_key: string | null;
  sheet_name: string | null;
  coordinate: string | null;
  expected: string | null;
  observed: string | null;
}

export interface LongCandidateRow {
  row_key: string;
  state: "LOADABLE_PENDING" | "ROW_HELD";
  status_label: string;
  pending_data_status: "PENDING" | "HELD";
  source: {
    sheet_name: string;
    coordinate: string;
    raw_value: TaggedSourceValue;
  };
  measurement_count: number;
  measurement_cells: Array<{ sheet_name: string; coordinate: string }>;
  binding: {
    binding_revision: number;
    canonical_model_key: string;
    canonical_supplier_key: string;
    canonical_model_part_key: string;
    canonical_item_key: string;
    measurement_mode: string;
    sample_policy: string;
    approved_by: string;
    approved_at: string;
    effective_from: string;
    effective_to: string | null;
  } | null;
  issues: LongCandidateIssue[];
}

export interface LongCandidateSnapshot {
  state: "LOAD_CANDIDATE_READY" | "PARTIAL_HOLD" | "LOAD_HELD";
  status_label: string;
  message: string;
  candidate_digest: string;
  project_key: string;
  supplier_scope: string;
  receipt: {
    receipt_id: string;
    content_sha256: string;
    original_filename: string;
    size_bytes: number;
  };
  mapping: {
    history_id: string;
    revision_id: string;
    payload_sha256: string;
    template_id: string;
    schema_version: string;
    revision: number;
    approved_by: string;
    approved_at: string;
    effective_from: string;
    effective_to: string | null;
    source_inspection_date: string;
  };
  binding_catalog_revision: string;
  row_count: number;
  loadable_row_count: number;
  held_row_count: number;
  identifiers: Array<{
    kind: string;
    source: { sheet_name: string; coordinate: string };
    raw_value: TaggedSourceValue;
  }>;
  rows: LongCandidateRow[];
  issues: LongCandidateIssue[];
  capabilities: {
    can_confirm: boolean;
    confirm_requires_digest: true;
    auto_binding: false;
    idempotency_managed_by_server: true;
  };
  official_values_created: false;
  calculations_performed: false;
  auto_valid: false;
  ai_called: false;
}

export interface LongPersistenceSnapshot {
  source_file_id: string;
  ingestion_job_id: string;
  status: string;
  status_label: string;
  row_version: number;
  replayed: boolean;
  reused_job_id: string | null;
  blocking_job_id: string | null;
  counts: {
    lot_count: number;
    result_count: number;
    measurement_count: number;
    held_result_count: number;
  };
  pending_only: true;
  official_values_created: false;
  calculations_performed: false;
  auto_valid: false;
}

export interface LongOperationResponse {
  candidate: LongCandidateSnapshot;
  persistence: LongPersistenceSnapshot | null;
}

export interface LongApi {
  createCandidate(request: LongScopeRequest, signal?: AbortSignal): Promise<LongOperationResponse>;
  confirm(
    request: LongConfirmationRequest,
    signal?: AbortSignal,
  ): Promise<LongOperationResponse>;
}

const SAFE_FALLBACK_MESSAGE = "요청을 처리하지 못했습니다. 다시 시도해 주세요.";

export class LongApiError extends Error {
  readonly code: string;
  readonly statusLabel: string;

  constructor(message: string, code = "LONG_REQUEST_FAILED", statusLabel = "Long 처리 오류") {
    super(message);
    this.name = "LongApiError";
    this.code = code;
    this.statusLabel = statusLabel;
  }
}

export const longApi: LongApi = {
  async createCandidate(request, signal) {
    return requestJson("/api/v1/long/candidates", request, signal);
  },

  async confirm(request, signal) {
    return requestJson("/api/v1/long/confirmations", request, signal);
  },
};

async function requestJson(
  input: string,
  body: LongScopeRequest | LongConfirmationRequest,
  signal?: AbortSignal,
): Promise<LongOperationResponse> {
  let response: Response;
  try {
    response = await fetch(input, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new LongApiError(SAFE_FALLBACK_MESSAGE);
  }
  if (!response.ok) throw await safeApiError(response);
  try {
    return (await response.json()) as LongOperationResponse;
  } catch {
    throw new LongApiError(SAFE_FALLBACK_MESSAGE);
  }
}

async function safeApiError(response: Response): Promise<LongApiError> {
  if (response.status < 400 || response.status >= 500) {
    return new LongApiError(SAFE_FALLBACK_MESSAGE);
  }
  try {
    const payload: unknown = await response.json();
    if (!isObject(payload) || !isObject(payload.detail)) {
      return new LongApiError(SAFE_FALLBACK_MESSAGE);
    }
    const { code, message, status_label: statusLabel } = payload.detail;
    if (typeof code !== "string" || typeof message !== "string" || typeof statusLabel !== "string") {
      return new LongApiError(SAFE_FALLBACK_MESSAGE);
    }
    return new LongApiError(message, code, statusLabel);
  } catch {
    return new LongApiError(SAFE_FALLBACK_MESSAGE);
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
