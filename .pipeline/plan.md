# DocForge Phase 1 상용화 실행 계획

## 요약
기존 DocForge PDF 파서의 상용화 품질 고도화. P0 항목 8개를 구현한다.

## 스코프 (P0 Only)
1. 이미지 품질 진단 + 조건부 전처리 (design-preprocessing.md 설계 기반)
2. 전처리 품질 게이트 (A/B 비교)
3. 줄바꿈 병합 개선 (95%+)
4. 테이블 추출 개선: 병합셀(colspan/rowspan) 지원
5. 테이블 추출 개선: 목차 리더dots 필터링
6. 멀티컬럼 레이아웃 감지 + 읽기 순서 보장
7. OCR 백엔드 플러거블 아키텍처
8. 파싱 신뢰도 점수 시스템

## 아키텍처 원칙
- 기존 레이어 구조 유지: domain/ -> processing/ -> adapters/ -> usecases/
- frozen dataclass, 불변성
- Protocol 기반 포트/어댑터
- 기존 127개 테스트 깨뜨리지 않음

## 태스크 분할 및 의존성

### Task 1: 도메인 모델 확장 (의존성: 없음)
- domain/value_objects.py에 RawImage, ImageQualityPolicy, ImageQualityReport, PreprocessingDecision, QualityGateResult 추가
- domain/ports.py에 ImageDiagnostics, ImagePreprocessor 프로토콜 추가
- domain/enums.py에 SelectionReason enum 추가
- domain/models.py에 PageConfidence 추가 (신뢰도 점수)
- 테스트: 도메인 모델 생성/불변성 검증

### Task 2: 이미지 품질 진단 모듈 (의존성: Task 1)
- processing/image_diagnostics.py 신규 생성
- 5개 진단 함수: _estimate_dpi, _detect_skew, _measure_contrast, _measure_noise, _measure_bg_uniformity
- numpy 배열만 입력받는 순수 함수
- 테스트: 합성 이미지로 각 진단 함수 개별 검증

### Task 3: 이미지 전처리 어댑터 + 품질 게이트 (의존성: Task 1, 2)
- adapters/opencv_preprocessor.py 신규 생성 (ImagePreprocessor 포트 구현)
- adapters/image_converter.py 신규 생성 (PIL <-> RawImage 변환)
- processing/quality_gate.py 신규 생성 (A/B 비교 로직)
- processing/preprocessing_router.py 신규 생성 (진단->판단->전처리->게이트 오케스트레이션)
- 테스트: 전처리 각 기법, 품질 게이트 판정 로직, 라우터 통합

### Task 4: 줄바꿈 병합 개선 (의존성: 없음)
- processing/line_merger.py 개선
- 보험약관 패턴: 짧은 항목 줄바꿈, 괄호 내 줄바꿈, 금액/날짜 뒤 줄바꿈
- 법률 문서 패턴: 조항 번호 앞뒤 줄바꿈, "다만," "단," 조건부 줄바꿈
- _should_split 로직 정교화: 문장 중간 줄바꿈 오분할 방지
- _join_texts 개선: 영한 혼용 시 스페이스 규칙
- 테스트: 보험약관/법률 문서 패턴별 줄바꿈 병합 정확도 검증

### Task 5: 테이블 추출 개선 (의존성: 없음)
- adapters/pdfplumber_tables.py 개선: 병합셀 감지 (colspan/rowspan)
- processing/table_merger.py 개선: 병합셀이 있는 테이블 cross-page 병합
- processing/noise_detector.py 개선: 리더dots 행을 테이블에서 필터링
- 테스트: 병합셀 테이블, 리더dots 필터링 검증

### Task 6: 멀티컬럼 레이아웃 감지 (의존성: 없음)
- processing/column_detector.py 신규 생성
- 텍스트 블록의 x좌표 분포 분석 -> 컬럼 수 감지
- 컬럼별 블록 정렬 (좌->우, 상->하)
- usecases/parse_pdf.py에서 멀티컬럼 감지 후 블록 재정렬 호출
- 테스트: 2단/3단 컬럼 시뮬레이션 검증

### Task 7: OCR 플러거블 아키텍처 + Apple Vision 스텁 (의존성: Task 1)
- domain/ports.py의 OCREngine 프로토콜 정비 (engine_name 추가)
- infrastructure/config.py에 ocr_backend 설정 추가
- usecases/engine.py에 OCR 팩토리 패턴 구현 (create_ocr_engine)
- adapters/apple_vision_engine.py 스텁 생성 (macOS에서만 동작)
- parse_pdf.py의 _create_ocr_engine을 engine.py의 팩토리로 교체
- 테스트: 팩토리 패턴, 폴백 로직 검증

### Task 8: 파싱 신뢰도 점수 시스템 (의존성: Task 1, 2, 3)
- processing/confidence_scorer.py 신규 생성
- 페이지별 신뢰도 산출 (OCR confidence, 텍스트 밀도, 구조 인식 비율, 전처리 결과)
- domain/models.py의 PageContent에 confidence 필드 추가
- 테스트: 다양한 시나리오별 신뢰도 점수 검증

### Task 9: parse_pdf.py 통합 (의존성: Task 1~8 전체)
- usecases/parse_pdf.py에 전처리 파이프라인 통합
- ScannedPageProcessor 클래스 도입
- 디지털/OCR 후처리 파이프라인 분기 (design-preprocessing.md 참조)
- 멀티컬럼 감지 호출 통합
- 신뢰도 점수 계산 통합
- 기존 테스트 전체 통과 확인

## 실행 순서 (의존성 그래프)

```
Phase A (병렬): Task 1, Task 4, Task 5, Task 6
Phase B (Task 1 완료 후, 병렬): Task 2, Task 7
Phase C (Task 2 완료 후): Task 3
Phase D (Task 1,2,3 완료 후): Task 8
Phase E (전체 완료 후): Task 9
```

## 리스크
1. OpenCV 미설치 시 전처리 불가 -> opencv-python-headless 의존성 추가 필요
2. numpy 버전 호환성 -> 기존 환경에 이미 설치됨 (EasyOCR 의존)
3. 기존 테스트 깨짐 -> 모든 변경은 하위호환 유지, 필드 추가 시 기본값 사용
4. 성능 저하 -> 전처리는 조건부만 적용, 양호 페이지는 건너뜀

## 완료 기준
- 모든 P0 항목 구현 완료
- 기존 테스트 127개 + 신규 테스트 전부 통과
- 각 기능의 유닛 테스트 커버리지 80%+
