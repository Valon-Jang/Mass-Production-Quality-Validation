import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import {
  IntakeApiError,
  type IntakeApi,
  type IntakeJobSnapshot,
} from "./api/intake";

const queuedJob: IntakeJobSnapshot = {
  job_id: "job-001",
  project_key: "PROJECT-A",
  status: "QUEUED",
  status_label: "접수 대기 중",
  message: "원본 보존 작업을 기다리고 있습니다.",
  created_at: "2026-08-15T09:00:00+09:00",
  updated_at: "2026-08-15T09:00:00+09:00",
  terminal: false,
  poll_after_ms: 1,
  receipt: null,
  scan: null,
  issues: [],
};

const processingJob: IntakeJobSnapshot = {
  ...queuedJob,
  status: "PROCESSING",
  status_label: "구조 스캔 중",
  message: "보존된 원본의 구조를 확인하고 있습니다.",
  updated_at: "2026-08-15T09:00:01+09:00",
};

const mappedJob: IntakeJobSnapshot = {
  ...processingJob,
  status: "MAPPING_REQUIRED",
  status_label: "매핑 검토 필요",
  message: "원본과 스캔 근거를 보존했습니다. 매핑을 검토해 주세요.",
  updated_at: "2026-08-15T09:00:02+09:00",
  terminal: true,
  poll_after_ms: null,
  receipt: {
    receipt_id: "receipt-001",
    content_sha256: "a".repeat(64),
    original_filename: "supplier_oqc.xlsx",
    received_at: "2026-08-15T09:00:00+09:00",
    size_bytes: 2048,
    model_candidates: ["MODEL-100"],
    lot_candidates: ["LOT-20260815"],
  },
  scan: {
    source_size_bytes: 2048,
    sha256_before: "a".repeat(64),
    sha256_after: "a".repeat(64),
    sheet_count: 2,
    sheets: [
      {
        name: "OQC 결과",
        kind: "WORKSHEET",
        state: "VISIBLE",
        used_range: "A1:H32",
        merged_ranges: ["A1:H2"],
        protected: true,
        issue_codes: ["MERGED_HEADER"],
      },
      {
        name: "기준",
        kind: "WORKSHEET",
        state: "HIDDEN",
        used_range: "A1:D8",
        merged_ranges: [],
        protected: false,
        issue_codes: [],
      },
    ],
  },
  issues: [
    {
      code: "MERGED_HEADER",
      message: "병합된 머리글을 확인해 주세요.",
      location: "OQC 결과!A1:H2",
    },
  ],
};

function createApi(overrides: Partial<IntakeApi> = {}): IntakeApi {
  return {
    createJob: vi.fn().mockResolvedValue(queuedJob),
    getJob: vi.fn().mockResolvedValue(mappedJob),
    ...overrides,
  };
}

async function submitWorkbook(api: IntakeApi, filename = "supplier_oqc.xlsx") {
  const user = userEvent.setup();
  render(<App api={api} />);
  await user.type(screen.getByLabelText(/프로젝트 키/), "  PROJECT-A  ");
  await user.type(screen.getByLabelText("모델 힌트"), "  MODEL-100  ");
  await user.type(screen.getByLabelText("LOT 힌트"), "  LOT-20260815  ");
  const workbook = new File(["fixture"], filename, {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  await user.upload(screen.getByLabelText(/OQC 원본 파일/), workbook);
  await user.click(screen.getByRole("button", { name: "원본 보존 및 스캔 시작" }));
  return workbook;
}

describe("Mass Production Quality Validation 수동 원본 접수 화면", () => {
  it("한글 안내, 입력 label, live region과 비공식 처리 경계를 제공한다", () => {
    render(<App api={createApi()} />);

    expect(screen.getAllByText("Mass Production Quality Validation").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "OQC 원본을 안전하게 접수하세요" })).toBeVisible();
    expect(screen.getByLabelText(/프로젝트 키/)).toBeRequired();
    expect(screen.getByLabelText(/OQC 원본 파일/)).toHaveAttribute("accept", ".xlsx,.xlsm");
    expect(screen.getByRole("button", { name: "파일 찾아보기" })).toBeEnabled();
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByLabelText("현재 기능 범위")).toHaveTextContent(
      "매핑 검토 전에는 공식 데이터로 등록되지 않으며 품질 결과를 확정하지 않습니다.",
    );

    const renderedText = document.body.textContent ?? "";
    for (const prohibited of ["PA" + "SS", "FA" + "IL", "VA" + "LID", "C" + "pk"]) {
      expect(renderedText).not.toContain(prohibited);
    }
  });

  it("xlsx를 선택하고 정리된 프로젝트와 선택 힌트로 접수한다", async () => {
    const createJob = vi.fn().mockResolvedValue(queuedJob);
    const api = createApi({ createJob });
    const workbook = await submitWorkbook(api);

    await waitFor(() => expect(createJob).toHaveBeenCalledTimes(1));
    expect(createJob).toHaveBeenCalledWith({
      projectKey: "PROJECT-A",
      workbook,
      modelHint: "MODEL-100",
      lotHint: "LOT-20260815",
    });
    expect(await screen.findByText("접수 대기 중")).toBeVisible();
  });

  it("대기와 처리 상태를 polling한 뒤 보존·스캔 근거를 표시한다", async () => {
    const getJob = vi
      .fn()
      .mockResolvedValueOnce(processingJob)
      .mockResolvedValueOnce(mappedJob);
    const api = createApi({ getJob });
    await submitWorkbook(api);

    expect(await screen.findByText("접수 대기 중")).toBeVisible();
    expect(await screen.findByText("구조 스캔 중", {}, { timeout: 1_500 })).toBeVisible();
    expect(await screen.findByText("매핑 검토 필요", {}, { timeout: 1_500 })).toBeVisible();

    expect(getJob).toHaveBeenNthCalledWith(1, "job-001", "PROJECT-A", expect.any(AbortSignal));
    expect(getJob).toHaveBeenNthCalledWith(2, "job-001", "PROJECT-A", expect.any(AbortSignal));
    expect(screen.getAllByText("supplier_oqc.xlsx").length).toBe(2);
    expect(screen.getAllByText("a".repeat(64)).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("MODEL-100")).toBeVisible();
    expect(screen.getByText("LOT-20260815")).toBeVisible();
    expect(screen.getByText("OQC 결과")).toBeVisible();
    expect(screen.getByText("A1:H32")).toBeVisible();
    expect(screen.getByText("A1:H2")).toBeVisible();
    expect(screen.getByText("보호됨")).toBeVisible();
    expect(screen.getByText("숨김")).toBeVisible();
    expect(screen.getByText("병합된 머리글을 확인해 주세요. · OQC 결과!A1:H2")).toBeVisible();
    expect(screen.getByLabelText("업체 범위")).toBeRequired();
    expect(screen.getByText(/업체 범위는 원본 후보에서 추론하지 않습니다/)).toBeVisible();
    expect(screen.getByRole("button", { name: "원본 셀 검토 시작" })).toBeEnabled();
  });

  it("원본 보존 후 스캔 실패를 근거와 함께 terminal 상태로 유지한다", async () => {
    const failedJob: IntakeJobSnapshot = {
      ...mappedJob,
      status: "RAW_PRESERVED_SCAN_FAILED",
      status_label: "원본 보존됨 · 스캔 실패",
      message: "원본은 보존했지만 구조 스캔을 마치지 못했습니다.",
      scan: null,
      issues: [
        {
          code: "WORKBOOK_SCAN_FAILED",
          message: "통합 문서 구조를 읽지 못했습니다.",
          location: "xl/workbook.xml",
        },
      ],
    };
    const api = createApi({ createJob: vi.fn().mockResolvedValue(failedJob) });
    await submitWorkbook(api, "macro_source.xlsm");

    expect(await screen.findByText("원본 보존됨 · 스캔 실패")).toBeVisible();
    expect(screen.getByText("macro_source.xlsm")).toBeVisible();
    expect(screen.getByText("통합 문서 구조를 읽지 못했습니다. · xl/workbook.xml")).toBeVisible();
  });

  it("용량 제한의 안정된 오류 상세를 표시한다", async () => {
    const api = createApi({
      createJob: vi.fn().mockRejectedValue(
        new IntakeApiError(
          "현재 처리 가능한 접수 수를 초과했습니다.",
          "INTAKE_CAPACITY_REACHED",
          "접수 한도 초과",
        ),
      ),
    });
    await submitWorkbook(api);

    expect(await screen.findByRole("alert")).toHaveTextContent("접수 한도 초과");
    expect(screen.getByRole("alert")).toHaveTextContent("현재 처리 가능한 접수 수를 초과했습니다.");
    expect(screen.getByText("INTAKE_CAPACITY_REACHED")).toBeVisible();
  });

  it("근거가 없는 terminal 작업 오류에서도 새 원본 접수 경로를 유지한다", async () => {
    const errorJob: IntakeJobSnapshot = {
      ...queuedJob,
      status: "ERROR",
      status_label: "접수 처리 오류",
      message: "작업을 계속할 수 없습니다. 새 원본으로 다시 시도해 주세요.",
      terminal: true,
      poll_after_ms: null,
    };
    const api = createApi({ createJob: vi.fn().mockResolvedValue(errorJob) });
    await submitWorkbook(api);

    expect(await screen.findByText("접수 처리 오류")).toBeVisible();
    expect(screen.getByRole("button", { name: "새 원본 접수" })).toBeEnabled();
  });

  it("알 수 없는 예외의 경로와 비밀값은 숨기고 안전 문구만 표시한다", async () => {
    const rawException = "C:\\private\\oqc.xlsx token=super-secret stack trace";
    const api = createApi({ createJob: vi.fn().mockRejectedValue(new Error(rawException)) });
    await submitWorkbook(api);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "요청을 처리하지 못했습니다. 다시 시도해 주세요.",
    );
    expect(document.body).not.toHaveTextContent(rawException);
    expect(document.body).not.toHaveTextContent("super-secret");
  });

  it("지원하지 않는 파일은 API 요청 전에 거부하고 drop 선택도 지원한다", async () => {
    const createJob = vi.fn().mockResolvedValue(queuedJob);
    const api = createApi({ createJob });
    render(<App api={api} />);

    const fileInput = screen.getByLabelText(/OQC 원본 파일/);
    fireEvent.change(fileInput, { target: { files: [new File(["x"], "report.csv")] } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      ".xlsx 또는 .xlsm 형식의 OQC 원본만 선택할 수 있습니다.",
    );
    expect(createJob).not.toHaveBeenCalled();

    const dropzone = screen.getByText("파일을 여기에 놓으세요").closest(".dropzone");
    expect(dropzone).not.toBeNull();
    const dropped = new File(["x"], "dropped.xlsm");
    fireEvent.drop(dropzone as Element, { dataTransfer: { files: [dropped] } });
    expect(screen.getByText("dropped.xlsm")).toBeVisible();
  });
});
