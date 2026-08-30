# WORK_OQC_SYSTEM_ARCHITECTURE

**Work Type:** 수행영역 Living Context  
**관리 대상:** OQC Module 전체 구조 / Scheduler 연계 / AI·Code 분리 / 성능·부하  
**상태:** 개념설계 확정 / 물리설계 전  
**Core:** `02_OQC_WORK_CORE.md`

---

## 1. 목적

업체 OQC를 지속 처리하더라도 프로그램과 AI 부하가 과도하게 증가하지 않도록, **메일 Queue → Excel 1회 해석 → 표준 DB → 증분통계 → 이상 시 AI 보조 → Dashboard** 구조를 확정한다.

핵심 원칙:

> **일반 코드가 반복·계산 업무를 담당하고, AI는 신규 구조와 애매한 의미, 이상 설명에만 사용한다.**

---

## 2. Valonark 내 Module 위치

OQC는 완전 독립 실행도구보다 **기존 Valonark AI 명령창에서 `OQC`를 선택하거나 입력하여 진입하는 별도 Module**로 운영한다.

기본 진입:

`Valonark → OQC → 모델별 현황판`

OQC Module이 켜질 때 Queue의 미처리건을 확인하고, 켜져 있는 동안에는 설정된 주기로 Queue만 가볍게 Polling한다. 신규 데이터가 있어도 기존 Dashboard를 먼저 사용할 수 있으며 Parsing·통계갱신은 Background에서 수행한다. 사용자 판단이 필요한 신규등록·Mapping·이상만 상단에 표시한다.

---

## 3. 기존 Scheduler와의 경계

### Scheduler 역할

- 메일 전체를 읽는다.
- OQC 관련 메일을 식별한다.
- 해당 메일의 고유번호를 OQC Queue에 저장한다.

### OQC Module 역할

- 메일함 전체를 다시 검색하지 않는다.
- Queue의 미처리 Mail ID만 확인한다.
- 해당 메일의 첨부 Excel을 가져온다.
- 분석·표준화·DB 적재·이상알림·Dashboard를 담당한다.

메일 고유번호는 내부 중복방지와 재처리를 위한 키로 사용하되, Dashboard에서 원본 추적정보를 과도하게 노출하지 않는다. 사용자에게는 수신일 중심으로 원본을 찾을 수 있게 한다.

---

## 4. 전체 처리 Architecture

```text
[Existing Scheduler]
    └─ OQC Mail ID Queue
             ↓
[Queue Manager]
             ↓
[Attachment Fetcher]
             ↓
[Excel Structure Scanner]
    ├─ Existing Mapping → Deterministic Parser
    └─ New/Changed Mapping → AI Mapping + User Confirmation
             ↓
[Standardization & Validation]
             ↓
[Standard OQC DB + Original File Store]
             ↓
 ┌───────────────┬────────────────┬──────────────────┐
 │ Statistics    │ Rule Engine    │ Shipment Engine  │
 │ Mean/Cpk/etc. │ Spec/Gap/etc.  │ Qty/Coverage     │
 └───────────────┴────────────────┴──────────────────┘
             ↓
[Issue / Supplier / Stability Engine]
             ↓
[AI Explanation only for exceptions or on-demand analysis]
             ↓
[OQC Dashboard / OQC Program Notification / Excel Export]
```

---

## 5. 주요 Component

### 5.1 Queue Manager

Queue 최소 필드:

- Mail ID
- Detected Date
- Status
- Retry Count
- Last Error

Status:

- `PENDING`
- `PROCESSING`
- `DONE`
- `REGISTRATION_REQUIRED`
- `MAPPING_REQUIRED`
- `ERROR`

재가동 시 `DONE`은 재처리하지 않고, `ERROR`와 중단된 `PROCESSING`만 안전하게 재시도한다.

### 5.2 Attachment Fetcher

- 하나의 메일에 여러 Excel이 있어도 각각 식별한다.
- 여러 모델이 섞인 경우 모델 단위로 분리한다.
- 동일 파일 재전송·동일 LOT 수정본 가능성을 후속 Validation에 전달한다.

### 5.3 Excel Structure Scanner

AI 호출 전에 일반 코드가 다음을 추출한다.

- Sheet명 / Hidden 여부
- 사용 영역
- 병합영역
- Header 후보
- 데이터 Type 분포
- 수식·외부참조·오류
- 반복 Header / 중간 Section
- 대표행 / 대표열

AI에는 전체 숫자배열보다 압축된 구조정보를 전달한다.

### 5.4 Mapping Engine

- 등록된 양식은 저장된 Mapping Template으로 처리한다.
- 양식 변경은 신뢰도 기준으로 자동 재매핑 또는 부분확인을 요청한다.
- 신규 양식은 대표 1건을 사용자와 확정한 뒤 동일 양식에 재사용한다.

### 5.5 Standard OQC DB

논리 Entity:

- Model
- Part
- Supplier
- Supplier Part Name Mapping
- Inspection Item
- Item Name Mapping
- Master Spec Revision
- Inspection Method Revision
- OQC Submission Standard Revision
- OQC Lot
- Production Subgroup
- Measurement
- Shipment
- OQC-Shipment Link
- Issue
- Change Event
- Supplier Evaluation
- Supply Stability
- Mapping Template
- Configuration
- Audit Log

초기 물리 DB는 SQLite로 시작 가능하되, UI·배포환경 확정 전 기술선택을 고정하지 않는다.

### 5.6 Statistics Engine

- 평균, Min, Max, Range, 표준편차
- Cp/Cpk/Pp/Ppk
- 최근 N Lot
- UCL/LCL
- Outlier·Trend Rule
- 변경 전후 통계

AI를 호출하지 않는다.

### 5.7 Rule Engine

- Spec 판정
- 업체 판정 재검증
- 필수항목·Sample·주기 누락
- Spec·공차·측정방법 변경
- 데이터 상태전이
- Issue 생성·지속·악화·정상화
- 업체평가와 공급 안정성 Rule

### 5.8 Shipment Engine

- 금번·누적 출하수량 산술검증
- 업체 누적과 DB 누적 대조
- 계획/실제/잔량/Exposure
- OQC Coverage
- 다대다 LOT-출하 연결

### 5.9 AI Explanation Layer

AI 호출 조건:

- 신규·애매한 Mapping
- 신규 측정방법 적절성 검토
- Rule Engine이 실제 이상을 생성한 경우
- 사용자가 심층 분석을 실행한 경우

AI가 반환할 내용:

- 이상 의미 요약
- 근거 기반 원인 후보
- 추가 확보정보
- 업체 확인사항 초안

AI는 공식 계산값·점수·Spec을 생성하지 않는다.

---

## 6. 부하 최소화 설계

### 6.1 최초 1회 Parsing

- 원본 Excel은 최초 등록 시 한 번만 구조해석한다.
- 표준 DB 적재 후 일반 조회와 통계는 Excel을 다시 열지 않는다.
- 원본 추적 또는 수정본 비교 시에만 해당 파일을 연다.

### 6.2 증분 계산

신규 데이터가 `모델 A / 부품 B / 업체 C / 높이`에 추가되면 해당 Segment만 갱신한다.

- 해당 항목 누적통계
- 최근 N Lot
- Cpk·Control Limit
- 관련 Issue
- 해당 업체평가 기여값
- 해당 공급 안정성

다른 모델·부품·검사항목은 재계산하지 않는다.

### 6.3 통계 Cache

검사항목·비교조건별 Cache 예:

- Count
- Mean
- 분산 갱신용 누적값
- Min / Max
- 최근 5 Lot / 최근 10 Lot 요약
- Current Cpk/Ppk
- Current UCL/LCL
- 최근 이상 발생시점
- Data Version

Dashboard는 Cache를 우선 사용하고 Raw Measurement는 상세 Drill-down 때 조회한다.

### 6.4 경량 판단이력

과거 분석 당시의 전체 평균·그래프·분포를 중복 Snapshot으로 저장하지 않는다.

판단이력에는 최소한 다음만 저장한다.

- 분석일
- 대상 LOT / Data Version
- 당시 이상항목과 근거
- 당시 판정
- 사용자 확인·조치

상세 통계는 원본 표준 DB에서 필요 시 재계산한다. 따라서 데이터가 추가된 현재 기준 재평가와 당시 판단근거를 둘 다 확인할 수 있다.

### 6.5 On-demand 심층분석

상시 계산하지 않는다.

- 신규 Spec 후보 검토
- 전체 과거 데이터 재평가
- 기간 A ↔ B 심층 비교
- 과거 모델 Reference 상세분석
- 분포 전체 중첩

분석결과에는 사용한 DB Version을 저장하고 데이터가 추가되면 `재분석 필요`로 표시한다.

### 6.6 Lazy UI

- 모델 현황은 요약 Cache로 즉시 표시한다.
- 검사항목 상세 그래프는 항목 클릭 시 로딩한다.
- 분포그래프는 추가 분석을 눌렀을 때만 계산한다.

---

## 7. AI Failure 독립성

AI 호출이 실패해도 다음은 정상 동작해야 한다.

- Excel 기존 Mapping 처리
- 표준 DB 적재
- Spec 판정
- 평균·Cpk·Trend·Outlier
- 출하수량·Coverage
- Issue 상태
- 업체평가 점수
- 공급 안정성 Rule
- Dashboard / Export

AI 실패 시 필요한 항목만 `AI 설명 대기` 또는 `Mapping 확인 필요`로 남긴다.

---

## 8. 보안·원본 보호

- 모든 Excel은 읽기 전용으로 처리한다.
- VBA·매크로는 실행하지 않는다.
- `.xlsm`의 셀 값과 구조는 읽을 수 있으나 매크로 의존 값은 `갱신 필요`로 표시한다.
- 시트 보호를 우회하거나 비밀번호를 추정하지 않는다.
- 읽을 수 없는 암호화 영역은 정확한 Sheet·영역을 표시한다.
- 사진은 1차 분석대상에서 제외한다.

---

## 9. 알림 Architecture

알림은 Scheduler가 아니라 **OQC Module**이 만든다.

알림 대상:

- 신규 OQC 등록 필요
- Mapping 재확인
- 신규 이상
- 기존 이상 악화
- 정상화 후보
- Spec·공차 변경
- 데이터 신뢰성 Risk
- 정기검사 지연
- 출하 전 OQC Coverage 부족

알림 클릭 시 일반 홈이 아니라 해당 모델의 최신 Lot Dashboard와 관련 이상항목을 직접 연다.

이미 관리 중인 동일 이상이 같은 수준으로 지속될 때는 새 팝업을 만들지 않는다.

---

## 10. 관리자 권한 및 Audit

관리자만 수정:

- Master Spec
- 관리/제외 항목
- 검사주기
- 중요 CTQ
- 측정방법 승인
- 업체평가 가중치·Hard Gate
- 공급 안정성 Rule
- OQC 제출기준

변경 시 최소 기록:

- 변경 전 / 후
- 변경일
- 변경자
- 적용 시작일

일반 사용자는 조회·필터·Issue 확인·Export를 수행한다.

---

## 11. 현재 판단

### 확정

- Scheduler는 Mail ID 전달까지만 담당한다.
- OQC Module이 분석·알림·Dashboard를 담당한다.
- 반복 계산은 일반 코드가 담당한다.
- AI는 Mapping·예외 설명·On-demand 분석에 제한한다.
- 증분 계산·Cache·Excel 1회 Parsing이 기본이다.

### 구현 전 확인 필요

- 실제 배포형태와 UI Framework
- Scheduler Queue 저장 포맷과 첨부파일 접근방식
- 대표 OQC의 Excel 구조
- 예상 데이터량과 동시사용자 수

이 항목은 기능범위를 바꾸는 질문이 아니라 구현환경 확인 단계에서 정리한다.

---

## 12. Gate

### Architecture Gate 완료조건

- Queue Interface 정의
- 표준 DB 물리 Schema 확정
- 데이터 Status / Issue Status 전이 정의
- 통계 Cache Key 정의
- AI 호출 Input/Output Schema 정의
- 기존 Mapping 경로가 AI 없이 처리됨을 검증

---

## 13. Resume Point

대표 OQC 1건을 기준으로:

1. Excel Structure Scanner 출력형식 정의
2. Mapping Template JSON 또는 Table 구조 정의
3. Measurement Long Format 적재 Schema 정의
4. 신규 1건 증분통계 갱신 Prototype 작성
5. AI 미사용 상태에서도 Spec 재판정과 Dashboard 요약이 가능한지 확인

---

## 14. Living Decision Override — 2026-08-15

이 절은 불변 ZIP의 Baseline Snapshot을 수정하지 않고, 이후 사용자 합의를 이
Repository의 Living Context에 반영한다. 충돌 시 이 절과
`13_MASS_PRODUCTION_QUALITY_VALIDATION_CODEX_MASTER_IMPLEMENTATION_SPEC.md`의 Living Override를 우선한다.

### 배포와 소유권

- Mass Production Quality Validation는 사용자가 선택해 설치하는 **개인용 Cloud Scheduler 확장팩**이다.
- 설치하지 않은 사용자의 Scheduler 동작은 바뀌지 않아야 한다.
- 한 로컬 설치에서 여러 개인 프로젝트를 운영할 수 있지만 프로젝트별 DB/설정/원본
  File Store/파생결과를 격리한다. 서버형은 전환 경계만 열어 둔다.
- Mass Production Quality Validation의 코드, 데이터, 버전, update/remove, rollback은 Scheduler와 독립적으로
  관리한다.

### Mail Locator와 프로젝트 Routing

- Scheduler의 책임은 OQC 분류·이동 완료 후 **최종 Outlook Mail Locator**를 versioned
  envelope로 공통 inbox에 전달하는 데까지다.
- Mass Production Quality Validation는 전달된 참조만 fetch하며 메일함 전체를 재검색하거나 Scheduler DB/table에
  직접 연결하지 않는다.
- Scheduler는 DQ 프로젝트를 배정하지 않는다. Mass Production Quality Validation가 workbook 내부 식별 근거를
  우선해 routing한다.
- 미등록 대상은 `REGISTRATION_REQUIRED / PROJECT_NOT_REGISTERED`, 복수 후보나 충돌은
  `MAPPING_REQUIRED / PROJECT_ROUTING_AMBIGUOUS` reason으로 보류한다. 새로운 최상위 Queue
  상태를 임의로 추가하지 않는다.

### 공용 AI 설정

- AI endpoint, model, API key는 Scheduler에서 한 번 입력한다.
- Mass Production Quality Validation는 versioned provider profile과 현재 사용자 Secret Store의
  `credential_reference`만 소비한다. 평문 key 복제, 로그/DB 기록, Scheduler DB 직접
  조회는 금지한다.
- AI가 꺼지거나 실패해도 deterministic ingestion과 Core 기능은 계속 동작한다.

실제 Outlook provider, locator field, Queue transport, Secret Store/ACL, installer와
호환성 계약은 Phase 5 입력이며 현재 `DEFERRED_BY_PHASE` 또는 `BLOCKED_BY_INPUT`이다.

### Phase 1 원본 경계 보강 — 2026-08-15

- 프로젝트마다 content-addressed Original File Store 경로를 격리한다.
- 공개 Receipt, 향후 API와 오류에는 내부 절대경로를 포함하지 않고 path traversal을
  거부한다.
- 원본 보존 Transaction과 Workbook Scan은 분리한다. Scan 실패는 이미 보존된 Raw 원본과
  수신 Receipt를 rollback하지 않는다.
- 같은 프로젝트의 동일 SHA-256 재수신은 Blob을 재사용하되 각 수신 Receipt를 남긴다.

추적 Requirement는 `ARC-029`, `ARC-030`, `ING-052`, `ING-053`이다.
