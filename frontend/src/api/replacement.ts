export type ReplacementPredecessorStatus = "VALID" | "SUSPECT";
export type ReplacementSuccessorStatus = "PENDING";
export type ReplacementSystemJudgment = "PASS" | "FAIL" | null;

export interface ReplacementMeasurementProof {
  measurement_id: string;
  sample_ordinal: number;
  source_cell: string;
  data_status: string;
  row_version: number;
  evidence_sha256: string;
}

export interface ReplacementResultProof {
  result_id: string;
  source_file_id: string;
  lot_id: string;
  data_status: ReplacementPredecessorStatus;
  row_version: number;
  original_data_status_transition_id: string;
  original_decision_candidate_sha256: string;
  system_judgment: ReplacementSystemJudgment;
  measurement_count: number;
  returned_measurement_count: number;
  measurements_has_more: boolean;
  measurement_set_sha256: string;
  measurements: ReplacementMeasurementProof[];
}

export interface ReplacementSuccessorProof {
  result_id: string;
  source_file_id: string;
  lot_id: string;
  data_status: ReplacementSuccessorStatus;
  row_version: number;
  data_review_state: "EVALUATED" | "REVIEW_ONLY" | "INELIGIBLE";
  data_review_candidate_sha256: string;
  proposed_system_judgment: ReplacementSystemJudgment;
  selected_master_history_id: string | null;
  selected_master_revision_id: string | null;
  selected_master_payload_sha256: string | null;
  item_row_version: number | null;
  measurement_count: number;
  returned_measurement_count: number;
  measurements_has_more: boolean;
  measurement_set_sha256: string;
  measurements: ReplacementMeasurementProof[];
}

export interface ReplacementIdentityProof {
  canonical_model_key: string;
  canonical_model_part_key: string;
  canonical_supplier_key: string;
  canonical_item_key: string;
  source_lot_text: string;
}

export interface ReplacementDifference {
  code: string;
  field: string;
  predecessor_value: string | null;
  successor_value: string | null;
}

export interface ReplacementIssue {
  code: string;
  message: string;
}

export interface ReplacementCapabilities {
  explicit_admin_only: true;
  atomic_successor_valid: true;
  automatic_replacement: false;
  automatic_valid: false;
  calculations: false;
  ai_used: false;
  measurement_pairing: false;
}

export interface ReplacementCandidate {
  candidate_contract_version: "result-replacement-candidate-v1";
  project_key: string;
  predecessor: ReplacementResultProof;
  successor: ReplacementSuccessorProof;
  identity: ReplacementIdentityProof;
  differences: ReplacementDifference[];
  issues: ReplacementIssue[];
  can_replace: boolean;
  candidate_sha256: string;
  capabilities: ReplacementCapabilities;
}

export interface ReplacementDecisionInput {
  project_key: string;
  predecessor_result_id: string;
  successor_result_id: string;
  candidate_sha256: string;
  expected_predecessor_result_row_version: number;
  expected_successor_result_row_version: number;
  expected_predecessor_measurement_set_sha256: string;
  expected_successor_measurement_set_sha256: string;
  expected_predecessor_decision_transition_id: string;
  expected_successor_data_review_candidate_sha256: string;
  confirmed: true;
  reason: string;
}

export interface ReplacementDecision {
  replacement_id: string;
  project_key: string;
  predecessor_result_id: string;
  successor_result_id: string;
  predecessor_status: "REPLACED";
  successor_status: "VALID";
  predecessor_result_row_version: number;
  successor_result_row_version: number;
  successor_data_status_transition_id: string;
  predecessor_measurement_count: number;
  successor_measurement_count: number;
  candidate_sha256: string;
  intent_sha256: string;
  decided_by: string;
  decided_at: string;
  reason: string;
  replayed: boolean;
  official_predecessor: false;
  official_successor: true;
  capabilities: ReplacementCapabilities;
}

export interface ReplacementCandidateInput {
  project_key: string;
  predecessor_result_id: string;
  successor_result_id: string;
}

export interface ResultReplacementApi {
  createCandidate(
    input: ReplacementCandidateInput,
    signal?: AbortSignal,
  ): Promise<ReplacementCandidate>;
  decide(input: ReplacementDecisionInput, signal?: AbortSignal): Promise<ReplacementDecision>;
  getDecision(
    replacementId: string,
    projectKey: string,
    signal?: AbortSignal,
  ): Promise<ReplacementDecision>;
}

const SAFE_FALLBACK_MESSAGE = "요청을 처리하지 못했습니다. 다시 시도해 주세요.";

export class ResultReplacementApiError extends Error {
  readonly code: string;
  readonly statusLabel: string;

  constructor(
    message: string,
    code = "RESULT_REPLACEMENT_REQUEST_FAILED",
    statusLabel = "수정본 처리 오류",
  ) {
    super(message);
    this.name = "ResultReplacementApiError";
    this.code = code;
    this.statusLabel = statusLabel;
  }
}

export const resultReplacementApi: ResultReplacementApi = {
  createCandidate(input, signal) {
    return requestJson("/api/v1/result-replacements/candidates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      signal,
    });
  },

  decide(input, signal) {
    return requestJson("/api/v1/result-replacements/decisions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      signal,
    });
  },

  getDecision(replacementId, projectKey, signal) {
    const query = new URLSearchParams({ project_key: projectKey });
    return requestJson(
      `/api/v1/result-replacements/${encodeURIComponent(replacementId)}?${query}`,
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
    throw new ResultReplacementApiError(SAFE_FALLBACK_MESSAGE);
  }
  if (!response.ok) throw await safeApiError(response);
  try {
    return (await response.json()) as T;
  } catch {
    throw new ResultReplacementApiError(SAFE_FALLBACK_MESSAGE);
  }
}

async function safeApiError(response: Response): Promise<ResultReplacementApiError> {
  if (response.status < 400 || response.status >= 500) {
    return new ResultReplacementApiError(SAFE_FALLBACK_MESSAGE);
  }
  try {
    const payload: unknown = await response.json();
    if (!isObject(payload) || !isObject(payload.detail)) {
      return new ResultReplacementApiError(SAFE_FALLBACK_MESSAGE);
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
      return new ResultReplacementApiError(SAFE_FALLBACK_MESSAGE);
    }
    return new ResultReplacementApiError(message, code, statusLabel);
  } catch {
    return new ResultReplacementApiError(SAFE_FALLBACK_MESSAGE);
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
