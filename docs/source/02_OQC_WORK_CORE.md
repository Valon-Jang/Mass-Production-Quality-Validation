# OQC_WORK_CORE

**Document Type:** 프로젝트 수행영역 Routing / 상태관리 Core  
**Project:** Valonark OQC AI  
**기준일:** 2026-08-15

---

## 1. 목적

이 문서는 Valonark OQC AI 프로젝트의 **수행영역 라우팅, 정보 우선순위, 공통 상태, 변경관리, Resume Point**를 관리한다.

개별 기능의 상세 요구사항을 중복 저장하지 않는다. 상세설계와 현재 상태는 관련 `WORK_OQC_*.md`를 우선한다.

---

## 2. 프로젝트 구조

```text
Valonark OQC AI Project
│
├─ 01_OQC_PROJECT_CUSTOM_INSTRUCTIONS.md
│   └─ OQC AI 판단방식 / 운영원칙 / 사용자 확인 경계
│
├─ 02_OQC_WORK_CORE.md
│   └─ 수행영역 Routing / 공통 상태 / Resume
│
├─ 03_WORK_OQC_SYSTEM_ARCHITECTURE.md
│   └─ Scheduler 연계 / Module 구조 / AI·Code 분리 / 부하
│
├─ 04_WORK_OQC_INGESTION_MAPPING.md
│   └─ Excel 수집 / 신규등록 / Mapping / 표준 DB 적재
│
├─ 05_WORK_OQC_MASTER_CONFIG.md
│   └─ Master Spec / 제출기준 / 관리항목 / 주기 / 측정방법
│
├─ 06_WORK_OQC_QUALITY_ANALYTICS.md
│   └─ 통계 / 최신 DB 비교 / 이상·신뢰성 탐지
│
├─ 07_WORK_OQC_SHIPMENT_EXPOSURE.md
│   └─ 출하수량 / 누적검증 / Exposure / Coverage
│
├─ 08_WORK_OQC_ISSUE_SUPPLY_STABILITY.md
│   └─ 알림 / Issue / 개선효과 / 생산단계 / 공급 안정성
│
├─ 09_WORK_OQC_SUPPLIER_EVALUATION.md
│   └─ 업체평가 / 이원화 비교 / 데이터 신뢰성 Penalty
│
├─ 10_WORK_OQC_DASHBOARD_EXPORT_REFERENCE.md
│   └─ Dashboard / 표준 Excel Export / 기간·Spec·Reference 분석
│
├─ 11_WORK_OQC_IMPLEMENTATION_ROADMAP.md
│   └─ 구현 단계 / Test / Acceptance Gate / 현재 Resume
│
└─ 12_OQC_REQUIREMENTS_TRACEABILITY.md
    └─ 전체 합의사항 Requirement ID / 반영 위치 / 누락 점검
```

---

## 3. 기본 운영 원칙

> **1 Project = OQC 표준 데이터·판단체계**  
> **1 WORK MD = 하나의 독립 수행영역**  
> **현재 대화의 최신 지시 = 최우선**

기본 처리흐름은 다음과 같다.

```text
Scheduler가 OQC 메일 ID Queue 저장
→ OQC Module이 미처리 ID만 확인
→ 첨부 Excel 확보
→ 기존 Mapping 자동처리 / 신규 대상 등록확인
→ 표준 DB 적재
→ 최신 데이터와 검증 DB 증분 비교
→ 정상은 조용히 누적
→ 이상·변경·확인필요만 OQC Module 알림
→ 해당 모델 Dashboard로 직접 진입
```

---

## 4. 공통 데이터 계층

### 4.1 기본 계층

**모델 → 부품 → 검사항목**

업체는 동일 모델·부품·검사항목의 비교 및 평가축이다.

### 4.2 선택 비교축

OQC에 존재하고 유효할 때만 사용한다.

- 업체
- 생산공장 / Line
- 금형 No.
- Cavity
- 생산일
- 작업조
- Spec Revision
- 측정방법
- 생산단계

### 4.3 모델 간 관계

- 동일 모델 데이터는 장기 누적한다.
- 다른 모델 데이터는 별도 DB Context로 유지한다.
- 유사·종료 모델은 Reference로 조회하되 같은 통계에 합산하지 않는다.

---

## 5. 공통 상태모델

### 5.1 Queue 상태

- `PENDING`
- `PROCESSING`
- `DONE`
- `REGISTRATION_REQUIRED`
- `MAPPING_REQUIRED`
- `ERROR`

### 5.2 데이터 상태

- `VALID`: 공식 통계 반영
- `PENDING`: 사용자 확인 전
- `SUSPECT`: 데이터 신뢰성 의심
- `EXCLUDED`: 공식 통계 제외
- `REPLACED`: 수정본으로 대체된 이력값

### 5.3 Issue 상태

- `미확인`
- `확인 완료`
- `조치 중`
- `정상화 확인`
- `Close`

알림 확인과 Issue 해결은 다른 상태다.

### 5.4 모델·공급 상태

- 활성 공급
- 일시 공급중단
- 종료 / Archive
- 재공급 검증

### 5.5 생산단계

- 초기 생산 / Ramp-up
- 안정화 확인 중
- 정상 양산
- 4M·금형수정 후 재안정화
- 장기 공급중단 후 재공급 검증

---

## 6. 정보 우선순위

1. 현재 대화에서 사용자가 수정한 내용
2. 관련 `WORK_OQC_*.md`
3. 본 Core
4. 프로젝트 맞춤설정
5. 일반 지식·AI 추론

공식 원본 데이터와 사용자 승인 이력보다 AI 추론을 우선하지 않는다.

---

## 7. 수행영역 Routing

| 사용자 요청 / 키워드 | 우선 참조 파일 |
|---|---|
| Scheduler, Queue, Module, AI 부하, Cache, DB, 상태, Retry | `03_WORK_OQC_SYSTEM_ARCHITECTURE.md` |
| Excel 읽기, 신규 OQC, Mapping, 대량 적재, 숨김 Sheet, 수식, 수정본 | `04_WORK_OQC_INGESTION_MAPPING.md` |
| Master Spec, 공차, 제출 필수항목, 관리 제외, CTQ, 검사주기, 측정방법 | `05_WORK_OQC_MASTER_CONFIG.md` |
| 평균, Cpk, UCL/LCL, Trend, Outlier, 반복값, 구라 의심, Point/Cavity | `06_WORK_OQC_QUALITY_ANALYTICS.md` |
| 금번/누적 출하, 수량 불일치, Exposure, 잔량, OQC Coverage | `07_WORK_OQC_SHIPMENT_EXPOSURE.md` |
| 알림, 동일 이슈 지속, Close, 개선효과, 4M, Ramp-up, 공급 안정성 | `08_WORK_OQC_ISSUE_SUPPLY_STABILITY.md` |
| 업체점수, 신뢰성 감점, 허위, 이원화 비교, CTQ 우위, 공급비중 근거 | `09_WORK_OQC_SUPPLIER_EVALUATION.md` |
| 화면, 모델카드, Export, 당사 양식, 기간비교, 신규 Spec, 유사모델 | `10_WORK_OQC_DASHBOARD_EXPORT_REFERENCE.md` |
| 구현순서, MVP, Test, Gate, 다음 개발작업 | `11_WORK_OQC_IMPLEMENTATION_ROADMAP.md` |
| 전체 요구사항 누락 검증 | `12_OQC_REQUIREMENTS_TRACEABILITY.md` |

하나의 요청이 여러 영역에 걸치면 가장 직접적인 WORK를 주 문서로 두고 관련 WORK를 함께 참조한다.

---

## 8. MECE 소유권 경계

각 WORK는 아래 결과물만 공식 소유한다. 인접 문서는 필요한 입력·출력만 참조하고 같은 상세규칙을 별도로 확정하지 않는다.

| WORK | 공식 소유범위 | 소유하지 않는 범위 |
|---|---|---|
| System Architecture | Queue, Module, Component, 상태, 성능, AI·Code 경계 | 검사항목별 품질판정 상세 |
| Ingestion & Mapping | Excel 해석, 식별, Mapping, 표준 DB 적재, 수정본 | Cpk·공급안정성 결론 |
| Master & Config | Spec, 제출기준, 관리 Scope, 주기, 방법, CTQ | 최신 Lot 이상결과 |
| Quality Analytics | 계산, 비교군, 이상·신뢰성 탐지 | Issue Close·업체점수 |
| Shipment & Exposure | 수량, 누적, LOT-출하 연결, Exposure, Coverage | 제품 품질판정 |
| Issue & Stability | 알림, Issue 생애주기, 개선효과, 생산단계, 공급안정성 | 업체 공식 평가점수 |
| Supplier Evaluation | 업체 평가축, Penalty, 이원화 우위, 평가확정 | 원본 Parsing·Issue 상태전이 |
| Dashboard & Export | 화면, Drill-down, Export, On-demand 분석 UX | 계산식·기준의 공식 Source |
| Implementation Roadmap | 개발순서, Test, Acceptance Gate | 업무 Rule의 신규 확정 |

Interface 예:

- Analytics가 이상을 검출하면 Issue가 상태를 관리한다.
- Shipment가 Exposure를 계산하면 Issue 우선순위와 Stability가 사용한다.
- Master가 유효 Spec을 제공하면 Analytics가 판정한다.
- Supplier Evaluation은 Analytics·Shipment·Issue의 확정 Indicator만 소비한다.
- Dashboard는 각 Engine 결과를 표시하되 자체적으로 다른 판정을 만들지 않는다.

---

## 9. 공통 확정사항

### 8.1 프로그램의 중심

- 최신 OQC 한 건을 보는 Viewer가 아니다.
- 같은 모델의 누적 DB를 기준으로 최신 OQC를 비교하는 판단 모듈이다.
- 이상 항목은 정확한 위치·값·비교대상·사유를 보여준다.

### 8.2 자동화 범위

- 기존 등록 양식은 자동처리한다.
- 신규 OQC는 등록 여부를 사용자에게 확인한다.
- 대표 1건 Mapping 확정 후 동일 양식 다량파일은 자동처리한다.
- 정상 데이터는 자동 누적하고 문제 있는 항목만 보류한다.

### 8.3 통계 기준

- 공식 기준선은 `VALID`만 사용한다.
- Spec 판정과 Trend 이상을 분리한다.
- 전체 누적과 최근 N Lot을 동시에 본다.
- 동일 Spec Rev.·동일 측정방법·동일 생산단계 데이터를 우선 비교한다.

### 8.4 출하 기준

- 금번 출하수량과 업체 누적 출하수량을 OQC 필수정보로 관리한다.
- 업체 누적과 프로그램 누적을 대조한다.
- 품질 이상률과 출하 Exposure를 임의로 혼합하지 않는다.
- 모델 출하수량과 부품 출하수량을 분리한다.

### 8.5 업체 기준

- 공정능력과 품질 안정성뿐 아니라 OQC 데이터 신뢰성을 평가한다.
- 허위 의심과 확인을 분리한다.
- 개선 성과는 단순 가점보다 향후 공급 안정성 판단에 우선 활용한다.
- 초기 생산과 정상 양산 데이터를 분리한다.

### 8.6 부하 기준

- Excel은 최초 1회만 해석한다.
- 이후 통계와 Dashboard는 표준 DB를 사용한다.
- 증분 계산과 Cache를 기본으로 한다.
- 신규 Spec·기간비교·과거 전체 재평가는 On-demand다.

---

## 10. 현재 명확히 제외된 Scope

- 사진 AI 분석
- 측정기 ID / Serial / 검교정 관리
- 업체 Action 응답속도 평가
- 업체 메일 자동 발송
- 자동 출하 Hold
- 자동 Spec 변경
- 자동 공급비율 결정
- 고객 Claim 연계 시장 무이슈 실적

이 Scope를 확장하려면 사용자 확인이 필요하다.

---

## 11. WORK MD 업데이트 규칙

다음 상황에서는 관련 WORK MD를 갱신한다.

- 기능범위 추가·삭제
- 데이터 Schema 또는 상태 변경
- Master·평가기준 변경
- 구현 Gate 통과
- Test 결과 확보
- 부하구조 변경
- 신규 주요 Risk 발생
- 사용자가 기존 합의를 수정
- 다음 채팅에서 Resume가 어려울 정도로 Context가 누적

변경 시 최신값을 본문 기준으로 반영하고, 중요한 이전값만 변경이력에 남긴다.

---

## 12. 전체 프로젝트 상태

**상태:** 개념설계 및 요구사항 합의 완료 / 구현 전 구조화 완료  
**완료:** 기능범위, AI·Code 역할, 부하 대원칙, 주요 화면, 데이터 관리·평가방향 합의  
**미완료:** 실제 대표 OQC 확보, 표준 DB 물리 Schema, Mapping UI, 구현환경 확정, Golden Test 데이터 구축

---

## 13. 현재 Resume Point

다음 작업은 `11_WORK_OQC_IMPLEMENTATION_ROADMAP.md` 기준으로 시작한다.

### 즉시 다음 Gate

1. 대표 OQC Excel 1건 선정
2. `04_WORK_OQC_INGESTION_MAPPING.md` 기준 Mapping Preview 정의
3. `03_WORK_OQC_SYSTEM_ARCHITECTURE.md`의 표준 DB 논리모델을 물리 Table로 구체화
4. 대표 OQC의 모델·부품·업체·LOT·Spec·측정값·출하수량을 손실 없이 적재
5. 사람이 원본과 대조하여 Mapping 정확성 승인

이 Gate가 닫히기 전에는 업체평가·공급안정성·신규 Spec 기능을 우선 구현하지 않는다.
