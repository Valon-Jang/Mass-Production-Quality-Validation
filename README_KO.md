# Mass Production Quality Validation

제품·부품·자재·공정이 **양산 투입 가능한 상태인지** Evidence 기반으로 판정하는 범용 검증·Release Gate 코어입니다.

[English README](README.md)

## 핵심 관점

양산 준비상태는 하나의 `PASS/FAIL` 값으로 표현하기 어렵습니다.

시험은 PASS했어도 품질 검토가 끝나지 않았을 수 있고, 승인 근거가 부족하거나 외부 승인이 대기 중일 수 있으며, High Risk가 아직 열려 있을 수도 있습니다.

그래서 이 프로젝트는 다음 상태를 서로 분리해 관리합니다.

- Validation / Test 상태
- Quality Review 상태
- 필요 시 Approval 상태
- Evidence 완결성
- Risk 등급과 Open/Closed 상태
- 담당자와 목표일
- Next Action

**시험 PASS가 자동으로 양산 승인이나 Release를 의미하지 않습니다.**

## Release Gate

다음 중 하나라도 남아 있으면 해당 Item은 양산 Release를 막습니다.

- 필수 검증 미PASS
- 품질 검토 미승인
- 필수 승인 미승인
- Evidence 미완결
- Risk 미평가
- Open 상태의 HIGH/CRITICAL Risk

모든 Item이 조건을 만족할 때만 `READY`를 반환합니다.

## 실행

```bash
python -m mass_production_quality_validation examples/synthetic_portfolio.json --as-of 2026-08-31 --pretty
```

공개 예제는 모두 합성 데이터이며 회사, 고객, 제품, 공급사, Site, Lot, 생산정보를 포함하지 않습니다.

## v0.1 포함 범위

- Item별 Deterministic 평가
- Validation / Quality / Approval 분리
- Evidence Gate
- High/Critical Risk Gate
- 일정 지연 경보
- 전체 진행상태 Summary
- 양산 Release Gate
- JSON CLI
- 합성 예제
- 자동 테스트

향후 UI, 이력 DB, 공급사/ERP Adapter, FMEA/CAPA/Control Plan, AI 보조 계층 등을 확장할 수 있지만 현재 구현됐다는 의미는 아닙니다.
