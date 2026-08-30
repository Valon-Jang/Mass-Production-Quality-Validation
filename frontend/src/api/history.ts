export type BulkFinalizationCommandStatus =
  | "QUEUED"
  | "PROCESSING"
  | "COMPLETED"
  | "BLOCKED";

export type BulkFinalizationEntryStatus =
  | "PENDING"
  | "PROCESSING"
  | "COMPLETED"
  | "BLOCKED";

export interface BulkFinalizationEligibleEntry {
  entry_id: string;
  ordinal: number;
  filename: string;
  bulk_row_version: number;
  receipt_id: string;
  content_sha256: string;
  mapping_sha256: string;
  long_candidate_digest: string;
  prepared_checkpoint_sha256: string;
  prepared_checkpoint_version: string;
  prepared_checkpoint_bytes: number;
}

export interface BulkFinalizationExcludedEntry {
  entry_id: string;
  ordinal: number;
  filename: string;
  outcome: string;
  status_code: string;
  issues_sha256: string;
  bulk_row_version: number;
  size_bytes: number;
  upload_sha256: string;
  receipt_id: string | null;
  content_sha256: string | null;
}

export interface BulkFinalizationCapabilities {
  batch_wide_only: boolean;
  async_processing: boolean;
  per_file_selection: boolean;
  auto_long: boolean;
  auto_valid: boolean;
  auto_replaced: boolean;
  calculations: boolean;
  ai_used: boolean;
  initial_database_gate_complete: boolean;
}

export interface BulkFinalizationCandidate {
  batch_id: string;
  project_key: string;
  supplier_scope: string;
  batch_status: string;
  batch_row_version: number;
  finalization_digest: string;
  can_finalize: boolean;
  eligible_count: number;
  excluded_count: number;
  eligible_entries: BulkFinalizationEligibleEntry[];
  excluded_entries: BulkFinalizationExcludedEntry[];
  capabilities: BulkFinalizationCapabilities;
}

export interface BulkFinalizationEntrySnapshot {
  entry_id: string;
  bulk_entry_id: string;
  ordinal: number;
  status: BulkFinalizationEntryStatus;
  status_label: string;
  attempt_count: number;
  row_version: number;
  long_source_file_id: string | null;
  long_ingestion_job_id: string | null;
  long_status: string | null;
  long_row_version: number | null;
  replayed: boolean | null;
  error_code: string | null;
}

export interface BulkFinalizationSummary {
  total: number;
  pending: number;
  processing: number;
  completed: number;
  blocked: number;
}

export interface BulkFinalizationSnapshot {
  command_id: string;
  batch_id: string;
  project_key: string;
  supplier_scope: string;
  status: BulkFinalizationCommandStatus;
  status_label: string;
  message: string;
  finalization_digest: string;
  reason: string;
  row_version: number;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  terminal: boolean;
  poll_after_ms: number | null;
  summary: BulkFinalizationSummary;
  entries: BulkFinalizationEntrySnapshot[];
  capabilities: BulkFinalizationCapabilities;
}

export type HistoricalDataStatus =
  | "PENDING"
  | "HELD"
  | "VALID"
  | "SUSPECT"
  | "EXCLUDED"
  | "REPLACED";

export interface HistoricalRangeInput {
  date_from: string;
  date_to: string;
}

export interface HistoricalComparisonFilters {
  canonical_model_key?: string;
  canonical_model_part_key?: string;
  canonical_item_key?: string;
  canonical_supplier_key?: string;
  mapping_revision_id?: string;
}

export interface HistoricalComparisonInput {
  project_key: string;
  left: HistoricalRangeInput;
  right: HistoricalRangeInput;
  data_statuses: HistoricalDataStatus[];
  filters: HistoricalComparisonFilters;
  limit_per_side: number;
}

export interface HistoricalSample {
  measurement_id: string;
  ordinal: number;
  row_version: number;
  source_sheet_name: string;
  source_cell: string;
  raw_value_tag: string;
  raw_value_text: string | null;
  raw_numeric_value: string | null;
  raw_qualitative_value: string | null;
  formula_flag: boolean;
  evidence_sha256: string;
  data_status: HistoricalDataStatus;
}

export interface HistoricalCellProof {
  role: string;
  sheet_name: string;
  coordinate: string;
  raw_value: { kind: string; value: unknown };
  cached_value: { kind: string; value: unknown };
  formula_text: string | null;
  number_format: string;
  data_type: string;
  display_value: string | null;
  display_value_status: string;
  value_kind: string;
  evidence_sha256: string;
}

export interface HistoricalMappingProof {
  revision_id: string;
  template_id: string;
  revision: number;
  payload_sha256: string;
  schema_version: string;
  applied_effective_from: string;
  applied_effective_to: string | null;
  current_declared_effective_from: string;
  current_declared_effective_to: string | null;
  current_resolved_effective_to: string | null;
}

export interface HistoricalMasterProof {
  history_id: string;
  revision_id: string;
  revision: number;
  history_row_version: number;
  revision_row_version: number;
  payload_sha256: string;
  declared_effective_from: string;
  declared_effective_to: string | null;
  resolved_effective_to: string | null;
}

export interface HistoricalDecisionProof {
  transition_id: string;
  command_id: string;
  evaluation_mode: string;
  candidate_sha256: string;
  decided_by: string;
  decided_at: string;
  reason: string;
  from_status: string;
  to_status: string;
  before_result_row_version: number;
  after_result_row_version: number;
  intent_sha256: string;
  decision_snapshot_sha256: string;
}

export interface HistoricalReplacementLinkProof {
  replacement_id: string;
  predecessor_result_id: string;
  successor_result_id: string;
  predecessor_original_data_status_transition_id: string;
  successor_data_status_transition_id: string;
  predecessor_status_before: "VALID" | "SUSPECT";
  predecessor_status_after: "REPLACED";
  successor_status_before: "PENDING";
  successor_status_after: "VALID";
  predecessor_result_row_version_before: number;
  predecessor_result_row_version_after: number;
  successor_result_row_version_before: number;
  successor_result_row_version_after: number;
  predecessor_measurement_count: number;
  successor_measurement_count: number;
  predecessor_measurement_set_sha256: string;
  successor_measurement_set_sha256: string;
  candidate_sha256: string;
  intent_sha256: string;
  decided_by: string;
  decided_at: string;
  reason: string;
}

export interface HistoricalReplacementChainProof {
  head_result_id: string;
  tail_result_id: string | null;
  current_result_id: string;
  current_position: number | null;
  returned_link_count: number;
  has_more: boolean;
  links_sha256: string;
  links: HistoricalReplacementLinkProof[];
}

export interface HistoricalResult {
  result_id: string;
  lot_id: string;
  source_file_id: string;
  ingestion_job_id: string;
  result_row_version: number;
  inspection_date: string;
  source_lot_text: string | null;
  canonical_model_key: string | null;
  canonical_model_part_key: string | null;
  canonical_item_key: string | null;
  canonical_supplier_key: string | null;
  data_status: HistoricalDataStatus;
  receipt_id: string;
  received_at: string;
  original_filename: string;
  content_sha256: string;
  source_row_key: string;
  source_sheet_name: string;
  supplier_judgment: string | null;
  system_judgment: string | null;
  system_judgment_status: string;
  spec_evaluation_status: string;
  source_evidence_sha256: string;
  source_fields: HistoricalCellProof[];
  binding_catalog_revision: string;
  binding_fingerprint: string;
  binding_revision: number | null;
  binding_snapshot_sha256: string | null;
  binding_proof: Record<string, unknown> | null;
  candidate_snapshot_sha256: string;
  mapping: HistoricalMappingProof;
  applied_master: HistoricalMasterProof | null;
  decision: HistoricalDecisionProof | null;
  replacement_chain: HistoricalReplacementChainProof | null;
  total_sample_count: number;
  returned_sample_count: number;
  samples_has_more: boolean;
  sample_set_sha256: string;
  samples: HistoricalSample[];
}

export interface HistoricalComparisonSide {
  date_from: string;
  date_to: string;
  total_matching: number;
  returned_count: number;
  has_more: boolean;
  total_sample_count: number;
  returned_results_sample_count: number;
  mapping_revision_ids: string[];
  results: HistoricalResult[];
}

export interface HistoricalComparisonDelta {
  result_count_delta: number;
  measurement_count_delta: number;
  left_mapping_revision_ids: string[];
  right_mapping_revision_ids: string[];
  added_mapping_revision_ids: string[];
  removed_mapping_revision_ids: string[];
}

export interface HistoricalComparisonCapabilities {
  official_values_created: boolean;
  calculations_performed: boolean;
  trend_analysis: boolean;
  thresholds_applied: boolean;
  current_master_rejudgment: boolean;
  ai_used: boolean;
}

export interface HistoricalComparisonResponse {
  project_key: string;
  data_statuses: HistoricalDataStatus[];
  filters: HistoricalComparisonFilters;
  left: HistoricalComparisonSide;
  right: HistoricalComparisonSide;
  delta: HistoricalComparisonDelta;
  capabilities: HistoricalComparisonCapabilities;
}

export interface CreateBulkFinalizationInput {
  projectKey: string;
  batchId: string;
  finalizationDigest: string;
  reason: string;
}

export interface HistoryApi {
  getFinalizationCandidate(
    batchId: string,
    projectKey: string,
    signal?: AbortSignal,
  ): Promise<BulkFinalizationCandidate>;
  createFinalization(
    input: CreateBulkFinalizationInput,
    signal?: AbortSignal,
  ): Promise<BulkFinalizationSnapshot>;
  getFinalization(
    batchId: string,
    projectKey: string,
    signal?: AbortSignal,
  ): Promise<BulkFinalizationSnapshot>;
  compare(
    input: HistoricalComparisonInput,
    signal?: AbortSignal,
  ): Promise<HistoricalComparisonResponse>;
}

const SAFE_FALLBACK_MESSAGE = "요청을 처리하지 못했습니다. 다시 시도해 주세요.";

export class HistoryApiError extends Error {
  readonly code: string;
  readonly statusLabel: string;

  constructor(message: string, code = "HISTORY_REQUEST_FAILED", statusLabel = "요청 오류") {
    super(message);
    this.name = "HistoryApiError";
    this.code = code;
    this.statusLabel = statusLabel;
  }
}

export const historyApi: HistoryApi = {
  getFinalizationCandidate(batchId, projectKey, signal) {
    const query = new URLSearchParams({ project_key: projectKey });
    return requestJson(
      `/api/v1/bulk/batches/${encodeURIComponent(batchId)}/finalization-candidate?${query}`,
      { method: "GET", signal },
    );
  },

  createFinalization(input, signal) {
    return requestJson(
      `/api/v1/bulk/batches/${encodeURIComponent(input.batchId)}/finalizations`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_key: input.projectKey,
          finalization_digest: input.finalizationDigest,
          confirmed: true,
          reason: input.reason,
        }),
        signal,
      },
    );
  },

  getFinalization(batchId, projectKey, signal) {
    const query = new URLSearchParams({ project_key: projectKey });
    return requestJson(
      `/api/v1/bulk/batches/${encodeURIComponent(batchId)}/finalizations?${query}`,
      { method: "GET", signal },
    );
  },

  compare(input, signal) {
    return requestJson("/api/v1/history/comparisons", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      signal,
    });
  },
};

async function requestJson<T>(input: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(input, {
      ...init,
      headers: { Accept: "application/json", ...init.headers },
    });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new HistoryApiError(SAFE_FALLBACK_MESSAGE);
  }
  if (!response.ok) throw await safeApiError(response);
  try {
    return (await response.json()) as T;
  } catch {
    throw new HistoryApiError(SAFE_FALLBACK_MESSAGE);
  }
}

async function safeApiError(response: Response): Promise<HistoryApiError> {
  if (response.status < 400 || response.status >= 500) {
    return new HistoryApiError(SAFE_FALLBACK_MESSAGE);
  }
  try {
    const payload: unknown = await response.json();
    if (!isObject(payload) || !isObject(payload.detail)) {
      return new HistoryApiError(SAFE_FALLBACK_MESSAGE);
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
      return new HistoryApiError(SAFE_FALLBACK_MESSAGE);
    }
    return new HistoryApiError(message, code, statusLabel);
  } catch {
    return new HistoryApiError(SAFE_FALLBACK_MESSAGE);
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
