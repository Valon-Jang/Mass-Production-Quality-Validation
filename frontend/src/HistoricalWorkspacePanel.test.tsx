import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  type BulkFinalizationCandidate,
  type BulkFinalizationSnapshot,
  type HistoricalComparisonResponse,
  type HistoricalResult,
  type HistoryApi,
} from "./api/history";
import {
  type ReplacementCandidate,
  type ReplacementDecision,
  type ResultReplacementApi,
  ResultReplacementApiError,
} from "./api/replacement";
import { HistoricalWorkspacePanel } from "./HistoricalWorkspacePanel";

const sha = (value: string) => value.repeat(64);

const capabilities = {
  batch_wide_only: true,
  async_processing: true,
  per_file_selection: false,
  auto_long: false,
  auto_valid: false,
  auto_replaced: false,
  calculations: false,
  ai_used: false,
  initial_database_gate_complete: false,
};

const candidate: BulkFinalizationCandidate = {
  batch_id: "batch-history",
  project_key: "project-a",
  supplier_scope: "supplier-a",
  batch_status: "COMPLETED_WITH_EXCEPTIONS",
  batch_row_version: 4,
  finalization_digest: sha("a"),
  can_finalize: true,
  eligible_count: 1,
  excluded_count: 1,
  eligible_entries: [
    {
      entry_id: "bulk-entry-ready",
      ordinal: 0,
      filename: "normal.xlsx",
      bulk_row_version: 3,
      receipt_id: "receipt-ready",
      content_sha256: sha("b"),
      mapping_sha256: sha("c"),
      long_candidate_digest: sha("d"),
      prepared_checkpoint_sha256: sha("5"),
      prepared_checkpoint_version: "bulk-prepared-long-v1",
      prepared_checkpoint_bytes: 4096,
    },
  ],
  excluded_entries: [
    {
      entry_id: "bulk-entry-revision",
      ordinal: 1,
      filename: "changed.xlsx",
      outcome: "REVISION_REVIEW_REQUIRED",
      status_code: "BULK_REVISION_REVIEW_REQUIRED",
      issues_sha256: sha("e"),
      bulk_row_version: 4,
      size_bytes: 8192,
      upload_sha256: sha("6"),
      receipt_id: "receipt-revision",
      content_sha256: sha("7"),
    },
  ],
  capabilities,
};

function finalization(status: BulkFinalizationSnapshot["status"]): BulkFinalizationSnapshot {
  const terminal = status === "COMPLETED" || status === "BLOCKED";
  const completed = status === "COMPLETED" ? 1 : 0;
  const blocked = status === "BLOCKED" ? 1 : 0;
  return {
    command_id: "finalization-command",
    batch_id: "batch-history",
    project_key: "project-a",
    supplier_scope: "supplier-a",
    status,
    status_label: terminal ? "정상 후보 반영 완료" : "정상 후보 반영 중",
    message: terminal
      ? "정상 후보를 PENDING Long DB에 반영했습니다."
      : "정상 후보를 순서대로 반영하고 있습니다.",
    finalization_digest: candidate.finalization_digest,
    reason: "과거 자료 정상 후보 일괄 반영",
    row_version: terminal ? 3 : 1,
    created_at: "2026-08-15T14:00:00Z",
    updated_at: terminal ? "2026-08-15T14:00:02Z" : "2026-08-15T14:00:00Z",
    finished_at: terminal ? "2026-08-15T14:00:02Z" : null,
    terminal,
    poll_after_ms: terminal ? null : 1,
    summary: {
      total: 1,
      pending: status === "QUEUED" ? 1 : 0,
      processing: status === "PROCESSING" ? 1 : 0,
      completed,
      blocked,
    },
    entries: [
      {
        entry_id: "finalization-entry",
        bulk_entry_id: "bulk-entry-ready",
        ordinal: 0,
        status: status === "COMPLETED" ? "COMPLETED" : status === "BLOCKED" ? "BLOCKED" : status === "PROCESSING" ? "PROCESSING" : "PENDING",
        status_label: status === "COMPLETED" ? "PENDING 반영 완료" : status === "BLOCKED" ? "반영 보류" : "처리 대기",
        attempt_count: terminal ? 1 : 0,
        row_version: terminal ? 2 : 1,
        long_source_file_id: status === "COMPLETED" ? "source-file" : null,
        long_ingestion_job_id: status === "COMPLETED" ? "long-job" : null,
        long_status: status === "COMPLETED" ? "COMPLETED_PENDING" : null,
        long_row_version: status === "COMPLETED" ? 2 : null,
        replayed: status === "COMPLETED" ? false : null,
        error_code: status === "BLOCKED" ? "FINALIZATION_CHECKPOINT_STALE" : null,
      },
    ],
    capabilities,
  };
}

const comparison: HistoricalComparisonResponse = {
  project_key: "project-a",
  data_statuses: ["PENDING"],
  filters: {},
  left: {
    date_from: "2026-01-01",
    date_to: "2026-01-31",
    total_matching: 1,
    returned_count: 1,
    has_more: false,
    total_sample_count: 1,
    returned_results_sample_count: 1,
    mapping_revision_ids: ["mapping-revision-1"],
    results: [
      {
        result_id: "result-left",
        lot_id: "lot-left",
        source_file_id: "source-left",
        ingestion_job_id: "job-left",
        result_row_version: 1,
        inspection_date: "2026-01-10",
        source_lot_text: "LOT-A",
        canonical_model_key: "MODEL-A",
        canonical_model_part_key: "PART-A",
        canonical_item_key: "WIDTH",
        canonical_supplier_key: "SUPPLIER-A",
        data_status: "PENDING",
        receipt_id: "receipt-left",
        received_at: "2026-01-10T01:00:00Z",
        original_filename: "history.xlsx",
        content_sha256: sha("8"),
        source_row_key: "Report!10",
        source_sheet_name: "Report",
        supplier_judgment: "OK",
        system_judgment: null,
        system_judgment_status: "NOT_EVALUATED",
        spec_evaluation_status: "NOT_EVALUATED",
        source_evidence_sha256: sha("f"),
        source_fields: [
          {
            role: "source_spec_revision",
            sheet_name: "Report",
            coordinate: "F10",
            raw_value: { kind: "string", value: "REV-A" },
            cached_value: { kind: "none", value: null },
            formula_text: null,
            number_format: "General",
            data_type: "s",
            display_value: "REV-A",
            display_value_status: "EXACT",
            value_kind: "QUALITATIVE",
            evidence_sha256: sha("9"),
          },
        ],
        binding_catalog_revision: "binding-catalog-v1",
        binding_fingerprint: sha("0"),
        binding_revision: 1,
        binding_snapshot_sha256: sha("1"),
        binding_proof: { canonical_item_key: "WIDTH" },
        candidate_snapshot_sha256: sha("2"),
        mapping: {
          revision_id: "mapping-revision-1",
          template_id: "template-a",
          revision: 1,
          payload_sha256: sha("3"),
          schema_version: "2",
          applied_effective_from: "2026-01-01",
          applied_effective_to: null,
          current_declared_effective_from: "2026-01-01",
          current_declared_effective_to: null,
          current_resolved_effective_to: null,
        },
        applied_master: null,
        decision: null,
        replacement_chain: null,
        total_sample_count: 1,
        returned_sample_count: 1,
        samples_has_more: false,
        sample_set_sha256: sha("a"),
        samples: [
          {
            measurement_id: "measurement-left",
            ordinal: 1,
            row_version: 1,
            source_sheet_name: "Report",
            source_cell: "H10",
            raw_value_tag: "decimal",
            raw_value_text: "{\"kind\":\"decimal\",\"value\":\"10.25\"}",
            raw_numeric_value: "10.25",
            raw_qualitative_value: null,
            formula_flag: false,
            evidence_sha256: sha("4"),
            data_status: "PENDING",
          },
        ],
      },
    ],
  },
  right: {
    date_from: "2026-02-01",
    date_to: "2026-02-28",
    total_matching: 0,
    returned_count: 0,
    has_more: false,
    total_sample_count: 0,
    returned_results_sample_count: 0,
    mapping_revision_ids: [],
    results: [],
  },
  delta: {
    result_count_delta: -1,
    measurement_count_delta: -1,
    left_mapping_revision_ids: ["mapping-revision-1"],
    right_mapping_revision_ids: [],
    added_mapping_revision_ids: [],
    removed_mapping_revision_ids: ["mapping-revision-1"],
  },
  capabilities: {
    official_values_created: false,
    calculations_performed: false,
    trend_analysis: false,
    thresholds_applied: false,
    current_master_rejudgment: false,
    ai_used: false,
  },
};

const baseHistoricalResult = comparison.left.results[0] as HistoricalResult;
const predecessorResult: HistoricalResult = {
  ...baseHistoricalResult,
  result_id: "result-predecessor",
  lot_id: "lot-predecessor",
  source_file_id: "source-predecessor",
  ingestion_job_id: "job-predecessor",
  result_row_version: 2,
  data_status: "VALID",
  receipt_id: "receipt-predecessor",
  original_filename: "original.xlsx",
  system_judgment: "FAIL",
  system_judgment_status: "EVALUATED",
  spec_evaluation_status: "EVALUATED_APPROVED_MASTER",
  decision: {
    transition_id: "decision-predecessor",
    command_id: "command-predecessor",
    evaluation_mode: "EVALUATED",
    candidate_sha256: sha("p"),
    decided_by: "local-owner",
    decided_at: "2026-01-10T02:00:00Z",
    reason: "초회 데이터 검토",
    from_status: "PENDING",
    to_status: "VALID",
    before_result_row_version: 1,
    after_result_row_version: 2,
    intent_sha256: sha("q"),
    decision_snapshot_sha256: sha("r"),
  },
  samples: baseHistoricalResult.samples.map((sample) => ({
    ...sample,
    measurement_id: "measurement-predecessor",
    row_version: 2,
    data_status: "VALID",
  })),
};
const successorResult: HistoricalResult = {
  ...baseHistoricalResult,
  result_id: "result-successor",
  lot_id: "lot-successor",
  source_file_id: "source-successor",
  ingestion_job_id: "job-successor",
  result_row_version: 1,
  inspection_date: "2026-02-10",
  data_status: "PENDING",
  receipt_id: "receipt-successor",
  original_filename: "corrected.xlsx",
  system_judgment: null,
  system_judgment_status: "NOT_EVALUATED",
  spec_evaluation_status: "NOT_EVALUATED",
  applied_master: null,
  decision: null,
  samples: baseHistoricalResult.samples.map((sample) => ({
    ...sample,
    measurement_id: "measurement-successor",
    raw_numeric_value: "9.95",
    raw_value_text: "{\"kind\":\"decimal\",\"value\":\"9.95\"}",
    row_version: 1,
    data_status: "PENDING",
  })),
};

const replacementComparison: HistoricalComparisonResponse = {
  ...comparison,
  data_statuses: ["VALID", "PENDING"],
  left: {
    ...comparison.left,
    results: [predecessorResult],
  },
  right: {
    ...comparison.right,
    total_matching: 1,
    returned_count: 1,
    total_sample_count: 1,
    returned_results_sample_count: 1,
    mapping_revision_ids: ["mapping-revision-1"],
    results: [successorResult],
  },
};

const replacementCapabilities = {
  explicit_admin_only: true,
  atomic_successor_valid: true,
  automatic_replacement: false,
  automatic_valid: false,
  calculations: false,
  ai_used: false,
  measurement_pairing: false,
} as const;

const replacementCandidate: ReplacementCandidate = {
  candidate_contract_version: "result-replacement-candidate-v1",
  project_key: "project-a",
  predecessor: {
    result_id: "result-predecessor",
    source_file_id: "source-predecessor",
    lot_id: "lot-predecessor",
    data_status: "VALID",
    row_version: 2,
    original_data_status_transition_id: "decision-predecessor",
    original_decision_candidate_sha256: sha("p"),
    system_judgment: "FAIL",
    measurement_count: 1,
    returned_measurement_count: 1,
    measurements_has_more: false,
    measurement_set_sha256: sha("s"),
    measurements: [
      {
        measurement_id: "measurement-predecessor",
        sample_ordinal: 1,
        source_cell: "H10",
        data_status: "VALID",
        row_version: 2,
        evidence_sha256: sha("4"),
      },
    ],
  },
  successor: {
    result_id: "result-successor",
    source_file_id: "source-successor",
    lot_id: "lot-successor",
    data_status: "PENDING",
    row_version: 1,
    data_review_state: "EVALUATED",
    data_review_candidate_sha256: sha("t"),
    proposed_system_judgment: "PASS",
    selected_master_history_id: "master-history",
    selected_master_revision_id: "master-revision",
    selected_master_payload_sha256: sha("u"),
    item_row_version: 3,
    measurement_count: 1,
    returned_measurement_count: 1,
    measurements_has_more: false,
    measurement_set_sha256: sha("v"),
    measurements: [
      {
        measurement_id: "measurement-successor",
        sample_ordinal: 1,
        source_cell: "H10",
        data_status: "PENDING",
        row_version: 1,
        evidence_sha256: sha("w"),
      },
    ],
  },
  identity: {
    canonical_model_key: "MODEL-A",
    canonical_model_part_key: "PART-A",
    canonical_supplier_key: "SUPPLIER-A",
    canonical_item_key: "WIDTH",
    source_lot_text: "LOT-A",
  },
  differences: [
    {
      code: "SYSTEM_JUDGMENT_FAIL_TO_PASS",
      field: "system_judgment",
      predecessor_value: "FAIL",
      successor_value: "PASS",
    },
    {
      code: "SOURCE_SPEC_CHANGED_NOT_EVALUABLE",
      field: "source_specification",
      predecessor_value: "10.0±0.1",
      successor_value: "10.0±0.2",
    },
  ],
  issues: [],
  can_replace: true,
  candidate_sha256: sha("x"),
  capabilities: replacementCapabilities,
};

const replacementDecision: ReplacementDecision = {
  replacement_id: "replacement-001",
  project_key: "project-a",
  predecessor_result_id: "result-predecessor",
  successor_result_id: "result-successor",
  predecessor_status: "REPLACED",
  successor_status: "VALID",
  predecessor_result_row_version: 3,
  successor_result_row_version: 2,
  successor_data_status_transition_id: "decision-successor",
  predecessor_measurement_count: 1,
  successor_measurement_count: 1,
  candidate_sha256: replacementCandidate.candidate_sha256,
  intent_sha256: sha("y"),
  decided_by: "local-owner",
  decided_at: "2026-08-16T01:00:00Z",
  reason: "수정본 원본과 위험 변경을 확인했습니다.",
  replayed: false,
  official_predecessor: false,
  official_successor: true,
  capabilities: replacementCapabilities,
};

function createApi(overrides: Partial<HistoryApi> = {}): HistoryApi {
  return {
    getFinalizationCandidate: vi.fn().mockResolvedValue(candidate),
    createFinalization: vi.fn().mockResolvedValue(finalization("COMPLETED")),
    getFinalization: vi.fn().mockResolvedValue(finalization("COMPLETED")),
    compare: vi.fn().mockResolvedValue(comparison),
    ...overrides,
  };
}

function createReplacementApi(overrides: Partial<ResultReplacementApi> = {}): ResultReplacementApi {
  return {
    createCandidate: vi.fn().mockResolvedValue(replacementCandidate),
    decide: vi.fn().mockResolvedValue(replacementDecision),
    getDecision: vi.fn().mockResolvedValue({ ...replacementDecision, replayed: true }),
    ...overrides,
  };
}

async function loadReplacementComparison(user: ReturnType<typeof userEvent.setup>) {
  const starts = screen.getAllByLabelText("시작일");
  const ends = screen.getAllByLabelText("종료일");
  await user.type(starts[0] as HTMLElement, "2026-01-01");
  await user.type(ends[0] as HTMLElement, "2026-01-31");
  await user.type(starts[1] as HTMLElement, "2026-02-01");
  await user.type(ends[1] as HTMLElement, "2026-02-28");
  await user.click(screen.getByRole("checkbox", { name: "VALID" }));
  await user.click(screen.getByRole("checkbox", { name: "PENDING" }));
  await user.click(screen.getByRole("button", { name: "원본 근거 비교 실행" }));
  await screen.findByText("original.xlsx · 2026-01-10T01:00:00Z");
}

describe("HistoricalWorkspacePanel", () => {
  it("DQ-P2-BULKFINALUI-001 정상 후보 전체와 예외 제외 근거를 보여주고 개별 선택을 만들지 않는다", async () => {
    const api = createApi();
    const user = userEvent.setup();
    render(<HistoricalWorkspacePanel batchId="batch-history" projectKey="project-a" supplierScope="supplier-a" batchTerminal api={api} />);

    await user.click(screen.getByRole("button", { name: "정상 후보 반영 근거 확인" }));
    expect(await screen.findByText(/normal\.xlsx/)).toBeInTheDocument();
    expect(screen.getByText(/changed\.xlsx/)).toBeInTheDocument();
    expect(screen.getByText(/REVISION_REVIEW_REQUIRED · BULK_REVISION_REVIEW_REQUIRED/)).toBeInTheDocument();
    const candidatePanel = screen.getByText("Finalization digest").closest(".history-candidate");
    expect(candidatePanel).not.toBeNull();
    expect(within(candidatePanel as HTMLElement).getAllByRole("checkbox")).toHaveLength(1);
    expect(screen.getByText(/초기 DB Gate 완료를 뜻하지 않습니다/)).toBeInTheDocument();
    const confirmation = within(candidatePanel as HTMLElement).getByRole("checkbox");
    const reason = within(candidatePanel as HTMLElement).getByRole("textbox");
    await user.type(reason, "이전 후보 확인 사유");
    await user.click(confirmation);
    await user.click(screen.getByRole("button", { name: "정상 후보 반영 근거 확인" }));
    await waitFor(() => expect(confirmation).not.toBeChecked());
    expect(reason).toHaveValue("");
  });

  it("DQ-P2-BULKFINALUI-002 명시 사유와 전체 확인 뒤 비동기 반영 상태를 재조회한다", async () => {
    const api = createApi({
      createFinalization: vi.fn().mockResolvedValue(finalization("PROCESSING")),
      getFinalization: vi.fn().mockResolvedValue(finalization("COMPLETED")),
    });
    const user = userEvent.setup();
    render(<HistoricalWorkspacePanel batchId="batch-history" projectKey="project-a" supplierScope="supplier-a" batchTerminal api={api} />);

    await user.click(screen.getByRole("button", { name: "정상 후보 반영 근거 확인" }));
    await user.type(await screen.findByLabelText("반영 사유"), "과거 자료 정상 후보 일괄 반영");
    await user.click(screen.getByRole("checkbox", { name: /정상 후보 전체와 예외 제외 근거/ }));
    await user.click(screen.getByRole("button", { name: "정상 후보 전체 PENDING/HELD 반영" }));

    expect(api.createFinalization).toHaveBeenCalledWith(
      {
        projectKey: "project-a",
        batchId: "batch-history",
        finalizationDigest: candidate.finalization_digest,
        reason: "과거 자료 정상 후보 일괄 반영",
      },
      expect.any(AbortSignal),
    );
    await waitFor(() => expect(api.getFinalization).toHaveBeenCalled(), { timeout: 2000 });
    expect(await screen.findByText("정상 후보를 PENDING Long DB에 반영했습니다.")).toBeInTheDocument();
    expect(screen.getByText("COMPLETED_PENDING")).toBeInTheDocument();
  });

  it("DQ-P2-BULKFINALUI-003 BLOCKED와 초기 Gate 미완료를 표시하고 VALID·REPLACED 명령을 노출하지 않는다", async () => {
    const api = createApi({ getFinalization: vi.fn().mockResolvedValue(finalization("BLOCKED")) });
    const user = userEvent.setup();
    render(<HistoricalWorkspacePanel batchId="batch-history" projectKey="project-a" supplierScope="supplier-a" batchTerminal api={api} />);
    await user.click(screen.getByRole("button", { name: "기존 반영 상태 조회" }));

    expect(await screen.findByText("FINALIZATION_CHECKPOINT_STALE")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /VALID|REPLACED|수정본 승인|초기 DB Gate 완료/ })).not.toBeInTheDocument();
  });

  it("DQ-P2-HISTUI-001 두 기간과 상태를 명시해 원본 Cell·Raw·Mapping 근거를 나란히 조회한다", async () => {
    const api = createApi();
    const user = userEvent.setup();
    render(<HistoricalWorkspacePanel batchId="batch-history" projectKey="project-a" supplierScope="supplier-a" batchTerminal api={api} />);

    const starts = screen.getAllByLabelText("시작일");
    const ends = screen.getAllByLabelText("종료일");
    await user.type(starts[0] as HTMLElement, "2026-01-01");
    await user.type(ends[0] as HTMLElement, "2026-01-31");
    await user.type(starts[1] as HTMLElement, "2026-02-01");
    await user.type(ends[1] as HTMLElement, "2026-02-28");
    await user.click(screen.getByRole("checkbox", { name: "PENDING" }));
    await user.click(screen.getByRole("button", { name: "원본 근거 비교 실행" }));

    expect(api.compare).toHaveBeenCalledWith(
      expect.objectContaining({
        project_key: "project-a",
        left: { date_from: "2026-01-01", date_to: "2026-01-31" },
        right: { date_from: "2026-02-01", date_to: "2026-02-28" },
        data_statuses: ["PENDING"],
        limit_per_side: 100,
      }),
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("2026-01-10 · LOT LOT-A")).toBeInTheDocument();
    expect(screen.getByText("Report!H10")).toBeInTheDocument();
    expect(screen.getByText("10.25")).toBeInTheDocument();
    expect(screen.getByText(/template-a rev\.1/)).toBeInTheDocument();
    expect(screen.getByText(/history\.xlsx/)).toBeInTheDocument();
    expect(screen.getByText("source_spec_revision")).toBeInTheDocument();
    expect(screen.getAllByText("REV-A").length).toBeGreaterThan(0);
    expect(screen.getByText("원본 근거 조회 전용")).toBeInTheDocument();
  });

  it("DQ-P2-HISTUI-002 scope 불일치와 기간·상태 누락을 fail-closed하고 통계 기능을 만들지 않는다", async () => {
    const api = createApi({
      getFinalizationCandidate: vi.fn().mockResolvedValue({ ...candidate, project_key: "project-b" }),
    });
    const user = userEvent.setup();
    render(<HistoricalWorkspacePanel batchId="batch-history" projectKey="project-a" supplierScope="supplier-a" batchTerminal api={api} />);

    await user.click(screen.getByRole("button", { name: "정상 후보 반영 근거 확인" }));
    expect(await screen.findByText(/Project, Supplier scope 또는 배치가 현재 화면과 일치하지 않습니다/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "원본 근거 비교 실행" }));
    expect(screen.getByText("비교할 두 기간의 시작일과 종료일을 모두 선택해 주세요.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /평균|Cpk|Trend|재판정|AI/ })).not.toBeInTheDocument();
    expect(api.compare).not.toHaveBeenCalled();
  });

  it("DQ-P2-REPLUI-001 두 결과의 원본·Master·위험변화를 확인하고 사유와 명시 확인 뒤 원자 대체한다", async () => {
    const api = createApi({ compare: vi.fn().mockResolvedValue(replacementComparison) });
    const replacementApi = createReplacementApi();
    const user = userEvent.setup();
    const view = render(
      <HistoricalWorkspacePanel
        batchId="batch-history"
        projectKey="project-a"
        supplierScope="supplier-a"
        batchTerminal
        api={api}
        replacementApi={replacementApi}
      />,
    );

    await loadReplacementComparison(user);
    await user.click(screen.getByRole("button", { name: "result-predecessor 기존 결과로 선택" }));
    await user.click(screen.getByRole("button", { name: "result-successor 수정본 후보로 선택" }));
    await user.click(screen.getByRole("button", { name: "대체 근거 다시 계산" }));

    expect(replacementApi.createCandidate).toHaveBeenCalledWith(
      {
        project_key: "project-a",
        predecessor_result_id: "result-predecessor",
        successor_result_id: "result-successor",
      },
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("SYSTEM_JUDGMENT_FAIL_TO_PASS")).toBeInTheDocument();
    expect(screen.getByText("SOURCE_SPEC_CHANGED_NOT_EVALUABLE")).toBeInTheDocument();
    expect(screen.getByText(/master-revision/)).toBeInTheDocument();
    expect(screen.getByText(/자동 VALID\/REPLACED 없음/)).toBeInTheDocument();

    const reason = screen.getByLabelText("수정본 대체 사유");
    const confirmation = screen.getByRole("checkbox", { name: /두 결과의 원본·Master·판정/ });
    await user.type(reason, "수정본 원본과 위험 변경을 확인했습니다.");
    await user.click(confirmation);
    await user.click(screen.getByRole("button", { name: "대체 근거 다시 계산" }));
    await waitFor(() => expect(confirmation).not.toBeChecked());
    expect(reason).toHaveValue("");

    await user.type(reason, "수정본 원본과 위험 변경을 확인했습니다.");
    await user.click(confirmation);
    await user.click(screen.getByRole("button", { name: "기존 REPLACED / 수정본 VALID 원자 확정" }));

    const expectedDecision = {
      project_key: "project-a",
      predecessor_result_id: "result-predecessor",
      successor_result_id: "result-successor",
      candidate_sha256: replacementCandidate.candidate_sha256,
      expected_predecessor_result_row_version: 2,
      expected_successor_result_row_version: 1,
      expected_predecessor_measurement_set_sha256: sha("s"),
      expected_successor_measurement_set_sha256: sha("v"),
      expected_predecessor_decision_transition_id: "decision-predecessor",
      expected_successor_data_review_candidate_sha256: sha("t"),
      confirmed: true,
      reason: "수정본 원본과 위험 변경을 확인했습니다.",
    };
    expect(replacementApi.decide).toHaveBeenCalledWith(expectedDecision, expect.any(AbortSignal));
    expect(JSON.stringify(expectedDecision)).not.toMatch(/actor|roles|command_id|target_status|master_revision_id/);
    expect(await screen.findByText("원자 대체를 완료했습니다")).toBeInTheDocument();
    expect(screen.getByText(/기존 REPLACED · 수정본 VALID · 공식선택은 수정본만/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "대체 이력 다시 조회" }));
    expect(replacementApi.getDecision).toHaveBeenCalledWith(
      "replacement-001",
      "project-a",
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("기존 결정을 멱등 재조회했습니다")).toBeInTheDocument();

    view.rerender(
      <HistoricalWorkspacePanel
        batchId="batch-history"
        projectKey="project-b"
        supplierScope="supplier-a"
        batchTerminal
        api={api}
        replacementApi={replacementApi}
      />,
    );
    await waitFor(() => expect(screen.queryByText("기존 결정을 멱등 재조회했습니다")).not.toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: "수정본 대체 이력 확인" })).not.toBeInTheDocument();
  }, 15_000);

  it("DQ-P2-REPLUI-002 비적격 대체는 차단하고 자동판정·통계·Threshold·AI 효과를 노출하지 않는다", async () => {
    const api = createApi({ compare: vi.fn().mockResolvedValue(replacementComparison) });
    const ineligible: ReplacementCandidate = {
      ...replacementCandidate,
      can_replace: false,
      issues: [{ code: "SUCCESSOR_REVIEW_REQUIRED", message: "수정본 후보가 VALID 평가 조건을 충족하지 않습니다." }],
      successor: {
        ...replacementCandidate.successor,
        data_review_state: "REVIEW_ONLY",
        proposed_system_judgment: null,
        selected_master_history_id: null,
        selected_master_revision_id: null,
        selected_master_payload_sha256: null,
      },
    };
    const replacementApi = createReplacementApi({
      createCandidate: vi.fn().mockResolvedValue(ineligible),
      decide: vi.fn().mockRejectedValue(
        new ResultReplacementApiError(
          "현재 근거로는 대체할 수 없습니다.",
          "RESULT_REPLACEMENT_INELIGIBLE",
          "대체 검토 필요",
        ),
      ),
    });
    const user = userEvent.setup();
    render(
      <HistoricalWorkspacePanel
        batchId="batch-history"
        projectKey="project-a"
        supplierScope="supplier-a"
        batchTerminal
        api={api}
        replacementApi={replacementApi}
      />,
    );

    await loadReplacementComparison(user);
    expect(screen.getByRole("button", { name: "result-predecessor 수정본 후보로 선택" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "result-successor 기존 결과로 선택" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "result-predecessor 기존 결과로 선택" }));
    await user.click(screen.getByRole("button", { name: "result-successor 수정본 후보로 선택" }));
    await user.click(screen.getByRole("button", { name: "대체 근거 다시 계산" }));

    expect(await screen.findByText("SUCCESSOR_REVIEW_REQUIRED")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "기존 REPLACED / 수정본 VALID 원자 확정" })).toBeDisabled();
    expect(screen.queryByLabelText(/actor|role|status|command|Master revision/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /평균|Cpk|Trend|Threshold|AI|Issue|Coverage|초기 DB Gate 완료/ })).not.toBeInTheDocument();
    expect(replacementApi.decide).not.toHaveBeenCalled();
  }, 15_000);
});
