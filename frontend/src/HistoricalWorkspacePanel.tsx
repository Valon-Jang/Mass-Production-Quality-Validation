import { type FormEvent, useEffect, useRef, useState } from "react";

import {
  historyApi,
  HistoryApiError,
  type BulkFinalizationCandidate,
  type BulkFinalizationSnapshot,
  type HistoricalComparisonInput,
  type HistoricalComparisonResponse,
  type HistoricalDataStatus,
  type HistoricalResult,
  type HistoryApi,
} from "./api/history";
import {
  resultReplacementApi,
  ResultReplacementApiError,
  type ReplacementCandidate,
  type ReplacementDecision,
  type ResultReplacementApi,
} from "./api/replacement";

const DATA_STATUSES: HistoricalDataStatus[] = [
  "PENDING",
  "HELD",
  "VALID",
  "SUSPECT",
  "EXCLUDED",
  "REPLACED",
];
const SAFE_ERROR = "요청을 처리하지 못했습니다. 다시 시도해 주세요.";

interface HistoricalWorkspacePanelProps {
  batchId: string;
  projectKey: string;
  supplierScope: string;
  batchTerminal: boolean;
  api?: HistoryApi;
  replacementApi?: ResultReplacementApi;
}

interface DisplayError {
  title: string;
  message: string;
  code: string;
}

export function HistoricalWorkspacePanel({
  batchId,
  projectKey,
  supplierScope,
  batchTerminal,
  api = historyApi,
  replacementApi = resultReplacementApi,
}: HistoricalWorkspacePanelProps) {
  const [candidate, setCandidate] = useState<BulkFinalizationCandidate | null>(null);
  const [finalization, setFinalization] = useState<BulkFinalizationSnapshot | null>(null);
  const [comparison, setComparison] = useState<HistoricalComparisonResponse | null>(null);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [selectedStatuses, setSelectedStatuses] = useState<HistoricalDataStatus[]>([]);
  const [leftFrom, setLeftFrom] = useState("");
  const [leftTo, setLeftTo] = useState("");
  const [rightFrom, setRightFrom] = useState("");
  const [rightTo, setRightTo] = useState("");
  const [modelKey, setModelKey] = useState("");
  const [partKey, setPartKey] = useState("");
  const [itemKey, setItemKey] = useState("");
  const [supplierKey, setSupplierKey] = useState("");
  const [mappingRevisionId, setMappingRevisionId] = useState("");
  const [predecessorResultId, setPredecessorResultId] = useState("");
  const [successorResultId, setSuccessorResultId] = useState("");
  const [replacementCandidate, setReplacementCandidate] =
    useState<ReplacementCandidate | null>(null);
  const [replacementDecision, setReplacementDecision] =
    useState<ReplacementDecision | null>(null);
  const [replacementReason, setReplacementReason] = useState("");
  const [replacementConfirmed, setReplacementConfirmed] = useState(false);
  const [replacementLookupId, setReplacementLookupId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<DisplayError | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);

  useEffect(() => {
    generationRef.current += 1;
    controllerRef.current?.abort();
    setCandidate(null);
    setFinalization(null);
    setComparison(null);
    setReason("");
    setConfirmed(false);
    setSelectedStatuses([]);
    setPredecessorResultId("");
    setSuccessorResultId("");
    setReplacementCandidate(null);
    setReplacementDecision(null);
    setReplacementReason("");
    setReplacementConfirmed(false);
    setReplacementLookupId("");
    setError(null);
    setFormError(null);
    setBusy(false);
  }, [batchId, projectKey, supplierScope]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  useEffect(() => {
    if (!finalization || finalization.terminal || error) return undefined;
    const controller = new AbortController();
    const generation = generationRef.current;
    const timer = window.setTimeout(() => {
      void api
        .getFinalization(batchId, projectKey, controller.signal)
        .then((snapshot) => {
          if (controller.signal.aborted || generation !== generationRef.current) return;
          if (!validFinalizationScope(snapshot, batchId, projectKey, supplierScope)) {
            setError(scopeError("정상 후보 반영 상태 오류"));
            setFinalization(null);
            return;
          }
          setFinalization(snapshot);
        })
        .catch((cause: unknown) => {
          if (!isAbortError(cause) && generation === generationRef.current) {
            setError(toDisplayError(cause, "정상 후보 반영 상태 오류"));
          }
        });
    }, Math.max(250, finalization.poll_after_ms ?? 500));
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [api, batchId, error, finalization, projectKey, supplierScope]);

  const run = async <T,>(
    operation: (signal: AbortSignal) => Promise<T>,
    accept: (value: T) => boolean,
    title: string,
  ) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    const generation = generationRef.current;
    controllerRef.current = controller;
    setBusy(true);
    setError(null);
    setFormError(null);
    try {
      const value = await operation(controller.signal);
      if (controller.signal.aborted || generation !== generationRef.current) return;
      accept(value);
    } catch (cause) {
      if (!isAbortError(cause) && generation === generationRef.current) {
        setError(toDisplayError(cause, title));
      }
    } finally {
      if (controllerRef.current === controller && generation === generationRef.current) {
        setBusy(false);
      }
    }
  };

  const loadCandidate = async () => {
    if (!batchTerminal) {
      setFormError("일괄 분석이 끝난 뒤 정상 후보 Long 반영 근거를 확인할 수 있습니다.");
      return;
    }
    setConfirmed(false);
    setReason("");
    await run(
      (signal) => api.getFinalizationCandidate(batchId, projectKey, signal),
      (value) => {
        if (!validCandidateScope(value, batchId, projectKey, supplierScope)) {
          setError(scopeError("정상 후보 반영 준비 오류"));
          setCandidate(null);
          return false;
        }
        setCandidate(value);
        setFinalization(null);
        return true;
      },
      "정상 후보 반영 준비 오류",
    );
  };

  const createFinalization = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!candidate?.can_finalize) {
      setFormError("확정 가능한 정상 후보가 없습니다.");
      return;
    }
    if (!confirmed || !reason.trim()) {
      setFormError("전체 정상 후보와 예외 제외 범위를 확인하고 사유를 입력해 주세요.");
      return;
    }
    await run(
      (signal) =>
        api.createFinalization(
          {
            projectKey,
            batchId,
            finalizationDigest: candidate.finalization_digest,
            reason: reason.trim(),
          },
          signal,
        ),
      (value) => {
        if (!validFinalizationScope(value, batchId, projectKey, supplierScope)) {
          setError(scopeError("정상 후보 반영 요청 오류"));
          setFinalization(null);
          return false;
        }
        setFinalization(value);
        setConfirmed(false);
        return true;
      },
      "정상 후보 반영 요청 오류",
    );
  };

  const loadFinalization = async () => {
    await run(
      (signal) => api.getFinalization(batchId, projectKey, signal),
      (value) => {
        if (!validFinalizationScope(value, batchId, projectKey, supplierScope)) {
          setError(scopeError("정상 후보 반영 상태 오류"));
          setFinalization(null);
          return false;
        }
        setFinalization(value);
        return true;
      },
      "정상 후보 반영 상태 오류",
    );
  };

  const compare = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!leftFrom || !leftTo || !rightFrom || !rightTo) {
      setFormError("비교할 두 기간의 시작일과 종료일을 모두 선택해 주세요.");
      return;
    }
    if (selectedStatuses.length === 0) {
      setFormError("조회할 데이터 상태를 하나 이상 명시적으로 선택해 주세요.");
      return;
    }
    const input: HistoricalComparisonInput = {
      project_key: projectKey,
      left: { date_from: leftFrom, date_to: leftTo },
      right: { date_from: rightFrom, date_to: rightTo },
      data_statuses: selectedStatuses,
      filters: compactFilters({
        canonical_model_key: modelKey,
        canonical_model_part_key: partKey,
        canonical_item_key: itemKey,
        canonical_supplier_key: supplierKey,
        mapping_revision_id: mappingRevisionId,
      }),
      limit_per_side: 100,
    };
    resetReplacementReview();
    setPredecessorResultId("");
    setSuccessorResultId("");
    await run(
      (signal) => api.compare(input, signal),
      (value) => {
        if (value.project_key !== projectKey) {
          setError(scopeError("과거 데이터 비교 오류"));
          setComparison(null);
          return false;
        }
        setComparison(value);
        return true;
      },
      "과거 데이터 비교 오류",
    );
  };

  const resetReplacementReview = () => {
    setReplacementCandidate(null);
    setReplacementDecision(null);
    setReplacementReason("");
    setReplacementConfirmed(false);
  };

  const selectPredecessor = (resultId: string) => {
    setPredecessorResultId(resultId);
    resetReplacementReview();
  };

  const selectSuccessor = (resultId: string) => {
    setSuccessorResultId(resultId);
    resetReplacementReview();
  };

  const loadReplacementCandidate = async () => {
    if (!predecessorResultId || !successorResultId) {
      setFormError("기존 결과와 수정본 후보를 비교 결과에서 각각 선택해 주세요.");
      return;
    }
    setReplacementConfirmed(false);
    setReplacementReason("");
    setReplacementDecision(null);
    await run(
      (signal) =>
        replacementApi.createCandidate(
          {
            project_key: projectKey,
            predecessor_result_id: predecessorResultId,
            successor_result_id: successorResultId,
          },
          signal,
        ),
      (value) => {
        if (
          value.project_key !== projectKey ||
          value.predecessor.result_id !== predecessorResultId ||
          value.successor.result_id !== successorResultId
        ) {
          setError(replacementScopeError("수정본 대체 근거 오류"));
          setReplacementCandidate(null);
          return false;
        }
        setReplacementCandidate(value);
        return true;
      },
      "수정본 대체 근거 오류",
    );
  };

  const decideReplacement = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!replacementCandidate?.can_replace) {
      setFormError("현재 근거로는 수정본 대체를 확정할 수 없습니다.");
      return;
    }
    if (!replacementConfirmed || !replacementReason.trim()) {
      setFormError("두 결과와 위험 변화 근거를 확인하고 대체 사유를 입력해 주세요.");
      return;
    }
    const candidate = replacementCandidate;
    await run(
      (signal) =>
        replacementApi.decide(
          {
            project_key: projectKey,
            predecessor_result_id: candidate.predecessor.result_id,
            successor_result_id: candidate.successor.result_id,
            candidate_sha256: candidate.candidate_sha256,
            expected_predecessor_result_row_version: candidate.predecessor.row_version,
            expected_successor_result_row_version: candidate.successor.row_version,
            expected_predecessor_measurement_set_sha256:
              candidate.predecessor.measurement_set_sha256,
            expected_successor_measurement_set_sha256:
              candidate.successor.measurement_set_sha256,
            expected_predecessor_decision_transition_id:
              candidate.predecessor.original_data_status_transition_id,
            expected_successor_data_review_candidate_sha256:
              candidate.successor.data_review_candidate_sha256,
            confirmed: true,
            reason: replacementReason.trim(),
          },
          signal,
        ),
      (value) => {
        if (!validReplacementDecisionScope(value, projectKey, candidate)) {
          setError(replacementScopeError("수정본 대체 결정 오류"));
          setReplacementDecision(null);
          return false;
        }
        setReplacementDecision(value);
        setReplacementLookupId(value.replacement_id);
        setReplacementConfirmed(false);
        return true;
      },
      "수정본 대체 결정 오류",
    );
  };

  const loadReplacementDecision = async () => {
    const replacementId = replacementLookupId.trim();
    if (!replacementId) {
      setFormError("조회할 대체 이력 ID를 입력해 주세요.");
      return;
    }
    setReplacementConfirmed(false);
    setReplacementReason("");
    await run(
      (signal) => replacementApi.getDecision(replacementId, projectKey, signal),
      (value) => {
        if (value.project_key !== projectKey || value.replacement_id !== replacementId) {
          setError(replacementScopeError("수정본 대체 이력 조회 오류"));
          setReplacementDecision(null);
          return false;
        }
        setPredecessorResultId(value.predecessor_result_id);
        setSuccessorResultId(value.successor_result_id);
        setReplacementCandidate(null);
        setReplacementDecision(value);
        return true;
      },
      "수정본 대체 이력 조회 오류",
    );
  };

  const toggleStatus = (status: HistoricalDataStatus) => {
    setSelectedStatuses((current) =>
      current.includes(status)
        ? current.filter((item) => item !== status)
        : [...current, status],
    );
  };

  return (
    <section className="history-workspace" aria-labelledby="history-workspace-heading" aria-busy={busy}>
      <div className="section-heading">
        <div>
          <p className="panel__kicker">HISTORICAL DB</p>
          <h5 id="history-workspace-heading">과거 정상 후보 반영 및 근거 비교</h5>
        </div>
        <span className="section-count">PHASE 2</span>
      </div>

      <div className="mapping-limit-note history-boundary">
        정상 후보 전체를 한 번에 명시적으로 확정합니다. 예외 파일은 제외되고, 저장 결과는
        PENDING 또는 HELD뿐입니다. VALID·REPLACED·평균·Cpk·Trend·현재 Master 재판정은
        자동 실행하지 않습니다. Worker 완료는 정상 후보 반영 완료일 뿐, 예외 해결과 실제
        Golden 대조가 필요한 초기 DB Gate 완료를 뜻하지 않습니다.
      </div>

      <div className="history-actions">
        <button className="secondary-button" type="button" onClick={() => void loadCandidate()} disabled={busy || !batchTerminal}>
          정상 후보 반영 근거 확인
        </button>
        <button className="secondary-button" type="button" onClick={() => void loadFinalization()} disabled={busy || !batchTerminal}>
          기존 반영 상태 조회
        </button>
      </div>

      {candidate ? (
        <div className="history-candidate">
          <dl className="evidence-grid">
            <Evidence label="Finalization digest" value={candidate.finalization_digest} wide />
            <Evidence label="Batch row_version" value={String(candidate.batch_row_version)} />
            <Evidence label="정상 후보" value={String(candidate.eligible_count)} />
            <Evidence label="제외 예외" value={String(candidate.excluded_count)} />
          </dl>
          <div className="history-partitions">
            <EntryPartition title="확정 대상 전체" entries={candidate.eligible_entries.map((entry) => ({
              key: entry.entry_id,
              ordinal: entry.ordinal,
              filename: entry.filename,
              detail: `Receipt ${entry.receipt_id} · Long ${shortHash(entry.long_candidate_digest)} · 준비근거 ${shortHash(entry.prepared_checkpoint_sha256)} / ${entry.prepared_checkpoint_bytes} bytes`,
            }))} />
            <EntryPartition title="자동 제외 예외" entries={candidate.excluded_entries.map((entry) => ({
              key: entry.entry_id,
              ordinal: entry.ordinal,
              filename: entry.filename,
              detail: `${entry.outcome} · ${entry.status_code} · 근거 ${shortHash(entry.issues_sha256)} · Receipt ${entry.receipt_id ?? "없음"}`,
            }))} />
          </div>
          <form className="history-confirm" onSubmit={(event) => void createFinalization(event)}>
            <label className="field">
              <span className="field__label">반영 사유</span>
              <textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={1000} disabled={busy} />
            </label>
            <label className="confirm-check">
              <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={busy || !candidate.can_finalize} />
              <span>표시된 정상 후보 전체와 예외 제외 근거를 확인했습니다.</span>
            </label>
            <button className="submit-button" type="submit" disabled={busy || !candidate.can_finalize || !confirmed || !reason.trim()}>
              정상 후보 전체 PENDING/HELD 반영
            </button>
          </form>
        </div>
      ) : null}

      {finalization ? <FinalizationView snapshot={finalization} /> : null}

      <form className="history-compare" onSubmit={(event) => void compare(event)}>
        <div className="section-heading"><h6>두 기간 원본 근거 나란히 조회</h6></div>
        <div className="history-periods">
          <DateRange label="왼쪽 기간" from={leftFrom} to={leftTo} setFrom={setLeftFrom} setTo={setLeftTo} />
          <DateRange label="오른쪽 기간" from={rightFrom} to={rightTo} setFrom={setRightFrom} setTo={setRightTo} />
        </div>
        <fieldset className="history-statuses">
          <legend>조회 데이터 상태</legend>
          {DATA_STATUSES.map((status) => (
            <label key={status}><input type="checkbox" checked={selectedStatuses.includes(status)} onChange={() => toggleStatus(status)} />{status}</label>
          ))}
        </fieldset>
        <details className="history-filters">
          <summary>정확한 Canonical / Mapping 필터</summary>
          <div className="history-filter-grid">
            <TextFilter label="Model key" value={modelKey} setValue={setModelKey} />
            <TextFilter label="Model-part key" value={partKey} setValue={setPartKey} />
            <TextFilter label="Item key" value={itemKey} setValue={setItemKey} />
            <TextFilter label="Supplier key" value={supplierKey} setValue={setSupplierKey} />
            <TextFilter label="Mapping revision ID" value={mappingRevisionId} setValue={setMappingRevisionId} />
          </div>
        </details>
        <button className="secondary-button" type="submit" disabled={busy}>원본 근거 비교 실행</button>
      </form>

      {formError ? <p className="form-error" role="alert">{formError}</p> : null}
      {error ? <div className="mapping-command-error" role="alert"><strong>{error.title}</strong><span>{error.message}</span><code>{error.code}</code></div> : null}
      {comparison ? (
        <ComparisonView
          comparison={comparison}
          predecessorResultId={predecessorResultId}
          successorResultId={successorResultId}
          selectPredecessor={selectPredecessor}
          selectSuccessor={selectSuccessor}
        />
      ) : null}
      {comparison ? (
        <ReplacementWorkspace
          predecessorResultId={predecessorResultId}
          successorResultId={successorResultId}
          candidate={replacementCandidate}
          decision={replacementDecision}
          reason={replacementReason}
          setReason={setReplacementReason}
          confirmed={replacementConfirmed}
          setConfirmed={setReplacementConfirmed}
          lookupId={replacementLookupId}
          setLookupId={setReplacementLookupId}
          busy={busy}
          loadCandidate={() => void loadReplacementCandidate()}
          decide={(event) => void decideReplacement(event)}
          loadDecision={() => void loadReplacementDecision()}
        />
      ) : null}
      <div className="screen-reader-only" role="status" aria-live="polite">
        {busy ? "과거 DB 요청을 처리하고 있습니다." : finalization ? `${finalization.status_label}. 완료 ${finalization.summary.completed}, 보류 ${finalization.summary.blocked}` : "정상 후보 Long 반영 근거를 확인할 수 있습니다."}
      </div>
    </section>
  );
}

function FinalizationView({ snapshot }: { snapshot: BulkFinalizationSnapshot }) {
  return (
    <div className="history-finalization">
      <div className="status-card" data-tone={snapshot.status === "BLOCKED" ? "danger" : snapshot.terminal ? "normal" : "warning"}>
        <div className="status-card__content"><p className="status-card__label">정상 후보 Long 반영 Worker</p><h6 className="status-card__title">{snapshot.status_label}</h6><p className="status-card__message">{snapshot.message}</p></div>
      </div>
      <dl className="evidence-grid"><Evidence label="Command ID" value={snapshot.command_id} /><Evidence label="row_version" value={String(snapshot.row_version)} /><Evidence label="완료 / 전체" value={`${snapshot.summary.completed} / ${snapshot.summary.total}`} /><Evidence label="BLOCKED" value={String(snapshot.summary.blocked)} /></dl>
      <div className="history-entry-progress">
        {snapshot.entries.map((entry) => <div key={entry.entry_id} data-status={entry.status}><strong>{entry.ordinal + 1}. {entry.status_label}</strong><span>{entry.long_status ?? entry.error_code ?? "처리 대기"}</span>{entry.long_ingestion_job_id ? <code>{entry.long_ingestion_job_id}</code> : null}</div>)}
      </div>
    </div>
  );
}

function ComparisonView({
  comparison,
  predecessorResultId,
  successorResultId,
  selectPredecessor,
  selectSuccessor,
}: {
  comparison: HistoricalComparisonResponse;
  predecessorResultId: string;
  successorResultId: string;
  selectPredecessor: (resultId: string) => void;
  selectSuccessor: (resultId: string) => void;
}) {
  const unsafe = Object.values(comparison.capabilities).some(Boolean);
  return (
    <div className="history-comparison" data-unsafe={unsafe}>
      <div className="bulk-capabilities" data-warning={unsafe}><strong>{unsafe ? "계산 경계 확인 필요" : "원본 근거 조회 전용"}</strong><span>평균·Cpk·Trend·Threshold·현재 Master 재판정·AI 없음</span></div>
      <dl className="evidence-grid"><Evidence label="구조적 결과 수 차이 (오른쪽-왼쪽)" value={String(comparison.delta.result_count_delta)} /><Evidence label="구조적 표본 수 차이 (오른쪽-왼쪽)" value={String(comparison.delta.measurement_count_delta)} /><Evidence label="왼쪽 Mapping revisions" value={comparison.delta.left_mapping_revision_ids.join(", ") || "없음"} /><Evidence label="오른쪽 Mapping revisions" value={comparison.delta.right_mapping_revision_ids.join(", ") || "없음"} /></dl>
      <div className="history-sides">
        <ComparisonSide title="왼쪽" side={comparison.left} predecessorResultId={predecessorResultId} successorResultId={successorResultId} selectPredecessor={selectPredecessor} selectSuccessor={selectSuccessor} />
        <ComparisonSide title="오른쪽" side={comparison.right} predecessorResultId={predecessorResultId} successorResultId={successorResultId} selectPredecessor={selectPredecessor} selectSuccessor={selectSuccessor} />
      </div>
    </div>
  );
}

function ComparisonSide({
  title,
  side,
  predecessorResultId,
  successorResultId,
  selectPredecessor,
  selectSuccessor,
}: {
  title: string;
  side: HistoricalComparisonResponse["left"];
  predecessorResultId: string;
  successorResultId: string;
  selectPredecessor: (resultId: string) => void;
  selectSuccessor: (resultId: string) => void;
}) {
  return (
    <section className="history-side">
      <h6>{title} · {side.date_from} ~ {side.date_to}</h6>
      <p>
        결과 {side.total_matching}건 중 {side.returned_count}건 표시 · 전체 표본 {side.total_sample_count}개
        {side.has_more ? " · 추가 결과 있음" : ""}
      </p>
      <small>반환 결과의 표본 {side.returned_results_sample_count}개 · Mapping revision {side.mapping_revision_ids.length}종</small>
      {side.results.map((result) => (
        <HistoricalResultCard
          key={result.result_id}
          result={result}
          predecessorSelected={predecessorResultId === result.result_id}
          successorSelected={successorResultId === result.result_id}
          selectPredecessor={selectPredecessor}
          selectSuccessor={selectSuccessor}
        />
      ))}
    </section>
  );
}

function HistoricalResultCard({
  result,
  predecessorSelected,
  successorSelected,
  selectPredecessor,
  selectSuccessor,
}: {
  result: HistoricalResult;
  predecessorSelected: boolean;
  successorSelected: boolean;
  selectPredecessor: (resultId: string) => void;
  selectSuccessor: (resultId: string) => void;
}) {
  const canBePredecessor = result.data_status === "VALID" || result.data_status === "SUSPECT";
  const canBeSuccessor = result.data_status === "PENDING";
  return (
    <article className="history-result-card">
      <div>
        <strong>{result.inspection_date} · LOT {result.source_lot_text ?? "미기재"}</strong>
        <span>{result.data_status} · {result.canonical_item_key ?? "미연결 항목"}</span>
      </div>
      <dl className="evidence-grid">
        <Evidence label="Result ID / version" value={`${result.result_id} / ${result.result_row_version}`} wide />
        <Evidence label="Receipt" value={result.receipt_id} />
        <Evidence label="수신 파일" value={`${result.original_filename} · ${result.received_at}`} wide />
        <Evidence label="원본 SHA" value={result.content_sha256} wide />
        <Evidence label="Source row" value={`${result.source_sheet_name}!${result.source_row_key}`} />
        <Evidence label="Mapping" value={`${result.mapping.template_id} rev.${result.mapping.revision} / schema ${result.mapping.schema_version}`} />
        <Evidence label="적용 당시 Mapping 기간" value={`${result.mapping.applied_effective_from} ~ ${result.mapping.applied_effective_to ?? "OPEN"}`} />
        <Evidence label="현재 Mapping 기록 기간" value={`${result.mapping.current_declared_effective_from} ~ ${result.mapping.current_resolved_effective_to ?? result.mapping.current_declared_effective_to ?? "OPEN"}`} />
        <Evidence label="Mapping SHA" value={result.mapping.payload_sha256} wide />
        <Evidence label="Binding revision" value={result.binding_revision === null ? "없음" : String(result.binding_revision)} />
        <Evidence label="Binding catalog" value={result.binding_catalog_revision} />
        <Evidence label="Binding fingerprint" value={result.binding_fingerprint} wide />
        <Evidence label="Source evidence SHA" value={result.source_evidence_sha256} wide />
        <Evidence label="System judgment" value={`${result.system_judgment_status} / ${result.system_judgment ?? "없음"}`} />
      </dl>
      {result.source_fields.length ? (
        <table>
          <caption>행 원본근거</caption>
          <thead><tr><th>역할</th><th>Sheet!Cell</th><th>Raw</th><th>표시값</th></tr></thead>
          <tbody>
            {result.source_fields.map((field) => (
              <tr key={`${field.role}:${field.sheet_name}:${field.coordinate}`}>
                <td>{field.role}</td>
                <td>{field.sheet_name}!{field.coordinate}</td>
                <td><code>{taggedValue(field.raw_value)}</code></td>
                <td>{field.display_value ?? "없음"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="empty-value">표시 가능한 행 원본근거 없음</p>}
      {result.applied_master ? (
        <p className="history-master">
          당시 Master rev.{result.applied_master.revision} · {shortHash(result.applied_master.payload_sha256)} · 적용근거 {result.applied_master.declared_effective_from} ~ {result.applied_master.resolved_effective_to ?? result.applied_master.declared_effective_to ?? "OPEN"}
        </p>
      ) : <p className="empty-value">적용 Master 없음</p>}
      {result.decision ? (
        <p className="history-master">
          데이터상태 결정 {result.decision.from_status} → {result.decision.to_status} · {result.decision.decided_by} · {result.decision.decided_at} · {result.decision.reason}
        </p>
      ) : <p className="empty-value">PENDING/HELD — 데이터상태 결정 이력 없음</p>}
      {result.replacement_chain ? (
        <div className="history-replacement-chain">
          <strong>
            수정본 체인 {result.replacement_chain.head_result_id} → {result.replacement_chain.tail_result_id ?? "추가 이력 있음"}
          </strong>
          <span>
            현재 위치 {result.replacement_chain.current_position ?? "절단 범위 밖"} · 링크 {result.replacement_chain.returned_link_count}개
            {result.replacement_chain.has_more ? " · 안전 한도로 일부만 표시" : ""}
          </span>
          <code>{shortHash(result.replacement_chain.links_sha256)}</code>
          <ol>
            {result.replacement_chain.links.map((link) => (
              <li key={link.replacement_id}>
                <span>{link.predecessor_result_id} ({link.predecessor_status_before}) → {link.successor_result_id} ({link.successor_status_after})</span>
                <small>{link.decided_by} · {link.decided_at} · {link.reason}</small>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      <p>
        표본 {result.total_sample_count}개 중 {result.returned_sample_count}개 표시
        {result.samples_has_more ? " · 안전 한도로 일부만 표시" : ""} · 전체 표본근거 {shortHash(result.sample_set_sha256)}
      </p>
      <table>
        <caption className="screen-reader-only">{result.result_id} 측정 원본</caption>
        <thead><tr><th>순번</th><th>Sheet!Cell</th><th>Raw</th><th>상태</th></tr></thead>
        <tbody>
          {result.samples.map((sample) => (
            <tr key={sample.measurement_id}>
              <td>{sample.ordinal}</td>
              <td>{sample.source_sheet_name}!{sample.source_cell}</td>
              <td><code>{sample.raw_numeric_value ?? sample.raw_qualitative_value ?? sample.raw_value_text ?? sample.raw_value_tag}</code></td>
              <td>{sample.data_status}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="replacement-result-actions">
        <button
          type="button"
          className="secondary-button"
          aria-label={`${result.result_id} 기존 결과로 선택`}
          aria-pressed={predecessorSelected}
          disabled={!canBePredecessor}
          onClick={() => selectPredecessor(result.result_id)}
        >
          {predecessorSelected ? "기존 결과 선택됨" : "기존 결과로 선택"}
        </button>
        <button
          type="button"
          className="secondary-button"
          aria-label={`${result.result_id} 수정본 후보로 선택`}
          aria-pressed={successorSelected}
          disabled={!canBeSuccessor}
          onClick={() => selectSuccessor(result.result_id)}
        >
          {successorSelected ? "수정본 후보 선택됨" : "수정본 후보로 선택"}
        </button>
      </div>
    </article>
  );
}

function ReplacementWorkspace({
  predecessorResultId,
  successorResultId,
  candidate,
  decision,
  reason,
  setReason,
  confirmed,
  setConfirmed,
  lookupId,
  setLookupId,
  busy,
  loadCandidate,
  decide,
  loadDecision,
}: {
  predecessorResultId: string;
  successorResultId: string;
  candidate: ReplacementCandidate | null;
  decision: ReplacementDecision | null;
  reason: string;
  setReason: (value: string) => void;
  confirmed: boolean;
  setConfirmed: (value: boolean) => void;
  lookupId: string;
  setLookupId: (value: string) => void;
  busy: boolean;
  loadCandidate: () => void;
  decide: (event: FormEvent<HTMLFormElement>) => void;
  loadDecision: () => void;
}) {
  return (
    <section className="replacement-workspace" aria-labelledby="replacement-workspace-heading">
      <div className="section-heading">
        <div>
          <p className="panel__kicker">CORRECTION HISTORY</p>
          <h6 id="replacement-workspace-heading">수정본 대체 이력 확인</h6>
        </div>
        <span className="section-count">ADMIN</span>
      </div>
      <div className="mapping-limit-note replacement-boundary">
        기존 결과와 수정본 후보를 직접 선택한 뒤 서버 근거를 다시 확인합니다. 확정 시 한
        트랜잭션에서 기존 결과만 REPLACED, 검토된 수정본만 VALID가 됩니다. 자동 교체,
        측정값 짝 추정, 통계, Threshold, AI 처리는 없습니다.
      </div>
      <dl className="evidence-grid replacement-selection">
        <Evidence label="기존 결과" value={predecessorResultId || "선택 안 됨"} wide />
        <Evidence label="수정본 후보" value={successorResultId || "선택 안 됨"} wide />
      </dl>
      <div className="history-actions">
        <button
          type="button"
          className="secondary-button"
          disabled={busy || !predecessorResultId || !successorResultId}
          onClick={loadCandidate}
        >
          대체 근거 다시 계산
        </button>
        <label className="replacement-lookup">
          <span>대체 이력 ID</span>
          <input
            className="text-input"
            value={lookupId}
            onChange={(event) => setLookupId(event.target.value)}
            autoComplete="off"
          />
        </label>
        <button
          type="button"
          className="secondary-button"
          disabled={busy || !lookupId.trim()}
          onClick={loadDecision}
        >
          대체 이력 다시 조회
        </button>
      </div>

      {candidate ? (
        <div className="replacement-candidate" data-eligible={candidate.can_replace}>
          <div className="bulk-capabilities" data-warning={!candidate.can_replace}>
            <strong>{candidate.can_replace ? "명시적 원자 대체 가능" : "대체 확정 불가"}</strong>
            <span>
              ADMIN 확인 전 변경 없음 · 자동 VALID/REPLACED 없음 · 측정값 자동 pairing 없음
            </span>
          </div>
          <dl className="evidence-grid">
            <Evidence label="Candidate SHA" value={candidate.candidate_sha256} wide />
            <Evidence label="Model / Part" value={`${candidate.identity.canonical_model_key} / ${candidate.identity.canonical_model_part_key}`} wide />
            <Evidence label="Supplier / LOT" value={`${candidate.identity.canonical_supplier_key} / ${candidate.identity.source_lot_text}`} wide />
            <Evidence label="검사항목" value={candidate.identity.canonical_item_key} />
            <Evidence label="기존 상태 / 판정" value={`${candidate.predecessor.data_status} / ${candidate.predecessor.system_judgment ?? "없음"}`} />
            <Evidence label="수정본 상태 / 제안판정" value={`${candidate.successor.data_status} / ${candidate.successor.proposed_system_judgment ?? "없음"}`} />
            <Evidence label="수정본 Master" value={`${candidate.successor.selected_master_revision_id ?? "없음"} / ${shortHash(candidate.successor.selected_master_payload_sha256 ?? "없음")}`} wide />
            <Evidence label="기존 측정근거" value={`${candidate.predecessor.measurement_count}개 / ${shortHash(candidate.predecessor.measurement_set_sha256)}`} />
            <Evidence label="수정본 측정근거" value={`${candidate.successor.measurement_count}개 / ${shortHash(candidate.successor.measurement_set_sha256)}`} />
          </dl>
          <div className="replacement-evidence-columns">
            <MeasurementProofTable
              title="기존 결과 측정근거"
              measurements={candidate.predecessor.measurements}
              returnedCount={candidate.predecessor.returned_measurement_count}
              totalCount={candidate.predecessor.measurement_count}
              hasMore={candidate.predecessor.measurements_has_more}
            />
            <MeasurementProofTable
              title="수정본 후보 측정근거"
              measurements={candidate.successor.measurements}
              returnedCount={candidate.successor.returned_measurement_count}
              totalCount={candidate.successor.measurement_count}
              hasMore={candidate.successor.measurements_has_more}
            />
          </div>
          <table className="replacement-differences">
            <caption>서버가 검출한 변경 근거</caption>
            <thead><tr><th>Code</th><th>Field</th><th>기존</th><th>수정본</th></tr></thead>
            <tbody>
              {candidate.differences.map((difference, index) => (
                <tr key={`${difference.code}:${difference.field}:${index}`} data-danger={isHighRiskDifference(difference.code)}>
                  <td><code>{difference.code}</code></td>
                  <td>{difference.field}</td>
                  <td>{difference.predecessor_value ?? "없음"}</td>
                  <td>{difference.successor_value ?? "없음"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {candidate.issues.length ? (
            <ul className="replacement-issues">
              {candidate.issues.map((issue, index) => (
                <li key={`${issue.code}:${index}`}><code>{issue.code}</code><span>{issue.message}</span></li>
              ))}
            </ul>
          ) : <p className="empty-value">추가 차단 사유 없음</p>}
          <form className="history-confirm replacement-confirm" onSubmit={decide}>
            <label className="field">
              <span className="field__label">수정본 대체 사유</span>
              <textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={1000} disabled={busy} />
            </label>
            <label className="confirm-check">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
                disabled={busy || !candidate.can_replace}
              />
              <span>두 결과의 원본·Master·판정·전체 측정 집합 해시와 반환된 개별 근거, 위험 변경을 확인했습니다.</span>
            </label>
            <button
              type="submit"
              className="submit-button"
              disabled={busy || !candidate.can_replace || !confirmed || !reason.trim()}
            >
              기존 REPLACED / 수정본 VALID 원자 확정
            </button>
          </form>
        </div>
      ) : null}

      {decision ? (
        <div className="replacement-decision status-card" data-tone="normal">
          <div className="status-card__content">
            <p className="status-card__label">수정본 대체 이력</p>
            <h6 className="status-card__title">{decision.replayed ? "기존 결정을 멱등 재조회했습니다" : "원자 대체를 완료했습니다"}</h6>
            <p className="status-card__message">
              기존 {decision.predecessor_status} · 수정본 {decision.successor_status} · 공식선택은 수정본만
            </p>
          </div>
          <dl className="evidence-grid">
            <Evidence label="Replacement ID" value={decision.replacement_id} wide />
            <Evidence label="Intent SHA" value={decision.intent_sha256} wide />
            <Evidence label="처리자 / 시각" value={`${decision.decided_by} / ${decision.decided_at}`} wide />
            <Evidence label="사유" value={decision.reason} wide />
            <Evidence label="기존 result version" value={String(decision.predecessor_result_row_version)} />
            <Evidence label="수정본 result version" value={String(decision.successor_result_row_version)} />
          </dl>
        </div>
      ) : null}
    </section>
  );
}

function MeasurementProofTable({
  title,
  measurements,
  returnedCount,
  totalCount,
  hasMore,
}: {
  title: string;
  measurements: ReplacementCandidate["predecessor"]["measurements"];
  returnedCount: number;
  totalCount: number;
  hasMore: boolean;
}) {
  return (
    <table>
      <caption>
        {title} ({returnedCount}/{totalCount}){hasMore ? " · 안전 한도로 일부 표시" : ""}
      </caption>
      <thead><tr><th>순번</th><th>Cell</th><th>상태</th><th>Evidence SHA</th></tr></thead>
      <tbody>
        {measurements.map((measurement) => (
          <tr key={measurement.measurement_id}>
            <td>{measurement.sample_ordinal}</td>
            <td>{measurement.source_cell}</td>
            <td>{measurement.data_status} / v{measurement.row_version}</td>
            <td><code>{shortHash(measurement.evidence_sha256)}</code></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function isHighRiskDifference(code: string): boolean {
  return code.includes("FAIL_TO_PASS") || code.includes("NG_TO_PASS");
}

function taggedValue(value: { kind: string; value: unknown }): string {
  return `${value.kind}:${typeof value.value === "string" ? value.value : JSON.stringify(value.value)}`;
}

function EntryPartition({ title, entries }: { title: string; entries: Array<{ key: string; ordinal: number; filename: string; detail: string }> }) {
  return <section><h6>{title} ({entries.length})</h6>{entries.length ? <ol>{entries.map((entry) => <li key={entry.key}><strong>{entry.ordinal + 1}. {entry.filename}</strong><small>{entry.detail}</small></li>)}</ol> : <p className="empty-value">해당 항목 없음</p>}</section>;
}

function DateRange({ label, from, to, setFrom, setTo }: { label: string; from: string; to: string; setFrom: (value: string) => void; setTo: (value: string) => void }) {
  return <fieldset><legend>{label}</legend><label>시작일<input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label><label>종료일<input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label></fieldset>;
}

function TextFilter({ label, value, setValue }: { label: string; value: string; setValue: (value: string) => void }) {
  return <label><span>{label}</span><input className="text-input" value={value} onChange={(event) => setValue(event.target.value)} autoComplete="off" /></label>;
}

function Evidence({ label, value, wide = false }: { label: string; value: string; wide?: boolean }) {
  return <div className={wide ? "evidence-item evidence-item--wide" : "evidence-item"}><dt>{label}</dt><dd title={value}>{value}</dd></div>;
}

function compactFilters(filters: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(filters).flatMap(([key, value]) => value.trim() ? [[key, value.trim()]] : []));
}

function validCandidateScope(value: BulkFinalizationCandidate, batchId: string, projectKey: string, supplierScope: string): boolean {
  return value.batch_id === batchId && value.project_key === projectKey && value.supplier_scope === supplierScope;
}

function validFinalizationScope(value: BulkFinalizationSnapshot, batchId: string, projectKey: string, supplierScope: string): boolean {
  return value.batch_id === batchId && value.project_key === projectKey && value.supplier_scope === supplierScope;
}

function validReplacementDecisionScope(
  value: ReplacementDecision,
  projectKey: string,
  candidate: ReplacementCandidate,
): boolean {
  return value.project_key === projectKey
    && value.predecessor_result_id === candidate.predecessor.result_id
    && value.successor_result_id === candidate.successor.result_id
    && value.candidate_sha256 === candidate.candidate_sha256
    && value.predecessor_status === "REPLACED"
    && value.successor_status === "VALID"
    && value.official_predecessor === false
    && value.official_successor === true;
}

function scopeError(title: string): DisplayError {
  return { title, message: "응답의 Project, Supplier scope 또는 배치가 현재 화면과 일치하지 않습니다.", code: "HISTORY_SCOPE_MISMATCH" };
}

function replacementScopeError(title: string): DisplayError {
  return { title, message: "응답의 Project 또는 선택한 결과 쌍이 현재 화면과 일치하지 않습니다.", code: "RESULT_REPLACEMENT_SCOPE_MISMATCH" };
}

function toDisplayError(error: unknown, title: string): DisplayError {
  if (error instanceof HistoryApiError) return { title: error.statusLabel || title, message: error.message, code: error.code };
  if (error instanceof ResultReplacementApiError) return { title: error.statusLabel || title, message: error.message, code: error.code };
  return { title, message: SAFE_ERROR, code: "HISTORY_REQUEST_FAILED" };
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function shortHash(value: string): string {
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-8)}` : value;
}
