export type DataReviewCandidateState = "EVALUATED" | "REVIEW_ONLY" | "INELIGIBLE";
export type DataReviewTargetStatus = "VALID" | "SUSPECT" | "EXCLUDED";
export type DataReviewSystemJudgment = "PASS" | "FAIL";
export type SampleComparison =
  | "WITHIN_LIMITS"
  | "BELOW_LSL"
  | "ABOVE_USL"
  | "NOT_EVALUATED";

export interface DataReviewCandidateRequest {
  project_key: string;
  result_id: string;
}

export interface DataReviewTargetsRequest {
  project_key: string;
  ingestion_job_id: string;
}

export interface DataReviewTarget {
  result_id: string;
  source_row_key: string;
  data_status: "PENDING" | "HELD" | "VALID" | "SUSPECT" | "EXCLUDED" | "REPLACED";
  row_version: number;
  canonical_item_key: string | null;
  lot_id: string;
  lot_ordinal: number;
  source_lot_text: string | null;
  inspection_date: string | null;
  reviewable: boolean;
  status_label: string;
}

export interface DataReviewTargetsResponse {
  project_key: string;
  ingestion_job_id: string;
  job_status: string;
  targets: DataReviewTarget[];
  official_values_created: false;
}

export interface DataReviewMasterEvidence {
  project_key: string;
  canonical_item_key: string;
  history_id: string;
  revision_id: string;
  revision_number: number;
  history_row_version: number;
  revision_row_version: number;
  payload_sha256: string;
  declared_effective_from: string;
  declared_effective_to: string | null;
  resolved_effective_to: string | null;
  target: string | null;
  lsl: string | null;
  usl: string | null;
  unit: string;
  external_spec_revision: string;
}

export interface DataReviewSampleEvidence {
  measurement_id: string;
  sample_ordinal: number;
  source_cell: string;
  row_version: number;
  evidence_sha256: string;
  raw_value_json: string;
  raw_numeric_value_json: string | null;
  raw_qualitative_value: string | null;
  formula_flag: boolean;
  numeric_value: string | null;
  comparison: SampleComparison;
}

export interface DataReviewCas {
  expected_result_row_version: number;
  expected_item_row_version: number | null;
  expected_measurement_versions: Array<{
    sample_ordinal: number;
    measurement_id: string;
    row_version: number;
  }>;
  expected_master: {
    history_id: string;
    revision_id: string;
    history_row_version: number;
    revision_row_version: number;
    payload_sha256: string;
  } | null;
}

export interface DataReviewCandidate {
  state: DataReviewCandidateState;
  status_label: string;
  message: string;
  candidate_sha256: string;
  project_key: string;
  result: {
    id: string;
    source_file_id: string;
    lot_id: string;
    source_content_sha256: string;
    inspection_date: string | null;
    data_status: string;
    current_system_judgment: string | null;
    current_system_judgment_status: string;
    current_spec_evaluation_status: string;
    source_evidence_sha256: string;
    binding_snapshot_sha256: string | null;
    candidate_snapshot_sha256: string;
  };
  item: {
    canonical_item_key: string | null;
    disposition: string | null;
    measurement_mode: string | null;
  };
  source_unit: {
    sheet_name: string;
    coordinate: string;
    raw_value: string;
    cell_evidence_sha256: string;
  } | null;
  master_candidates: DataReviewMasterEvidence[];
  selected_master: DataReviewMasterEvidence | null;
  samples: DataReviewSampleEvidence[];
  issues: Array<{ code: string; message: string }>;
  proposed_system_judgment: DataReviewSystemJudgment | null;
  proposed_system_judgment_status: string;
  proposed_spec_evaluation_status: string;
  allowed_target_statuses: DataReviewTargetStatus[];
  cas: DataReviewCas;
  capabilities: {
    can_decide: boolean;
    explicit_confirmation_required: true;
    trusted_local_admin: true;
  };
  official_values_created: false;
  unit_conversion_performed: false;
  ai_used: false;
  statistics_calculated: false;
}

export interface DataReviewCandidateResponse {
  candidate: DataReviewCandidate;
}

export interface DataReviewDecisionRequest {
  project_key: string;
  result_id: string;
  target_status: DataReviewTargetStatus;
  candidate_sha256: string;
  cas: DataReviewCas;
  reason: string;
  confirmed: true;
}

export interface DataReviewDecision {
  transition_id: string;
  project_key: string;
  result_id: string;
  candidate_sha256: string;
  intent_sha256: string;
  target_status: DataReviewTargetStatus;
  result_row_version: number;
  measurement_count: number;
  evaluation_mode: "EVALUATED" | "REVIEW_ONLY";
  system_judgment: DataReviewSystemJudgment | null;
  master: DataReviewMasterEvidence | null;
  replayed: boolean;
  auto_decision: false;
  ai_used: false;
  additional_calculation: false;
}

export interface DataReviewDecisionResponse {
  decision: DataReviewDecision;
}

export interface DataReviewApi {
  getTargets(
    request: DataReviewTargetsRequest,
    signal?: AbortSignal,
  ): Promise<DataReviewTargetsResponse>;
  createCandidate(
    request: DataReviewCandidateRequest,
    signal?: AbortSignal,
  ): Promise<DataReviewCandidateResponse>;
  decide(
    request: DataReviewDecisionRequest,
    signal?: AbortSignal,
  ): Promise<DataReviewDecisionResponse>;
}

const SAFE_FALLBACK_MESSAGE = "요청을 처리하지 못했습니다. 다시 시도해 주세요.";

export class DataReviewApiError extends Error {
  readonly code: string;
  readonly statusLabel: string;

  constructor(
    message: string,
    code = "DATA_REVIEW_REQUEST_FAILED",
    statusLabel = "데이터상태 처리 오류",
  ) {
    super(message);
    this.name = "DataReviewApiError";
    this.code = code;
    this.statusLabel = statusLabel;
  }
}

export const dataReviewApi: DataReviewApi = {
  async getTargets(request, signal) {
    return requestJson<DataReviewTargetsResponse>(
      "/api/v1/data-reviews/targets",
      request,
      signal,
    );
  },

  async createCandidate(request, signal) {
    return requestJson<DataReviewCandidateResponse>(
      "/api/v1/data-reviews/candidates",
      request,
      signal,
    );
  },

  async decide(request, signal) {
    return requestJson<DataReviewDecisionResponse>(
      "/api/v1/data-reviews/decisions",
      request,
      signal,
    );
  },
};

async function requestJson<T>(
  input: string,
  body: DataReviewTargetsRequest | DataReviewCandidateRequest | DataReviewDecisionRequest,
  signal?: AbortSignal,
): Promise<T> {
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
    throw new DataReviewApiError(SAFE_FALLBACK_MESSAGE);
  }
  if (!response.ok) throw await safeApiError(response);
  try {
    return (await response.json()) as T;
  } catch {
    throw new DataReviewApiError(SAFE_FALLBACK_MESSAGE);
  }
}

async function safeApiError(response: Response): Promise<DataReviewApiError> {
  try {
    const payload: unknown = await response.json();
    if (!isObject(payload) || !isObject(payload.detail)) {
      return new DataReviewApiError(SAFE_FALLBACK_MESSAGE);
    }
    const { code, message, status_label: statusLabel } = payload.detail;
    if (typeof code !== "string" || typeof message !== "string" || typeof statusLabel !== "string") {
      return new DataReviewApiError(SAFE_FALLBACK_MESSAGE);
    }
    return new DataReviewApiError(message, code, statusLabel);
  } catch {
    return new DataReviewApiError(SAFE_FALLBACK_MESSAGE);
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
