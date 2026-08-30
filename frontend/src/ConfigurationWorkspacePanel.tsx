import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";

import {
  configurationApi as defaultConfigurationApi,
  ConfigurationApiError,
  type ApprovedMappingSelection,
  type CanonicalInspectionItemResource,
  type ConfigurationApi,
  type ConfigurationCellSource,
  type ConfigurationSnapshot,
  type DecidedItemDisposition,
  type MappingRowSelection,
  type MasterSpecResource,
  type MeasurementMode,
  type RowBindingResource,
  type SamplePolicy,
} from "./api/configuration";

const SAFE_REQUEST_ERROR = "요청을 처리하지 못했습니다. 다시 시도해 주세요.";

interface ConfigurationWorkspacePanelProps {
  projectKey: string;
  api?: ConfigurationApi;
}

type SnapshotUpdater<T> = (snapshot: ConfigurationSnapshot, value: T) => ConfigurationSnapshot;

interface CommandRunner {
  <T>(
    key: string,
    operation: (signal: AbortSignal) => Promise<T>,
    update: SnapshotUpdater<T>,
    successMessage: string,
  ): Promise<boolean>;
}

export function ConfigurationWorkspacePanel({
  projectKey,
  api = defaultConfigurationApi,
}: ConfigurationWorkspacePanelProps) {
  const [snapshot, setSnapshot] = useState<ConfigurationSnapshot | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<{ title: string; message: string; code: string } | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const begin = (key: string) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(key);
    setNotice(null);
    setError(null);
    return controller;
  };

  const load = async () => {
    const controller = begin("snapshot");
    try {
      setSnapshot(await api.getSnapshot(projectKey, controller.signal));
      setNotice("서버의 최신 canonical 설정 snapshot을 불러왔습니다.");
    } catch (reason) {
      if (!isAbortError(reason)) setError(toConfigurationError(reason));
    } finally {
      if (controllerRef.current === controller) setBusy(null);
    }
  };

  async function runCommand<T>(
    key: string,
    operation: (signal: AbortSignal) => Promise<T>,
    update: SnapshotUpdater<T>,
    successMessage: string,
  ): Promise<boolean> {
    const controller = begin(key);
    try {
      const value = await operation(controller.signal);
      setSnapshot((current) => (current ? update(current, value) : current));
      setNotice(successMessage);
      return true;
    } catch (reason) {
      if (!isAbortError(reason)) setError(toConfigurationError(reason));
      return false;
    } finally {
      if (controllerRef.current === controller) setBusy(null);
    }
  }

  return (
    <section className="configuration-workspace" aria-labelledby="configuration-heading">
      <div className="section-heading">
        <div>
          <p className="panel__kicker">CANONICAL CONFIGURATION</p>
          <h4 id="configuration-heading">프로젝트 기준정보 첫 설정</h4>
        </div>
        <span className="section-count">04</span>
      </div>
      <div className="configuration-boundary">
        <strong>모든 설정은 별도 명령과 이유가 필요합니다.</strong>
        canonical 계층과 Supplier를 만든 뒤 항목 처리방향, 숫자 Master, 승인 Mapping 행 Binding을
        순서대로 설정합니다. 같은 로컬 사용자여도 Draft·검토·승인은 합치지 않습니다. AI 추론,
        fuzzy 연결, 기본 Master, 자동 VALID 전환은 수행하지 않습니다.
      </div>

      {!snapshot ? (
        <button className="secondary-button" type="button" onClick={() => void load()} disabled={busy !== null}>
          {busy === "snapshot" ? "설정 현황 불러오는 중…" : "설정 현황 불러오기"}
        </button>
      ) : (
        <>
          <ConfigurationSummary snapshot={snapshot} />
          <CanonicalCreationForms
            snapshot={snapshot}
            projectKey={projectKey}
            api={api}
            run={runCommand}
            disabled={busy !== null}
          />
          <ItemDispositionForm
            snapshot={snapshot}
            projectKey={projectKey}
            api={api}
            run={runCommand}
            disabled={busy !== null}
          />
          <MasterFirstRevisionForm
            snapshot={snapshot}
            projectKey={projectKey}
            api={api}
            run={runCommand}
            disabled={busy !== null}
          />
          <BindingFirstRevisionForm
            snapshot={snapshot}
            projectKey={projectKey}
            api={api}
            run={runCommand}
            disabled={busy !== null}
          />
          <div className="configuration-resume-note">
            <strong>설정 반영 후 다음 단계</strong>
            승인된 Binding이 생겨도 기존 Long 후보를 자동 변경하지 않습니다. 위 설정 snapshot을
            다시 확인한 뒤, 이 화면 아래의 <em>Long 후보 만들기</em>를 다시 실행하세요.
          </div>
          <button className="secondary-button" type="button" onClick={() => void load()} disabled={busy !== null}>
            최신 설정 snapshot 다시 확인
          </button>
        </>
      )}

      <div className="screen-reader-only" role="status" aria-live="polite" aria-atomic="true">
        {busy ? "설정 명령을 처리하고 있습니다." : error?.message ?? notice ?? "설정 현황을 불러올 수 있습니다."}
      </div>
      {notice ? <p className="configuration-notice">{notice}</p> : null}
      {error ? (
        <div className="mapping-command-error" role="alert">
          <strong>{error.title}</strong><span>{error.message}</span><code>{error.code}</code>
        </div>
      ) : null}
    </section>
  );
}

function ConfigurationSummary({ snapshot }: { snapshot: ConfigurationSnapshot }) {
  return (
    <div className="configuration-summary">
      <div className="configuration-counts" aria-label="canonical 설정 개수">
        <span>Model <strong>{snapshot.models.length}</strong></span>
        <span>ModelPart <strong>{snapshot.model_parts.length}</strong></span>
        <span>InspectionItem <strong>{snapshot.inspection_items.length}</strong></span>
        <span>Supplier <strong>{snapshot.suppliers.length}</strong></span>
        <span>Master <strong>{snapshot.master_specs.length}</strong></span>
        <span>Binding <strong>{snapshot.row_bindings.length}</strong></span>
      </div>
      <div className="configuration-safety" aria-label="설정 안전 경계">
        <span>첫 Master revision만</span><span>첫 Binding revision만</span>
        <span>후속 revision·supersede 미지원</span><span>공식값 생성 없음</span>
        <span>자동 효과 없음</span><span>AI 사용 없음</span><span>서버 actor: TRUSTED_LOCAL_OWNER</span>
      </div>
      <div className="sheet-table-wrap" tabIndex={0}>
        <table className="sheet-table configuration-catalog-table" aria-label="canonical 항목과 CAS 현황">
          <thead><tr><th>계층</th><th>Key</th><th>표시명</th><th>상위 Key</th><th>상태</th><th>CAS</th></tr></thead>
          <tbody>
            {snapshot.models.map((model) => (
              <tr key={`model:${model.model_key}`}><td>Model</td><td>{model.model_key}</td><td>{model.display_name}</td><td>-</td><td>등록됨</td><td>row_version {model.row_version}</td></tr>
            ))}
            {snapshot.model_parts.map((part) => (
              <tr key={`part:${part.model_part_key}`}><td>ModelPart</td><td>{part.model_part_key}</td><td>{part.display_name}</td><td>{part.model_key}</td><td>등록됨</td><td>row_version {part.row_version}</td></tr>
            ))}
            {snapshot.inspection_items.map((item) => (
              <tr key={`item:${item.item_key}`}><td>InspectionItem</td><td>{item.item_key}</td><td>{item.display_name}</td><td>{item.model_part_key}</td><td>{item.disposition}</td><td>row_version {item.row_version}</td></tr>
            ))}
            {snapshot.suppliers.map((supplier) => (
              <tr key={`supplier:${supplier.supplier_key}`}><td>Supplier</td><td>{supplier.supplier_key}</td><td>{supplier.display_name}</td><td>독립 기준정보</td><td>등록됨</td><td>row_version {supplier.row_version}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CanonicalCreationForms({
  snapshot,
  projectKey,
  api,
  run,
  disabled,
}: {
  snapshot: ConfigurationSnapshot;
  projectKey: string;
  api: ConfigurationApi;
  run: CommandRunner;
  disabled: boolean;
}) {
  const [modelKey, setModelKey] = useState("");
  const [modelName, setModelName] = useState("");
  const [modelReason, setModelReason] = useState("");
  const [supplierKey, setSupplierKey] = useState("");
  const [supplierName, setSupplierName] = useState("");
  const [supplierReason, setSupplierReason] = useState("");
  const [parentModel, setParentModel] = useState("");
  const [partKey, setPartKey] = useState("");
  const [partName, setPartName] = useState("");
  const [partReason, setPartReason] = useState("");
  const [parentPart, setParentPart] = useState("");
  const [itemKey, setItemKey] = useState("");
  const [itemName, setItemName] = useState("");
  const [itemReason, setItemReason] = useState("");

  const createModel = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const request = exactValues([modelKey, modelName, modelReason]);
    if (!request) return;
    if (await run(
      "create-model",
      (signal) => api.createModel({ project_key: projectKey, model_key: request[0], display_name: request[1], reason: request[2] }, signal),
      (current, value) => ({ ...current, models: upsert(current.models, value, (item) => item.model_key) }),
      "Model을 등록했습니다.",
    )) {
      setModelKey(""); setModelName(""); setModelReason("");
    }
  };

  const createSupplier = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const request = exactValues([supplierKey, supplierName, supplierReason]);
    if (!request) return;
    if (await run(
      "create-supplier",
      (signal) => api.createSupplier({ project_key: projectKey, supplier_key: request[0], display_name: request[1], reason: request[2] }, signal),
      (current, value) => ({ ...current, suppliers: upsert(current.suppliers, value, (item) => item.supplier_key) }),
      "독립 Supplier를 등록했습니다.",
    )) {
      setSupplierKey(""); setSupplierName(""); setSupplierReason("");
    }
  };

  const createPart = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const request = exactValues([parentModel, partKey, partName, partReason]);
    if (!request) return;
    if (await run(
      "create-part",
      (signal) => api.createModelPart({ project_key: projectKey, model_key: request[0], model_part_key: request[1], display_name: request[2], reason: request[3] }, signal),
      (current, value) => ({ ...current, model_parts: upsert(current.model_parts, value, (item) => item.model_part_key) }),
      "선택한 Model 아래에 ModelPart를 등록했습니다.",
    )) {
      setPartKey(""); setPartName(""); setPartReason("");
    }
  };

  const createItem = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const request = exactValues([parentPart, itemKey, itemName, itemReason]);
    if (!request) return;
    if (await run(
      "create-item",
      (signal) => api.createInspectionItem({ project_key: projectKey, model_part_key: request[0], item_key: request[1], display_name: request[2], reason: request[3] }, signal),
      (current, value) => ({ ...current, inspection_items: upsert(current.inspection_items, value, (item) => item.item_key) }),
      "InspectionItem을 CANDIDATE로 등록했습니다.",
    )) {
      setItemKey(""); setItemName(""); setItemReason("");
    }
  };

  return (
    <section className="configuration-stage" aria-labelledby="canonical-create-heading">
      <div className="section-heading"><h5 id="canonical-create-heading">1. Canonical 계층과 Supplier 생성</h5><span className="section-count">각각 별도 저장</span></div>
      <div className="configuration-form-grid">
        <ConfigForm title="Model 생성" onSubmit={createModel} disabled={disabled} submitLabel="Model 저장">
          <TextField id="config-model-key" label="Model key" value={modelKey} onChange={setModelKey} disabled={disabled} />
          <TextField id="config-model-name" label="Model 표시명" value={modelName} onChange={setModelName} disabled={disabled} />
          <ReasonInput id="config-model-reason" label="Model 생성 이유" value={modelReason} onChange={setModelReason} disabled={disabled} />
        </ConfigForm>
        <ConfigForm title="Supplier 독립 생성" onSubmit={createSupplier} disabled={disabled} submitLabel="Supplier 저장">
          <TextField id="config-supplier-key" label="Supplier key" value={supplierKey} onChange={setSupplierKey} disabled={disabled} />
          <TextField id="config-supplier-name" label="Supplier 표시명" value={supplierName} onChange={setSupplierName} disabled={disabled} />
          <ReasonInput id="config-supplier-reason" label="Supplier 생성 이유" value={supplierReason} onChange={setSupplierReason} disabled={disabled} />
        </ConfigForm>
        <ConfigForm title="ModelPart 생성" onSubmit={createPart} disabled={disabled || !snapshot.models.length} submitLabel="ModelPart 저장">
          <SelectField id="config-part-model" label="상위 Model" value={parentModel} onChange={setParentModel} disabled={disabled} options={snapshot.models.map((item) => ({ value: item.model_key, label: `${item.display_name} (${item.model_key})` }))} />
          <TextField id="config-part-key" label="ModelPart key" value={partKey} onChange={setPartKey} disabled={disabled} />
          <TextField id="config-part-name" label="ModelPart 표시명" value={partName} onChange={setPartName} disabled={disabled} />
          <ReasonInput id="config-part-reason" label="ModelPart 생성 이유" value={partReason} onChange={setPartReason} disabled={disabled} />
        </ConfigForm>
        <ConfigForm title="InspectionItem 생성" onSubmit={createItem} disabled={disabled || !snapshot.model_parts.length} submitLabel="InspectionItem 저장">
          <SelectField id="config-item-part" label="상위 ModelPart" value={parentPart} onChange={setParentPart} disabled={disabled} options={snapshot.model_parts.map((item) => ({ value: item.model_part_key, label: `${item.display_name} (${item.model_part_key})` }))} />
          <TextField id="config-item-key" label="InspectionItem key" value={itemKey} onChange={setItemKey} disabled={disabled} />
          <TextField id="config-item-name" label="InspectionItem 표시명" value={itemName} onChange={setItemName} disabled={disabled} />
          <ReasonInput id="config-item-reason" label="InspectionItem 생성 이유" value={itemReason} onChange={setItemReason} disabled={disabled} />
          <p className="field__hint">새 항목은 서버가 CANDIDATE로 생성하며 자동 관리하지 않습니다.</p>
        </ConfigForm>
      </div>
    </section>
  );
}

function ItemDispositionForm({
  snapshot,
  projectKey,
  api,
  run,
  disabled,
}: {
  snapshot: ConfigurationSnapshot;
  projectKey: string;
  api: ConfigurationApi;
  run: CommandRunner;
  disabled: boolean;
}) {
  const candidates = snapshot.inspection_items.filter((item) => item.disposition === "CANDIDATE");
  const [itemKey, setItemKey] = useState("");
  const [disposition, setDisposition] = useState<DecidedItemDisposition | "">("");
  const [reason, setReason] = useState("");
  const selected = candidates.find((item) => item.item_key === itemKey) ?? null;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected || !disposition || !reason.trim()) return;
    if (await run(
      "set-disposition",
      (signal) => api.setItemDisposition({ project_key: projectKey, item_key: selected.item_key, disposition, expected_row_version: selected.row_version, reason: reason.trim() }, signal),
      (current, value) => ({ ...current, inspection_items: upsert(current.inspection_items, value, (item) => item.item_key) }),
      `항목 처리방향을 ${disposition}로 결정했습니다.`,
    )) {
      setItemKey(""); setDisposition(""); setReason("");
    }
  };

  return (
    <section className="configuration-stage" aria-labelledby="disposition-heading">
      <div className="section-heading"><h5 id="disposition-heading">2. InspectionItem 처리방향 결정</h5><span className="section-count">CANDIDATE → MANAGED / EXCLUDED</span></div>
      <form className="configuration-command" onSubmit={submit}>
        <SelectField id="config-disposition-item" label="CANDIDATE 항목" value={itemKey} onChange={setItemKey} disabled={disabled} options={candidates.map(itemOption)} />
        <fieldset className="configuration-choice" disabled={disabled || !selected}>
          <legend>처리방향</legend>
          <label><input type="radio" name="item-disposition" checked={disposition === "MANAGED"} onChange={() => setDisposition("MANAGED")} />MANAGED · 숫자 Master 관리 대상</label>
          <label><input type="radio" name="item-disposition" checked={disposition === "EXCLUDED"} onChange={() => setDisposition("EXCLUDED")} />EXCLUDED · 명시적 제외</label>
        </fieldset>
        <p className="configuration-cas">서버 snapshot CAS: row_version <strong>{selected?.row_version ?? "선택 필요"}</strong></p>
        <ReasonInput id="config-disposition-reason" label="처리방향 결정 이유" value={reason} onChange={setReason} disabled={disabled} />
        <button className="secondary-button" type="submit" disabled={disabled || !selected || !disposition || !reason.trim()}>처리방향 결정</button>
      </form>
    </section>
  );
}

function MasterFirstRevisionForm({
  snapshot,
  projectKey,
  api,
  run,
  disabled,
}: {
  snapshot: ConfigurationSnapshot;
  projectKey: string;
  api: ConfigurationApi;
  run: CommandRunner;
  disabled: boolean;
}) {
  const managedItems = snapshot.inspection_items.filter((item) => item.disposition === "MANAGED");
  const [itemKey, setItemKey] = useState("");
  const [target, setTarget] = useState("");
  const [lsl, setLsl] = useState("");
  const [usl, setUsl] = useState("");
  const [unit, setUnit] = useState("");
  const [externalRevision, setExternalRevision] = useState("");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [effectiveTo, setEffectiveTo] = useState("");
  const [sourceReference, setSourceReference] = useState("");
  const [draftReason, setDraftReason] = useState("");
  const [reviewReason, setReviewReason] = useState("");
  const [approveReason, setApproveReason] = useState("");
  const master = snapshot.master_specs.find((value) => value.canonical_item_key === itemKey && value.revision === 1) ?? null;

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!itemKey || !unit.trim() || !externalRevision.trim() || !effectiveFrom || !sourceReference.trim() || !draftReason.trim()) return;
    const exactLsl = optionalExact(lsl);
    const exactUsl = optionalExact(usl);
    if (exactLsl === null && exactUsl === null) return;
    await run(
      "master-draft",
      (signal) => api.createMasterDraft({
        project_key: projectKey,
        canonical_item_key: itemKey,
        target: optionalExact(target),
        lsl: exactLsl,
        usl: exactUsl,
        unit: unit.trim(),
        external_spec_revision: externalRevision.trim(),
        effective_from: effectiveFrom,
        effective_to: optionalExact(effectiveTo),
        source_reference: sourceReference.trim(),
        expected_history_row_version: 0,
        reason: draftReason.trim(),
      }, signal),
      upsertMaster,
      "Master Spec revision 1 Draft를 생성했습니다. 검토는 아직 완료되지 않았습니다.",
    );
  };

  const transition = async (kind: "review" | "approve") => {
    if (!master) return;
    const reason = (kind === "review" ? reviewReason : approveReason).trim();
    if (!reason) return;
    await run(
      `master-${kind}`,
      (signal) => (kind === "review" ? api.reviewMaster : api.approveMaster)({
        project_key: projectKey,
        canonical_item_key: master.canonical_item_key,
        revision: 1,
        expected_history_row_version: master.history_row_version,
        expected_revision_row_version: master.revision_row_version,
        reason,
      }, signal),
      upsertMaster,
      kind === "review" ? "Master Spec 검토를 별도 명령으로 완료했습니다." : "Master Spec을 별도 승인 명령으로 승인했습니다.",
    );
  };

  return (
    <section className="configuration-stage" aria-labelledby="master-first-heading">
      <div className="section-heading"><h5 id="master-first-heading">3. 숫자 Master Spec 첫 revision</h5><span className="section-count">Draft → 검토 → 승인</span></div>
      <SelectField id="config-master-item" label="MANAGED 항목" value={itemKey} onChange={setItemKey} disabled={disabled} options={managedItems.map(itemOption)} />
      {!itemKey ? <p className="empty-value">MANAGED 항목을 선택하세요. CANDIDATE/EXCLUDED 항목에는 Master를 만들지 않습니다.</p> : null}
      {itemKey && !master ? (
        <form className="configuration-command" onSubmit={create}>
          <div className="configuration-decimal-grid">
            <TextField id="config-master-target" label="Target Decimal (선택)" value={target} onChange={setTarget} disabled={disabled} inputMode="decimal" required={false} />
            <TextField id="config-master-lsl" label="LSL Decimal (둘 중 하나 필수)" value={lsl} onChange={setLsl} disabled={disabled} inputMode="decimal" required={false} />
            <TextField id="config-master-usl" label="USL Decimal (둘 중 하나 필수)" value={usl} onChange={setUsl} disabled={disabled} inputMode="decimal" required={false} />
            <TextField id="config-master-unit" label="단위" value={unit} onChange={setUnit} disabled={disabled} />
          </div>
          <TextField id="config-master-external-revision" label="외부 Spec revision" value={externalRevision} onChange={setExternalRevision} disabled={disabled} />
          <DatePeriodFields prefix="config-master" effectiveFrom={effectiveFrom} effectiveTo={effectiveTo} setEffectiveFrom={setEffectiveFrom} setEffectiveTo={setEffectiveTo} disabled={disabled} />
          <TextField id="config-master-source" label="Master 출처 참조" value={sourceReference} onChange={setSourceReference} disabled={disabled} />
          <ReasonInput id="config-master-draft-reason" label="Master Draft 생성 이유" value={draftReason} onChange={setDraftReason} disabled={disabled} />
          <p className="configuration-cas">첫 history CAS: expected_history_row_version <strong>0</strong> · revision은 서버가 1로 생성</p>
          <button className="secondary-button" type="submit" disabled={disabled || !itemKey || (!lsl.trim() && !usl.trim())}>Master Draft 생성</button>
        </form>
      ) : null}
      {master ? (
        <div className="configuration-workflow">
          <MasterEvidence master={master} />
          {master.status === "DRAFT" ? (
            <div className="configuration-command">
              <ReasonInput id="config-master-review-reason" label="Master 검토 완료 이유" value={reviewReason} onChange={setReviewReason} disabled={disabled} />
              <p className="field__hint">LOCAL_OWNER의 검토 명령입니다. 승인과 합치지 않습니다.</p>
              <button className="secondary-button" type="button" onClick={() => void transition("review")} disabled={disabled || !reviewReason.trim()}>Master 검토 완료 (REVIEWER)</button>
            </div>
          ) : null}
          {master.status === "REVIEWED" ? (
            <div className="configuration-command">
              <ReasonInput id="config-master-approve-reason" label="Master 최종 승인 이유" value={approveReason} onChange={setApproveReason} disabled={disabled} />
              <p className="field__hint">검토가 끝난 revision만 별도 승인합니다.</p>
              <button className="submit-button" type="button" onClick={() => void transition("approve")} disabled={disabled || !approveReason.trim()}>Master 최종 승인 (ADMIN)</button>
            </div>
          ) : null}
        </div>
      ) : null}
      <p className="configuration-limit">후속 Master revision과 supersede는 이 화면에서 지원하지 않습니다.</p>
    </section>
  );
}

function BindingFirstRevisionForm({
  snapshot,
  projectKey,
  api,
  run,
  disabled,
}: {
  snapshot: ConfigurationSnapshot;
  projectKey: string;
  api: ConfigurationApi;
  run: CommandRunner;
  disabled: boolean;
}) {
  const [mappingIndex, setMappingIndex] = useState("");
  const [rowKey, setRowKey] = useState("");
  const [sourceModels, setSourceModels] = useState("");
  const [modelKey, setModelKey] = useState("");
  const [supplierKey, setSupplierKey] = useState("");
  const [partKey, setPartKey] = useState("");
  const [itemKey, setItemKey] = useState("");
  const [measurementMode, setMeasurementMode] = useState<MeasurementMode | "">("");
  const [samplePolicy, setSamplePolicy] = useState<SamplePolicy | "">("");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [effectiveTo, setEffectiveTo] = useState("");
  const [draftReason, setDraftReason] = useState("");
  const [reviewReason, setReviewReason] = useState("");
  const [approveReason, setApproveReason] = useState("");

  const mapping = mappingIndex === "" ? null : snapshot.approved_mapping_revisions[Number(mappingIndex)] ?? null;
  const row = mapping?.rows.find((value) => value.row_key === rowKey) ?? null;
  const parts = snapshot.model_parts.filter((value) => value.model_key === modelKey);
  const items = snapshot.inspection_items.filter((value) => value.model_part_key === partKey && value.disposition !== "CANDIDATE");
  const binding = mapping && row ? snapshot.row_bindings.find((value) => bindingMatches(value, mapping, row.row_key)) ?? null : null;

  const selectMapping = (value: string) => {
    setMappingIndex(value); setRowKey(""); setReviewReason(""); setApproveReason("");
  };

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!mapping || !row || !modelKey || !supplierKey || !partKey || !itemKey || !measurementMode || !samplePolicy || !effectiveFrom || !draftReason.trim()) return;
    const exactModels = sourceModels.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    if (!exactModels.length || !mapping.model_source) return;
    if ((measurementMode === "JUDGMENT_ONLY") !== (samplePolicy === "ZERO_ALLOWED")) return;
    await run(
      "binding-draft",
      (signal) => api.createBindingDraft({
        project_key: projectKey,
        supplier_scope: mapping.supplier_scope,
        template_id: mapping.template_id,
        template_revision: mapping.revision,
        row_key: row.row_key,
        source_model_values: exactModels,
        canonical_model_key: modelKey,
        canonical_supplier_key: supplierKey,
        canonical_model_part_key: partKey,
        canonical_item_key: itemKey,
        measurement_mode: measurementMode,
        sample_policy: samplePolicy,
        effective_from: effectiveFrom,
        effective_to: optionalExact(effectiveTo),
        expected_history_row_version: 0,
        reason: draftReason.trim(),
      }, signal),
      upsertBinding,
      "선택한 승인 Mapping 행의 Binding revision 1 Draft를 생성했습니다.",
    );
  };

  const transition = async (kind: "review" | "approve") => {
    if (!binding) return;
    const reason = (kind === "review" ? reviewReason : approveReason).trim();
    if (!reason) return;
    await run(
      `binding-${kind}`,
      (signal) => (kind === "review" ? api.reviewBinding : api.approveBinding)({
        project_key: projectKey,
        supplier_scope: binding.supplier_scope,
        template_id: binding.template_id,
        template_revision: binding.template_revision,
        row_key: binding.row_key,
        binding_revision: 1,
        expected_history_row_version: binding.history_row_version,
        expected_revision_row_version: binding.revision_row_version,
        reason,
      }, signal),
      upsertBinding,
      kind === "review" ? "Row Binding 검토를 별도 명령으로 완료했습니다." : "Row Binding을 별도 승인 명령으로 승인했습니다. Long 후보를 다시 만드세요.",
    );
  };

  return (
    <section className="configuration-stage" aria-labelledby="binding-first-heading">
      <div className="section-heading"><h5 id="binding-first-heading">4. 승인 Mapping 행의 첫 Row Binding</h5><span className="section-count">Draft → 검토 → 승인</span></div>
      <SelectField
        id="config-binding-mapping"
        label="승인 Mapping revision"
        value={mappingIndex}
        onChange={selectMapping}
        disabled={disabled}
        options={snapshot.approved_mapping_revisions.map((value, index) => ({ value: String(index), label: `${value.supplier_scope} · ${value.template_id} rev.${value.revision}` }))}
      />
      {mapping ? (
        <>
          <MappingSelectionEvidence mapping={mapping} />
          <SelectField id="config-binding-row" label="exact Mapping row_key" value={rowKey} onChange={setRowKey} disabled={disabled} options={mapping.rows.map((value) => ({ value: value.row_key, label: `${value.row_key} · ${value.item_source.sheet_name}!${value.item_source.coordinate}` }))} />
        </>
      ) : null}
      {row ? <MappingRowEvidence row={row} /> : null}
      {mapping && row && !binding ? (
        <form className="configuration-command" onSubmit={create}>
          {!mapping.model_source ? <p className="form-error">승인 Mapping에 MODEL source cell이 없어 exact source model 값을 연결할 수 없습니다.</p> : null}
          <div className="field"><label htmlFor="config-binding-source-models">원본 MODEL exact 문자열 (한 줄에 하나)</label><textarea id="config-binding-source-models" className="text-input reason-input" value={sourceModels} onChange={(event) => setSourceModels(event.target.value)} disabled={disabled || !mapping.model_source} required /><p className="field__hint">표시된 MODEL source cell과 대조한 문자열만 입력하세요. fuzzy·alias 추론은 하지 않습니다.</p></div>
          <div className="configuration-select-grid">
            <SelectField id="config-binding-model" label="Canonical Model" value={modelKey} onChange={(value) => { setModelKey(value); setPartKey(""); setItemKey(""); }} disabled={disabled} options={snapshot.models.map((item) => ({ value: item.model_key, label: `${item.display_name} (${item.model_key})` }))} />
            <SelectField id="config-binding-supplier" label="Canonical Supplier" value={supplierKey} onChange={setSupplierKey} disabled={disabled} options={snapshot.suppliers.map((item) => ({ value: item.supplier_key, label: `${item.display_name} (${item.supplier_key})` }))} />
            <SelectField id="config-binding-part" label="Canonical ModelPart" value={partKey} onChange={(value) => { setPartKey(value); setItemKey(""); }} disabled={disabled || !modelKey} options={parts.map((item) => ({ value: item.model_part_key, label: `${item.display_name} (${item.model_part_key})` }))} />
            <SelectField id="config-binding-item" label="결정된 InspectionItem" value={itemKey} onChange={setItemKey} disabled={disabled || !partKey} options={items.map(itemOption)} />
            <SelectField id="config-binding-mode" label="측정 모드" value={measurementMode} onChange={(value) => setMeasurementMode(value as MeasurementMode)} disabled={disabled} options={[{ value: "NUMERIC", label: "NUMERIC" }, { value: "QUALITATIVE", label: "QUALITATIVE" }, { value: "JUDGMENT_ONLY", label: "JUDGMENT_ONLY" }]} />
            <SelectField id="config-binding-policy" label="샘플 정책" value={samplePolicy} onChange={(value) => setSamplePolicy(value as SamplePolicy)} disabled={disabled} options={[{ value: "AT_LEAST_ONE", label: "AT_LEAST_ONE" }, { value: "ZERO_ALLOWED", label: "ZERO_ALLOWED" }]} />
          </div>
          <p className="field__hint">JUDGMENT_ONLY는 ZERO_ALLOWED와, 나머지 모드는 AT_LEAST_ONE과 함께 명시해야 합니다.</p>
          <DatePeriodFields prefix="config-binding" effectiveFrom={effectiveFrom} effectiveTo={effectiveTo} setEffectiveFrom={setEffectiveFrom} setEffectiveTo={setEffectiveTo} disabled={disabled} />
          <ReasonInput id="config-binding-draft-reason" label="Row Binding Draft 생성 이유" value={draftReason} onChange={setDraftReason} disabled={disabled} />
          <p className="configuration-cas">첫 history CAS: expected_history_row_version <strong>0</strong> · Binding revision은 서버가 1로 생성</p>
          <button className="secondary-button" type="submit" disabled={disabled || !mapping.model_source || !sourceModels.trim()}>Row Binding Draft 생성</button>
        </form>
      ) : null}
      {binding ? (
        <div className="configuration-workflow">
          <BindingEvidence binding={binding} />
          {binding.status === "DRAFT" ? (
            <div className="configuration-command">
              <ReasonInput id="config-binding-review-reason" label="Row Binding 검토 완료 이유" value={reviewReason} onChange={setReviewReason} disabled={disabled} />
              <p className="field__hint">LOCAL_OWNER의 검토 명령이며 승인을 자동 실행하지 않습니다.</p>
              <button className="secondary-button" type="button" onClick={() => void transition("review")} disabled={disabled || !reviewReason.trim()}>Row Binding 검토 완료 (REVIEWER)</button>
            </div>
          ) : null}
          {binding.status === "REVIEWED" ? (
            <div className="configuration-command">
              <ReasonInput id="config-binding-approve-reason" label="Row Binding 최종 승인 이유" value={approveReason} onChange={setApproveReason} disabled={disabled} />
              <button className="submit-button" type="button" onClick={() => void transition("approve")} disabled={disabled || !approveReason.trim()}>Row Binding 최종 승인 (ADMIN)</button>
            </div>
          ) : null}
          {binding.status === "APPROVED" ? <p className="configuration-ready">Binding이 승인되었습니다. 기존 결과를 자동 변경하지 않으므로 Long 후보 만들기를 다시 실행하세요.</p> : null}
        </div>
      ) : null}
      <p className="configuration-limit">후속 Binding revision과 supersede는 이 화면에서 지원하지 않습니다.</p>
    </section>
  );
}

function MappingSelectionEvidence({ mapping }: { mapping: ApprovedMappingSelection }) {
  return (
    <div className="configuration-evidence">
      <div className="section-heading"><h6>승인 Mapping provenance</h6><span className="section-count">schema {mapping.schema_version} · {mapping.status}</span></div>
      <dl className="evidence-grid">
        <Evidence label="Supplier scope" value={mapping.supplier_scope} />
        <Evidence label="Template revision" value={`${mapping.template_id} · rev.${mapping.revision}`} />
        <Evidence label="MODEL source" value={cellLabel(mapping.model_source)} />
        <Evidence label="Supplier source aliases" value={mapping.supplier_source_aliases.join(" · ") || "없음"} />
        <Evidence label="적용기간" value={periodLabel(mapping.declared_effective_from, mapping.resolved_effective_to ?? mapping.declared_effective_to)} />
        <Evidence label="Mapping CAS" value={`history ${mapping.history_row_version} / revision ${mapping.revision_row_version}`} />
        <Evidence label="History ID" value={mapping.history_id} wide />
        <Evidence label="Revision ID" value={mapping.revision_id} wide />
        <Evidence label="Payload SHA-256" value={mapping.payload_sha256} wide />
      </dl>
    </div>
  );
}

function MappingRowEvidence({ row }: { row: MappingRowSelection }) {
  const roles: Array<[string, ConfigurationCellSource | null]> = [
    ["검사항목", row.item_source], ["측정방법", row.method_source], ["측정기", row.instrument_source],
    ["원본 사양", row.specification_source], ["공차", row.tolerance_source], ["최소", row.minimum_source],
    ["최대", row.maximum_source], ["업체 결과", row.supplier_result_source], ["구역", row.section_source],
    ["분류", row.category_source], ["단위", row.unit_source], ["측정점", row.measurement_point_source],
    ["측정 위치", row.measurement_location_source], ["Cavity", row.cavity_source], ["Target", row.target_source],
    ["LSL", row.lsl_source], ["USL", row.usl_source], ["원본 Spec revision", row.source_spec_revision_source],
  ];
  return (
    <div className="configuration-row-evidence">
      <div className="section-heading"><h6>선택한 source row evidence</h6><span className="section-count">{row.row_key} · row {row.row_index}</span></div>
      <div className="configuration-role-grid">
        {roles.map(([label, source]) => <span key={label}><strong>{label}</strong>{cellLabel(source)}</span>)}
        <span><strong>샘플 셀</strong>{row.sample_cells.map(cellLabel).join(" · ") || "없음"}</span>
      </div>
    </div>
  );
}

function MasterEvidence({ master }: { master: MasterSpecResource }) {
  return (
    <div className="configuration-evidence">
      <div className="status-card" data-tone={master.status === "APPROVED" ? "normal" : "warning"}>
        <p className="status-card__label">Master Spec workflow</p><h6 className="status-card__title">{master.status}</h6>
        <p className="status-card__message">{master.external_spec_revision} · {master.unit} · Target/LSL/USL {master.target ?? "-"}/{master.lsl ?? "-"}/{master.usl ?? "-"}</p>
      </div>
      <WorkflowCasEvidence historyId={master.history_id} revisionId={master.revision_id} payload={master.payload_sha256} historyVersion={master.history_row_version} revisionVersion={master.revision_row_version} period={periodLabel(master.declared_effective_from, master.resolved_effective_to ?? master.declared_effective_to)} />
    </div>
  );
}

function BindingEvidence({ binding }: { binding: RowBindingResource }) {
  return (
    <div className="configuration-evidence">
      <div className="status-card" data-tone={binding.status === "APPROVED" ? "normal" : "warning"}>
        <p className="status-card__label">Row Binding workflow</p><h6 className="status-card__title">{binding.status}</h6>
        <p className="status-card__message">{binding.row_key} → {binding.canonical_model_key} / {binding.canonical_model_part_key} / {binding.canonical_item_key} · {binding.canonical_supplier_key}</p>
      </div>
      <WorkflowCasEvidence historyId={binding.history_id} revisionId={binding.revision_id} payload={binding.payload_sha256} historyVersion={binding.history_row_version} revisionVersion={binding.revision_row_version} period={periodLabel(binding.declared_effective_from, binding.resolved_effective_to ?? binding.declared_effective_to)} />
    </div>
  );
}

function WorkflowCasEvidence({ historyId, revisionId, payload, historyVersion, revisionVersion, period }: { historyId: string; revisionId: string; payload: string; historyVersion: number; revisionVersion: number; period: string }) {
  return <dl className="evidence-grid"><Evidence label="적용기간" value={period} /><Evidence label="CAS row_version" value={`history ${historyVersion} / revision ${revisionVersion}`} /><Evidence label="History ID" value={historyId} wide /><Evidence label="Revision ID" value={revisionId} wide /><Evidence label="Payload SHA-256" value={payload} wide /></dl>;
}

function ConfigForm({ title, onSubmit, disabled, submitLabel, children }: { title: string; onSubmit: (event: FormEvent<HTMLFormElement>) => void; disabled: boolean; submitLabel: string; children: ReactNode }) {
  return <form className="configuration-command" onSubmit={onSubmit}><h6>{title}</h6>{children}<button className="secondary-button" type="submit" disabled={disabled}>{submitLabel}</button></form>;
}

function TextField({ id, label, value, onChange, disabled, inputMode, required = true }: { id: string; label: string; value: string; onChange: (value: string) => void; disabled: boolean; inputMode?: "decimal"; required?: boolean }) {
  return <div className="field"><label htmlFor={id}>{label}</label><input id={id} className="text-input" value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} required={required} inputMode={inputMode} autoComplete="off" /></div>;
}

function SelectField({ id, label, value, onChange, disabled, options }: { id: string; label: string; value: string; onChange: (value: string) => void; disabled: boolean; options: Array<{ value: string; label: string }> }) {
  return <div className="field"><label htmlFor={id}>{label}</label><select id={id} className="text-input" value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} required><option value="">선택하세요</option>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></div>;
}

function ReasonInput({ id, label, value, onChange, disabled }: { id: string; label: string; value: string; onChange: (value: string) => void; disabled: boolean }) {
  return <div className="field"><label htmlFor={id}>{label}</label><textarea id={id} className="text-input reason-input" value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} required maxLength={2000} /></div>;
}

function DatePeriodFields({ prefix, effectiveFrom, effectiveTo, setEffectiveFrom, setEffectiveTo, disabled }: { prefix: string; effectiveFrom: string; effectiveTo: string; setEffectiveFrom: (value: string) => void; setEffectiveTo: (value: string) => void; disabled: boolean }) {
  return <div className="field-row"><div className="field"><label htmlFor={`${prefix}-from`}>적용 시작일</label><input id={`${prefix}-from`} className="text-input" type="date" value={effectiveFrom} onChange={(event) => setEffectiveFrom(event.target.value)} disabled={disabled} required /><p className="field__hint">숨은 today 기본값을 사용하지 않습니다.</p></div><div className="field"><label htmlFor={`${prefix}-to`}>적용 종료일 (선택)</label><input id={`${prefix}-to`} className="text-input" type="date" value={effectiveTo} onChange={(event) => setEffectiveTo(event.target.value)} disabled={disabled} /></div></div>;
}

function Evidence({ label, value, wide = false }: { label: string; value: string; wide?: boolean }) {
  return <div className={`evidence-item${wide ? " evidence-item--wide" : ""}`}><dt>{label}</dt><dd className={wide ? "hash-value" : undefined}>{value}</dd></div>;
}

function upsert<T>(values: T[], value: T, key: (item: T) => string): T[] {
  const next = values.filter((item) => key(item) !== key(value));
  return [...next, value].sort((left, right) => key(left).localeCompare(key(right)));
}

function upsertMaster(snapshot: ConfigurationSnapshot, value: MasterSpecResource): ConfigurationSnapshot {
  return { ...snapshot, master_specs: upsert(snapshot.master_specs, value, (item) => `${item.canonical_item_key}:${item.revision}`) };
}

function upsertBinding(snapshot: ConfigurationSnapshot, value: RowBindingResource): ConfigurationSnapshot {
  return { ...snapshot, row_bindings: upsert(snapshot.row_bindings, value, bindingIdentity) };
}

function bindingIdentity(value: RowBindingResource): string {
  return `${value.supplier_scope}\u0000${value.template_id}\u0000${value.template_revision}\u0000${value.row_key}\u0000${value.binding_revision}`;
}

function bindingMatches(value: RowBindingResource, mapping: ApprovedMappingSelection, rowKey: string): boolean {
  return value.supplier_scope === mapping.supplier_scope && value.template_id === mapping.template_id && value.template_revision === mapping.revision && value.row_key === rowKey && value.binding_revision === 1;
}

function itemOption(item: CanonicalInspectionItemResource): { value: string; label: string } {
  return { value: item.item_key, label: `${item.display_name} (${item.item_key}) · ${item.disposition}` };
}

function exactValues(values: string[]): string[] | null {
  const normalized = values.map((value) => value.trim());
  return normalized.every(Boolean) ? normalized : null;
}

function optionalExact(value: string): string | null {
  const normalized = value.trim();
  return normalized || null;
}

function cellLabel(value: ConfigurationCellSource | null): string {
  return value ? `${value.sheet_name}!${value.coordinate}` : "없음";
}

function periodLabel(start: string, end: string | null): string {
  return `${start} ~ ${end ?? "열린 기간"}`;
}

function toConfigurationError(reason: unknown): { title: string; message: string; code: string } {
  if (reason instanceof ConfigurationApiError) return { title: reason.statusLabel, message: reason.message, code: reason.code };
  return { title: "설정 처리 오류", message: SAFE_REQUEST_ERROR, code: "CONFIGURATION_REQUEST_FAILED" };
}

function isAbortError(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}
