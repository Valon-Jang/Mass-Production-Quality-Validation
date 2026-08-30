export type DataQualityStatusCounts = {
  PENDING: number;
  HELD: number;
  VALID: number;
  SUSPECT: number;
  EXCLUDED: number;
  REPLACED: number;
};

export interface BoundedDataQualityDetails<T> {
  total: number;
  returned: number;
  has_more: boolean;
  full_set_sha256: string;
  items: T[];
}

export interface DataQualityBulkEntry {
  entry_id: string;
  ordinal: number;
  status: string;
  outcome: string | null;
  status_code: string;
  receipt_id: string | null;
  content_sha256: string | null;
  issue_count: number;
  issues_sha256: string;
  duplicate_of_entry_id: string | null;
  revision_baseline_entry_id: string | null;
}

export interface DataQualityFinalizationEntry {
  bulk_entry_id: string;
  ordinal: number;
  status: string;
  error_code: string | null;
  long_source_file_id: string | null;
  long_ingestion_job_id: string | null;
  long_status: string | null;
  row_version: number;
}

export interface DataQualityResult {
  result_id: string;
  lot_id: string;
  source_file_id: string;
  data_status: string;
  measurement_count: number;
  row_version: number;
  current_data_status_transition_id: string | null;
  current_replacement_transition_id: string | null;
}

export interface DataQualityReplacementLink {
  replacement_id: string;
  predecessor_result_id: string;
  successor_result_id: string;
  predecessor_before_status: string;
  predecessor_after_status: string;
  successor_before_status: string;
  successor_after_status: string;
  decided_at: string;
  candidate_sha256: string;
}

export type DataQualityEvaluationState =
  | "EVALUATED"
  | "NOT_EVALUATED_BY_PHASE"
  | "BLOCKED_BY_INPUT";

export interface InitialDataQualityReport {
  report_version: "initial-data-quality-report-v1";
  report_sha256: string;
  project_key: string;
  batch_id: string;
  supplier_scope: string;
  bulk_status:
    | "STAGED"
    | "PROCESSING"
    | "COMPLETED"
    | "COMPLETED_WITH_EXCEPTIONS"
    | "FAILED";
  inventory: {
    submitted_file_count: number;
    receipt_count: number;
    materialized_source_file_count: number;
    lot_count: number;
    result_count: number;
    measurement_count: number;
  };
  result_status_counts: DataQualityStatusCounts;
  measurement_status_counts: DataQualityStatusCounts;
  bulk_proof: {
    batch_row_version: number;
    terminal: boolean;
    manifest_sha256: string;
    terminal_summary_sha256: string | null;
    outcome_counts: {
      CANDIDATE_READY: number;
      DUPLICATE_CANDIDATE: number;
      MAPPING_REQUIRED: number;
      SCAN_FAILED: number;
      IDENTIFIER_HOLD: number;
      BINDING_HOLD: number;
      VARIATION_REVIEW_REQUIRED: number;
      REVISION_REVIEW_REQUIRED: number;
      ERROR: number;
    };
    unresolved_count: number;
    unresolved_entries_sha256: string;
  };
  finalization_proof: {
    state: "NOT_REQUESTED" | "PRESENT";
    command_id: string | null;
    status: "QUEUED" | "PROCESSING" | "COMPLETED" | "BLOCKED" | null;
    finalization_digest: string | null;
    row_version: number | null;
    total: number;
    pending: number;
    processing: number;
    completed: number;
    blocked: number;
    materialized_job_count: number;
  };
  replacement_proof: {
    transition_count: number;
    predecessor_result_count: number;
    successor_result_count: number;
    transitions_sha256: string;
  };
  details: {
    bulk_entries: BoundedDataQualityDetails<DataQualityBulkEntry>;
    finalization_entries: BoundedDataQualityDetails<DataQualityFinalizationEntry>;
    results: BoundedDataQualityDetails<DataQualityResult>;
    replacement_links: BoundedDataQualityDetails<DataQualityReplacementLink>;
  };
  evaluation_scopes: Array<{
    scope: string;
    state: DataQualityEvaluationState;
    finding_count: number | null;
    evidence_sha256: string | null;
    message: string;
  }>;
  capabilities: {
    read_only: true;
    bounded_details: true;
    official_baseline: false;
    initial_database_gate_complete: false;
    pass_score: false;
    calculations: false;
    thresholds: false;
    ai_used: false;
    scheduler_used: false;
    automatic_state_change: false;
  };
}

export interface DataQualityReportApi {
  getReport(
    batchId: string,
    projectKey: string,
    signal?: AbortSignal,
  ): Promise<InitialDataQualityReport>;
}

const SAFE_FALLBACK_MESSAGE =
  "초기 DB 품질 리포트를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";

export class DataQualityReportApiError extends Error {
  readonly code: string;
  readonly statusLabel: string;

  constructor(
    message: string,
    code = "DATA_QUALITY_REPORT_REQUEST_FAILED",
    statusLabel = "초기 DB 품질 리포트 조회 오류",
  ) {
    super(message);
    this.name = "DataQualityReportApiError";
    this.code = code;
    this.statusLabel = statusLabel;
  }
}

export const dataQualityReportApi: DataQualityReportApi = {
  getReport(batchId, projectKey, signal) {
    const query = new URLSearchParams({ project_key: projectKey });
    return requestJson(
      `/api/v1/bulk/batches/${encodeURIComponent(batchId)}/data-quality-report?${query}`,
      { method: "GET", signal },
    );
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
    throw new DataQualityReportApiError(SAFE_FALLBACK_MESSAGE);
  }
  if (!response.ok) throw await safeApiError(response);
  try {
    return (await response.json()) as T;
  } catch {
    throw new DataQualityReportApiError(SAFE_FALLBACK_MESSAGE);
  }
}

async function safeApiError(response: Response): Promise<DataQualityReportApiError> {
  if (response.status < 400 || response.status >= 500) {
    return new DataQualityReportApiError(SAFE_FALLBACK_MESSAGE);
  }
  try {
    const payload: unknown = await response.json();
    if (!isObject(payload) || !isObject(payload.detail)) {
      return new DataQualityReportApiError(SAFE_FALLBACK_MESSAGE);
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
      return new DataQualityReportApiError(SAFE_FALLBACK_MESSAGE);
    }
    return new DataQualityReportApiError(message, code, statusLabel);
  } catch {
    return new DataQualityReportApiError(SAFE_FALLBACK_MESSAGE);
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
