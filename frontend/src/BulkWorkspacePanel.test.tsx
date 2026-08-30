import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  bulkApi,
  BulkApiError,
  type BulkApi,
  type BulkBatchSnapshot,
  type BulkEntrySnapshot,
} from "./api/bulk";
import { BulkWorkspacePanel } from "./BulkWorkspacePanel";

const sha = (character: string) => character.repeat(64);

const processingEntry = (ordinal: number, filename: string): BulkEntrySnapshot => ({
  entry_id: `entry-${ordinal}`,
  ordinal,
  filename,
  mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  size_bytes: 2048 + ordinal,
  upload_sha256: sha(String(ordinal + 1)),
  status: "PROCESSING",
  outcome: null,
  status_label: "분석 중",
  message: "원본을 보존하고 구조를 비교하고 있습니다.",
  attempt_count: 1,
  row_version: 1,
  receipt: null,
  mapping: null,
  candidate: null,
  duplicate_of_entry_id: null,
  revision_baseline_entry_id: null,
  issues: [],
});

const readyEntry: BulkEntrySnapshot = {
  ...processingEntry(0, "normal.xlsx"),
  upload_sha256: sha("a"),
  status: "TERMINAL",
  outcome: "CANDIDATE_READY",
  status_label: "후보 준비",
  message: "승인 Mapping과 동일한 구조입니다.",
  row_version: 2,
  receipt: {
    receipt_id: "receipt-normal",
    content_sha256: sha("a"),
    original_filename: "normal.xlsx",
    received_at: "2026-08-15T13:00:00+09:00",
    size_bytes: 2048,
  },
  mapping: {
    template_id: "template-oqc",
    revision: 1,
    template_sha256: sha("b"),
    effective_from: "2026-01-01",
    effective_to: null,
    history_row_version: 3,
    revision_row_version: 2,
  },
  candidate: {
    state: "LOAD_CANDIDATE_READY",
    candidate_digest: sha("c"),
    loadable_row_count: 4,
    held_row_count: 0,
    revision_identity_sha256: sha("d"),
    revision_evidence_sha256: sha("e"),
  },
};

const revisionEntry: BulkEntrySnapshot = {
  ...processingEntry(1, "changed.xlsm"),
  upload_sha256: sha("f"),
  status: "TERMINAL",
  outcome: "REVISION_REVIEW_REQUIRED",
  status_label: "수정본 검토 필요",
  message: "동일 LOT의 품질판단 영향값이 달라졌습니다.",
  row_version: 2,
  receipt: {
    receipt_id: "receipt-changed",
    content_sha256: sha("f"),
    original_filename: "changed.xlsm",
    received_at: "2026-08-15T13:01:00+09:00",
    size_bytes: 2049,
  },
  mapping: readyEntry.mapping,
  candidate: {
    state: "PARTIAL_HOLD",
    candidate_digest: sha("1"),
    loadable_row_count: 3,
    held_row_count: 1,
    revision_identity_sha256: sha("2"),
    revision_evidence_sha256: sha("3"),
  },
  revision_baseline_entry_id: "entry-0",
  issues: [
    {
      code: "QUALITY_VALUE_CHANGED",
      category: "REVISION",
      severity: "BLOCKING",
      message: "NG에서 PASS 방향으로 원본 측정값이 변경되었습니다.",
      location: "OQC!D14",
      evidence_path: "worksheets/OQC/D14",
      baseline_entry_id: "entry-0",
      expected_json: { raw: "10.8", judgment: "NG" },
      observed_json: { raw: "9.8", judgment: "PASS" },
    },
  ],
};

const capabilities = {
  durable_staging: true,
  approved_template_reuse: true,
  per_file_approval: false,
  finalize_available: false,
  auto_long: false,
  auto_valid: false,
  auto_replaced: false,
  auto_revision: false,
  ai_used: false,
};

function snapshot(
  status: BulkBatchSnapshot["status"],
  entries: BulkEntrySnapshot[],
  overrides: Partial<BulkBatchSnapshot> = {},
): BulkBatchSnapshot {
  const terminal = status === "COMPLETED" || status === "COMPLETED_WITH_EXCEPTIONS" || status === "FAILED";
  return {
    batch_id: "batch-001",
    project_key: "project-a",
    supplier_scope: "supplier-a",
    idempotency_key: "idem-001",
    status,
    status_label: terminal ? "예외 포함 분석 완료" : "일괄 분석 중",
    message: terminal ? "정상 후보와 검토 예외를 분리했습니다." : "파일을 순서대로 분석하고 있습니다.",
    created_at: "2026-08-15T13:00:00+09:00",
    updated_at: "2026-08-15T13:02:00+09:00",
    finished_at: terminal ? "2026-08-15T13:02:00+09:00" : null,
    terminal,
    poll_after_ms: terminal ? null : 1,
    replayed: false,
    limits: { max_files: 100, max_file_bytes: 20_000_000, max_batch_bytes: 500_000_000 },
    summary: {
      total: entries.length,
      staged: status === "STAGED" ? entries.length : 0,
      processing: status === "PROCESSING" ? entries.length : 0,
      candidate_ready: entries.filter((entry) => entry.outcome === "CANDIDATE_READY").length,
      duplicate: entries.filter((entry) => entry.outcome === "DUPLICATE_CANDIDATE").length,
      variation: entries.filter((entry) => entry.outcome === "VARIATION_REVIEW_REQUIRED").length,
      mapping_required: entries.filter((entry) => entry.outcome === "MAPPING_REQUIRED").length,
      scan_failed: entries.filter((entry) => entry.outcome === "SCAN_FAILED").length,
      identifier_hold: entries.filter((entry) => entry.outcome === "IDENTIFIER_HOLD").length,
      binding_hold: entries.filter((entry) => entry.outcome === "BINDING_HOLD").length,
      revision_review_required: entries.filter((entry) => entry.outcome === "REVISION_REVIEW_REQUIRED").length,
      error: entries.filter((entry) => entry.outcome === "ERROR").length,
    },
    entries,
    capabilities,
    ...overrides,
  };
}

function createApi(initial: BulkBatchSnapshot, polled = initial): BulkApi {
  return {
    createBatch: vi.fn().mockResolvedValue(initial),
    getBatch: vi.fn().mockResolvedValue(polled),
  };
}

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("BulkWorkspacePanel", () => {
  it("DQ-P2-BULKUI-001 다중 원본을 한 배치로 제출하고 polling 결과·원본·Mapping 근거를 표시한다", async () => {
    const api = createApi(
      snapshot("PROCESSING", [processingEntry(0, "normal.xlsx"), processingEntry(1, "changed.xlsm")]),
      snapshot("COMPLETED_WITH_EXCEPTIONS", [readyEntry, revisionEntry]),
    );
    const user = userEvent.setup();
    render(<BulkWorkspacePanel projectKey="project-a" supplierScope="supplier-a" api={api} />);

    const files = [
      new File(["normal"], "normal.xlsx", { type: readyEntry.mime_type }),
      new File(["changed"], "changed.xlsm", { type: "application/vnd.ms-excel.sheet.macroEnabled.12" }),
    ];
    await user.upload(screen.getByLabelText(/과거 OQC 원본 파일/), files);
    expect(screen.getByText("선택한 파일 2개")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "원본 2개 보존 및 일괄 분석" }));

    await waitFor(() => expect(api.getBatch).toHaveBeenCalledWith("batch-001", "project-a", expect.any(AbortSignal)), { timeout: 2000 });
    expect(api.createBatch).toHaveBeenCalledWith(
      expect.objectContaining({
        projectKey: "project-a",
        supplierScope: "supplier-a",
        workbooks: files,
        idempotencyKey: expect.any(String),
      }),
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("정상 후보와 검토 예외를 분리했습니다.")).toBeInTheDocument();
    const summary = screen.getByLabelText("대량 분석 요약");
    expect(within(summary).getByText("후보 준비")).toBeInTheDocument();
    expect(within(summary).getByText("수정본 검토 필요")).toBeInTheDocument();

    const readyCard = screen.getByText("normal.xlsx", { selector: "h6" }).closest("article");
    expect(readyCard).not.toBeNull();
    await user.click(within(readyCard as HTMLElement).getByText("원본·후보 근거 보기"));
    expect(within(readyCard as HTMLElement).getByText("receipt-normal")).toBeInTheDocument();
    expect(within(readyCard as HTMLElement).getByText("template-oqc · rev.1")).toBeInTheDocument();
    expect(within(readyCard as HTMLElement).getByText(sha("c"))).toBeInTheDocument();
    expect(screen.getByText("1", { selector: ".bulk-entry__ordinal" })).toBeInTheDocument();
    expect(screen.getByText("2", { selector: ".bulk-entry__ordinal" })).toBeInTheDocument();
  });

  it("DQ-P2-BULKUI-002 typed 수정본 예외를 drill-down하고 개별 승인·자동 상태 버튼을 만들지 않는다", async () => {
    const api = createApi(snapshot("COMPLETED_WITH_EXCEPTIONS", [revisionEntry]));
    const user = userEvent.setup();
    render(<BulkWorkspacePanel projectKey="project-a" supplierScope="supplier-a" api={api} />);
    await user.upload(
      screen.getByLabelText(/과거 OQC 원본 파일/),
      new File(["changed"], "changed.xlsm", { type: "application/vnd.ms-excel.sheet.macroEnabled.12" }),
    );
    await user.click(screen.getByRole("button", { name: "원본 1개 보존 및 일괄 분석" }));
    await user.click(await screen.findByText("예외 근거 보기 (1)"));

    expect(screen.getByText("QUALITY_VALUE_CHANGED")).toBeInTheDocument();
    expect(screen.getByText("OQC!D14")).toBeInTheDocument();
    expect(screen.getByText("worksheets/OQC/D14")).toBeInTheDocument();
    expect(screen.getByText('{"raw":"10.8","judgment":"NG"}')).toBeInTheDocument();
    expect(screen.getByText('{"raw":"9.8","judgment":"PASS"}')).toBeInTheDocument();
    expect(screen.getByText(/정상 파일은 개별 승인하지 않습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /개별 승인|VALID|REPLACED|수정본 승인/ })).not.toBeInTheDocument();
  });

  it("DQ-P2-BULKUI-003 저장된 durable batch ID를 조회하고 project·supplier 불일치는 fail-closed한다", async () => {
    window.localStorage.setItem("mass-production-quality-validation:bulk:project-a:supplier-a", "batch-restart");
    const correct = snapshot("COMPLETED", [readyEntry], { batch_id: "batch-restart" });
    const api: BulkApi = {
      createBatch: vi.fn(),
      getBatch: vi.fn()
        .mockResolvedValueOnce(correct)
        .mockResolvedValueOnce({ ...correct, batch_id: "batch-wrong", project_key: "project-b" }),
    };
    const user = userEvent.setup();
    render(<BulkWorkspacePanel projectKey="project-a" supplierScope="supplier-a" api={api} />);

    const input = screen.getByLabelText("배치 ID");
    expect(input).toHaveValue("batch-restart");
    await user.click(screen.getByRole("button", { name: "배치 조회" }));
    expect(await screen.findByText("batch-restart")).toBeInTheDocument();
    expect(api.getBatch).toHaveBeenCalledWith("batch-restart", "project-a", expect.any(AbortSignal));

    await user.clear(input);
    await user.type(input, "batch-wrong");
    await user.click(screen.getByRole("button", { name: "배치 조회" }));
    expect(await screen.findByText("조회된 배치의 Project 또는 Supplier scope가 현재 화면과 일치하지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByText("batch-wrong")).not.toBeInTheDocument();
  });

  it("scope 변경 뒤 늦게 도착한 이전 project 응답을 화면 상태에 반영하지 않는다", async () => {
    let resolveCreate: ((value: BulkBatchSnapshot) => void) | undefined;
    const pendingCreate = new Promise<BulkBatchSnapshot>((resolve) => {
      resolveCreate = resolve;
    });
    const api: BulkApi = {
      createBatch: vi.fn().mockReturnValue(pendingCreate),
      getBatch: vi.fn(),
    };
    const user = userEvent.setup();
    const { rerender } = render(
      <BulkWorkspacePanel projectKey="project-a" supplierScope="supplier-a" api={api} />,
    );

    await user.upload(
      screen.getByLabelText(/과거 OQC 원본 파일/),
      new File(["normal"], "normal.xlsx", { type: readyEntry.mime_type }),
    );
    await user.click(screen.getByRole("button", { name: "원본 1개 보존 및 일괄 분석" }));

    rerender(
      <BulkWorkspacePanel projectKey="project-b" supplierScope="supplier-b" api={api} />,
    );
    resolveCreate?.(snapshot("COMPLETED", [readyEntry], { batch_id: "batch-old" }));

    await waitFor(() => expect(screen.getByText("project-b")).toBeInTheDocument());
    expect(screen.queryByText("batch-old")).not.toBeInTheDocument();
    expect(screen.queryByText("정상 후보와 검토 예외를 분리했습니다.")).not.toBeInTheDocument();
  });

  it("DQ-P2-BULKUI-004 exact multipart와 안전한 한글 4xx·5xx 오류 경계를 지킨다", async () => {
    const terminal = snapshot("COMPLETED", [readyEntry]);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(terminal), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: { code: "BULK_CAPACITY_REACHED", message: "현재 처리 가능한 배치 수를 초과했습니다.", status_label: "처리 용량 초과" } }), { status: 429, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response("internal path C:\\secret", { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);
    const files = [new File(["a"], "a.xlsx"), new File(["b"], "b.xlsm")];

    await bulkApi.createBatch({ projectKey: "project-a", supplierScope: "supplier-a", idempotencyKey: "idem-fixed", workbooks: files });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/bulk/batches");
    expect(init.method).toBe("POST");
    const body = init.body as FormData;
    expect(body.get("project_key")).toBe("project-a");
    expect(body.get("supplier_scope")).toBe("supplier-a");
    expect(body.get("idempotency_key")).toBe("idem-fixed");
    expect(body.getAll("workbooks")).toHaveLength(2);

    await expect(bulkApi.getBatch("batch-001", "project-a")).rejects.toMatchObject({
      name: "BulkApiError",
      code: "BULK_CAPACITY_REACHED",
      message: "현재 처리 가능한 배치 수를 초과했습니다.",
      statusLabel: "처리 용량 초과",
    } satisfies Partial<BulkApiError>);
    await expect(bulkApi.getBatch("batch-001", "project-a")).rejects.toMatchObject({
      message: "요청을 처리하지 못했습니다. 다시 시도해 주세요.",
    });
  });
});
