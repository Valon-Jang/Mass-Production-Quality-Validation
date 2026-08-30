import { afterEach, describe, expect, it, vi } from "vitest";

import {
  IntakeApiError,
  intakeApi,
  type IntakeJobSnapshot,
} from "./intake";

const snapshot: IntakeJobSnapshot = {
  job_id: "job-contract",
  project_key: "PROJECT A/B",
  status: "QUEUED",
  status_label: "접수 대기 중",
  message: "접수되었습니다.",
  created_at: "2026-08-15T00:00:00Z",
  updated_at: "2026-08-15T00:00:00Z",
  terminal: false,
  poll_after_ms: 500,
  receipt: null,
  scan: null,
  issues: [],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("manual intake API contract", () => {
  it("정확한 multipart 필드로 작업을 생성한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(snapshot, 202));
    vi.stubGlobal("fetch", fetchMock);
    const workbook = new File(["fixture"], "oqc.xlsm");

    await expect(
      intakeApi.createJob({
        projectKey: "PROJECT A/B",
        workbook,
        modelHint: "MODEL-1",
        lotHint: "LOT-1",
      }),
    ).resolves.toEqual(snapshot);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/intake/jobs");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ Accept: "application/json" });
    const body = init.body as FormData;
    expect(body.get("project_key")).toBe("PROJECT A/B");
    const submittedWorkbook = body.get("workbook") as File;
    expect(submittedWorkbook.name).toBe(workbook.name);
    expect(submittedWorkbook.size).toBe(workbook.size);
    expect(body.get("model_hint")).toBe("MODEL-1");
    expect(body.get("lot_hint")).toBe("LOT-1");
    expect([...body.keys()].sort()).toEqual([
      "lot_hint",
      "model_hint",
      "project_key",
      "workbook",
    ]);
  });

  it("project_key를 query로 인코딩해 같은 프로젝트 작업만 조회한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(snapshot, 200));
    vi.stubGlobal("fetch", fetchMock);

    await intakeApi.getJob("job/contract", "PROJECT A/B");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/intake/jobs/job%2Fcontract?project_key=PROJECT+A%2FB",
      expect.objectContaining({ method: "GET", headers: { Accept: "application/json" } }),
    );
  });

  it("4xx의 검증된 detail 객체만 사용자 오류로 전달한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            detail: {
              code: "INTAKE_CAPACITY_REACHED",
              message: "현재 접수 한도를 초과했습니다.",
              status_label: "접수 한도 초과",
            },
          },
          429,
        ),
      ),
    );

    const promise = intakeApi.createJob({
      projectKey: "PROJECT-A",
      workbook: new File(["fixture"], "oqc.xlsx"),
    });

    await expect(promise).rejects.toMatchObject({
      name: "IntakeApiError",
      code: "INTAKE_CAPACITY_REACHED",
      statusLabel: "접수 한도 초과",
      message: "현재 접수 한도를 초과했습니다.",
    });
  });

  it("5xx 및 잘못된 오류 body에서 서버 원문을 노출하지 않는다", async () => {
    const rawException = "C:\\server\\secret.xlsx API_KEY=do-not-render";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: rawException }, 500)),
    );

    const promise = intakeApi.getJob("job-contract", "PROJECT-A");

    await expect(promise).rejects.toBeInstanceOf(IntakeApiError);
    await expect(promise).rejects.toMatchObject({
      code: "INTAKE_REQUEST_FAILED",
      message: "요청을 처리하지 못했습니다. 다시 시도해 주세요.",
    });
    await promise.catch((error: IntakeApiError) => {
      expect(error.message).not.toContain(rawException);
      expect(error.message).not.toContain("API_KEY");
    });
  });

  it("network 예외를 안전한 일반 오류로 바꾼다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("token=network-secret")));

    await expect(intakeApi.getJob("job-contract", "PROJECT-A")).rejects.toMatchObject({
      code: "INTAKE_REQUEST_FAILED",
      message: "요청을 처리하지 못했습니다. 다시 시도해 주세요.",
    });
  });
});

function jsonResponse(payload: unknown, status: number): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(payload),
  } as unknown as Response;
}
