# WORK_OQC_IMPLEMENTATION_ROADMAP

**Work Type:** 수행영역 Living Context  
**관리 대상:** 구현순서 / Test / Acceptance Gate / Risk / 현재 Resume Point  
**상태:** 요구사항 Baseline 확정 / 구현 착수 전  
**Core:** `02_OQC_WORK_CORE.md`

---

## 1. 목적

합의된 전체 기능을 한 번에 구현하여 복잡성과 오류를 키우지 않고, **데이터 정확성 → 누적 DB → 통계 → 자동수집 → Issue·출하 → 업체평가·공급안정성 → 심층분석** 순으로 Gate를 통과한다.

파트장 관점의 우선순위:

1. 데이터가 정확히 쌓이는가
2. 최신 데이터와 과거를 정확히 비교하는가
3. 이상과 근거를 직접 보여주는가
4. 실제 출하·업체관리 판단으로 이어지는가
5. 부하가 통제되는가

---

## 2. 구현 대원칙

- 정확한 DB가 확보되기 전 업체평가·공급안정성을 먼저 만들지 않는다.
- AI Demo보다 일반 코드의 재현 가능한 계산을 우선한다.
- 정상파일·정상항목은 자동처리하고 예외만 사용자에게 올린다.
- 기능별 Acceptance Gate를 통과한 뒤 다음 단계로 이동한다.
- 실제 사용자 승인값과 Test 결과를 WORK MD에 반영한다.
- 지속 부하 증가, 지속 AI 부하 증가, 완전히 새로운 기능은 사용자에게 먼저 확인한다.

---

## 3. Phase 0 — 프로젝트 기반

### 목표

새 OQC 프로젝트가 독립적으로 재개 가능한 상태를 만든다.

### 산출물

- 프로젝트 맞춤설정
- Core / WORK MD
- Requirement Traceability
- 대표 OQC 후보목록
- 원본 보관폴더 / 표준 DB 개발폴더

### Gate

- 파일구조 확정
- Scope / 제외 Scope 확정
- Resume Point 명확

**현재 상태:** 완료

---

## 4. Phase 1 — OQC Data Engine

### 목표

대표 OQC Excel 1건을 표준 DB로 정확히 변환한다.

### 구현범위

- Queue 없이 수동 단건 입력부터 시작 가능
- 전체 Sheet Scan
- 병합셀·Header·수식·실제셀값 구분
- 모델 / 부품 / 업체 / LOT / 날짜
- Spec / 단위 / 측정방법
- Sample Measurement
- 금번 / 누적 출하수량
- 원본 Sheet / Cell 추적
- Mapping Preview

### Acceptance Gate

- 원본과 추출값 100% 대조
- 측정값 누락·오배치 없음
- 수식과 Raw Data 구분
- 실제값과 표시값 구분
- 사용자 Mapping 확정 가능
- 동일 양식 두 번째 파일 자동처리

### 주요 Risk

- 업체 Excel 구조가 예상보다 비정형
- 동일 Sheet에 여러 Section·LOT 혼재
- 외부참조 수식

### 대응

- Excel Structure Scanner 결과를 먼저 시각화
- Mapping을 코드에 하드코딩하지 않고 Template화
- 읽을 수 없는 영역을 조용히 건너뛰지 않음

---

## 5. Phase 2 — 과거 DB 구축

### 목표

대표 1건 확정 후 과거 OQC 다량파일을 일괄 적재한다.

### 구현범위

- 대표 Mapping 재사용
- 양식 유사도 / 변경 Detection
- Spec / 항목 / Sample / 방법 변화
- 중복 / 동일 LOT / 수정본
- 부분 적재
- 데이터 상태
- 초기 데이터 품질 리포트

### Acceptance Gate

- 정상파일은 자동 적재
- 예외파일만 분리
- 날짜·LOT·Revision 충돌 표시
- 수정본 이력 보존
- 공식 기준선에 `VALID`만 포함
- 전체 적재요약과 미해결항목 수 일치

---

## 6. Phase 3 — 품질 Dashboard

### 목표

모델 → 부품 → 검사항목 누적 Trend를 빠르게 조회한다.

### 구현범위

- 모델카드
- 부품요약
- 검사항목 Card
- 최신 / 최근 N / 전체 Trend
- Spec / 평균 / Min / Max / σ
- Raw Sample Drill-down
- 업체 Filter
- 기본 Cache

### Acceptance Gate

- 모델카드 즉시 표시
- 항목 클릭 시 Raw Data 일치
- 업체 A/B Filter 정확
- 다른 모델 데이터 혼합 없음
- Archive 모델 분리 가능

---

## 7. Phase 4 — 통계·이상·신뢰성 Engine

### 목표

사람이 원본 OQC를 전수검토하지 않아도 이상을 선별한다.

### 구현범위

- Spec 재판정
- Cpk/Ppk
- UCL/LCL
- 평균 Shift / 산포 확대
- Outlier
- Point / Cavity
- Missing Sample
- Unit / Format / Precision
- Duplicate / Partial Duplicate / 반복패턴
- Spec·공차·방법 변경
- 최신 OQC 판단요약

### Acceptance Gate

- 의도적으로 만든 Test 이상을 모두 검출
- 정상데이터의 과도한 False Alarm 관리
- 이상위치와 비교대상 직접 표시
- 의심값 자동삭제 없음
- 사용자 정상 예외승인 반영
- Absolute Spec Gate는 예외학습으로 무시되지 않음

---

## 8. Phase 5 — Scheduler Queue 자동연계

### 목표

메일 수신부터 OQC 분석까지 자동화한다.

### 구현범위

- Queue Interface
- 시작 시 미처리건 확인
- 실행 중 주기적 Queue Polling
- Background 처리
- Retry / Error
- 신규등록 / Mapping 확인
- OQC Module 알림
- Deep Link

### Acceptance Gate

- 같은 Mail ID 중복처리 없음
- 실패건만 재시도
- 기존 Dashboard 사용 중 Background 처리 가능
- 신규/변경/이상만 사용자 확인
- 알림 클릭 시 해당 모델·항목 직접진입

---

## 9. Phase 6 — 출하·Coverage·Issue

### 목표

품질 이상을 실제 출하영향과 연결하고 지속 Issue로 관리한다.

### 구현범위

- 금번 / 누적수량 검증
- 업체누적 vs DB
- 분할출하 / 다대다 LOT
- 계획 / 실제 / 잔량
- Exposure
- OQC Coverage
- 출하 전 경고
- Issue 상태와 정상화
- 개선 전후

### Acceptance Gate

- 누적 불일치 최초시점 탐색
- Exposure 임의배분 금지
- Coverage 미확보 원인 구분
- 동일 Issue 연속추적
- 악화 / 정상화 / 재발 알림
- Issue 우선순위에 중대도·지속성·Margin·Exposure 반영

---

## 10. Phase 7 — 업체평가·공급 안정성

### 목표

누적 OQC와 출하실적을 기반으로 업체 품질수준과 향후 Risk를 판단한다.

### 구현범위

- 정상양산 중심 업체평가
- 최근 / 장기
- 평가 신뢰도
- 신뢰성 중대도·Hard Gate
- 이원화 CTQ 비교
- 생산단계
- 제품 품질 / OQC 운영 안정성
- 안정 / 관찰 / Risk

### Acceptance Gate

- 데이터 부족업체 정식순위 제외
- 생산초기 / 정상양산 분리
- 허위 의심 / 확인 분리
- 사건 중복감점 없음
- CTQ 약점이 평균에 묻히지 않음
- 최근 악화 선제표시

---

## 11. Phase 8 — Export·Reference·심층분석

### 목표

누적 DB를 당사 표준자료와 신규 기준설계에 활용한다.

### 구현범위

- 당사 표준 OQC Excel
- 업체평가 Export
- 제출기준 Export
- 기간비교
- 신규 Spec 검토
- 과거 모델 검색
- 유사모델 추천
- 신규 모델 초기제안안

### Acceptance Gate

- 모델 1개 = Excel 1개
- Summary + 부품 Sheet
- 관리항목 기본출력
- 과거/현재 판정 구분
- 심층분석 On-demand
- 유사모델 데이터 직접혼합 없음
- 설정만 복사, 과거 데이터 복사 없음

---

## 12. Test 전략

### 12.1 Golden Excel

원본과 기대추출값이 확정된 대표파일을 Test 기준으로 유지한다.

### 12.2 구조변형 Test

- Header 한 줄 추가
- Sheet명 변경
- 열 이동
- 숨김 Sheet
- 병합셀 변경
- 신규 항목

### 12.3 데이터 품질 Test

- 정확한 중복
- 부분 중복
- 반복패턴
- 빈칸
- 단위변경
- 표시값 / 실제값 차이
- 외부참조 수식
- NG→PASS 수정본

### 12.4 통계 Test

검증된 계산결과와:

- 평균
- 표준편차
- Cpk/Ppk
- Control Limit
- Outlier

을 대조한다.

### 12.5 출하 Test

- 누적 누락
- 누적 중복
- 누적 역전
- 분할출하
- 여러 LOT 한 출하
- Coverage 부분미확보

### 12.6 상태 Test

- Queue 재시도
- 부분 적재
- Issue 지속·악화·정상화
- 수정본 승인
- 통계 제외·복구
- 4M 전후 기준선

---

## 13. 성능·부하 Acceptance

정확한 수치기준은 실제 데이터량 확인 후 확정한다.

필수 원칙:

- 모델현황은 Cache로 즉시 조회
- 기존양식 1건 처리에 AI 호출 불필요
- 신규 OQC는 영향 Segment만 재계산
- 심층분석은 사용자 실행 시에만 동작
- AI 장애 중에도 핵심 Dashboard 동작

지속적인 부하 증가가 예상되면 사용자에게 먼저 구조변경을 보고한다.

---

## 14. 변경관리

새 요구사항 발생 시:

1. 기존 WORK 중 어느 영역인지 Routing
2. 기존 합의범위 내 세부인지 판단
3. 부하영향 검토
4. 완전히 새로운 Scope면 사용자 확인
5. Requirement Traceability 추가
6. 관련 WORK 및 Core 갱신

---

## 15. 현재 Risk

### Risk 1 — 대표 OQC 미확보

실제 파일 없이는 Mapping·Schema 세부확정 불가.

### Risk 2 — Scope가 넓음

Data Engine이 불안정한 상태에서 후속기능을 동시에 만들면 수정비용이 커짐.

### Risk 3 — 평가기준 과설계

실제 데이터 분포 확인 전 Threshold를 고정하면 False Alarm 또는 잘못된 순위 가능.

### Risk 4 — Scheduler Interface 미확정

Mail ID로 첨부를 어떻게 Fetch하는지 구현계약 필요.

---

## 16. 현재 완료 / 미완료

### 완료

- 전체 기능범위 합의
- MECE 수행영역 분리
- AI·Code 역할분리
- 부하대원칙
- 제외 Scope
- 구현 Gate 순서

### 미완료

- 대표 OQC 선정
- 실제 Schema DDL
- Mapping Preview Wireframe
- Queue Interface
- Threshold
- Export Template

---

## 17. 현재 Resume Point

**다음 작업은 Phase 1 Data Engine 착수다.**

즉시 필요한 입력:

- 실제 대표 OQC Excel 1건
- 가능하면 같은 양식의 과거 OQC 2~3건
- 해당 모델의 Master Spec 또는 승인기준

다음 산출물:

1. 표준 DB 물리 Schema
2. 대표 OQC Mapping Preview
3. 원본 ↔ 표준 DB 대조표
4. 첫 Mapping Template
5. Phase 1 Acceptance 결과

---

## 18. Living Roadmap Amendment — 2026-08-15

### 현재 배포 결정

- 첫 배포는 opt-in 개인용 `Cloud Scheduler extension pack — Mass Production Quality Validation`다.
- 단일 사용자·다중 개인 프로젝트를 지원하며 프로젝트 저장소는 격리한다.
- 서버형은 확장 가능한 Port와 storage boundary만 유지하고 현재 구현 Scope로 올리지
  않는다.

### Phase 조정

- Phase 0은 독립 Repository, 반복 가능한 Windows bootstrap, migration, audit/identity,
  requirement integrity와 release gate를 만든다.
- Phase 1은 local original File Store와 Workbook Scanner부터 시작한다. 대표 OQC가 없는
  동안 구조 Framework Test는 진행하되 Golden Acceptance는 `BLOCKED_BY_INPUT`이다.
- Scheduler/Outlook live adapter, 공통 inbox transport, 실제 AI shared profile/Secret
  Store, 확장팩 installer/update/remove/rollback은 모두 Phase 5다.
- Phase 0/1에서는 provider-neutral Port, DTO, Mock/In-memory adapter와 프로젝트 routing
  규칙까지만 허용한다.

### Phase 5 입력과 선행 Gate

Phase 5 착수에는 Outlook provider와 최종 Mail Locator, atomic Queue transport, 첨부 Fetch,
Scheduler extension discovery, AI profile/Secret Store ACL, 설치본 version/hash와 호환성
matrix가 필요하다. 실제 Scheduler Workspace 변경은 별도 승인과 양쪽 Impact Map, contract
test, rollback 계획 이후에만 수행한다.

세부 요청과 acceptance ID는
`../integration/CLOUD_SCHEDULER_MASS_PRODUCTION_QUALITY_VALIDATION_EXTENSION_REQUEST.md`를 따른다.

### 2026-08-15 Resume Point 갱신

- 구현 기반 Phase 0 Gate: `PASS`
- Phase 1 첫 수직 Slice: `IN_PROGRESS`
- 현재 순서: 프로젝트 격리 File Store -> Workbook Scanner -> 동일 수동 Route -> Mapping
  Template/Preview
- 실제 대표 OQC Golden Acceptance: `BLOCKED_BY_INPUT`
