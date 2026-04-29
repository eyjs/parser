# 코드 리뷰 피드백 반영 — Critical & Important 15건

## 생성일시
2026-04-29 00:00

## 목적
- 왜 만드는가: Phase 1 구현 완료 후 코드 리뷰에서 식별된 아키텍처 위반 4건 및 품질 개선 11건을 반영하여 코드베이스를 설계 의도에 부합하게 교정
- 누가 사용하는가: 내부 개발팀 (Implementor 에이전트 및 실제 개발자)
- 기대 효과: 계층 경계 위반 제거, 타입 안전성 강화, 버그성 동작 수정, 테스트 커버리지 보완

## 스코프

### 포함 (이번에 수정하는 것)
- [ ] C1: `image_diagnostics.py` — `_detect_skew()` OpenCV 의존 제거
- [ ] C2: `image_diagnostics.py` — `_measure_noise()` OpenCV 의존 제거
- [ ] C3: `ocr_factory.py` — `_create_auto()` 플랫폼 우선순위 오류 수정
- [ ] C4: `parse_pdf.py` — 후처리 순서를 page_type 기반으로 분기
- [ ] I1: `ports.py` — `Any` 타입 13회 사용 제거
- [ ] I2: `preprocessing_router.py` — `Any` 타입 파라미터를 Protocol 타입으로 교체
- [ ] I3: `quality_gate.py` — `Any` 타입 파라미터를 Protocol 타입으로 교체
- [ ] I4: `value_objects.py:130` — `quality_report` 필수 파라미터로 변경
- [ ] I5: `value_objects.py:155` — `winning_blocks` 타입을 `tuple[TextBlock, ...]`로 변경
- [ ] I6: `parse_pdf.py` — 데드 코드 `_create_ocr_engine()` 삭제
- [ ] I7: `parse_pdf.py` — SCANNED 경로 PIL Image / RawImage 타입 혼용 정리
- [ ] I8: `ocr_factory.py` — 모든 백엔드 실패 시 `RuntimeError` raise로 변경
- [ ] I9: `apple_vision_engine.py` — OCR 실패 묵음 처리를 `logger.warning`으로 격상
- [ ] I10: `config.py` — `dpi` 기본값을 200에서 300으로 변경
- [ ] I11: `test_preprocessing.py` — 누락된 테스트 케이스 2건 추가

### 제외 (이번에 만들지 않는 것)
- 기능 신규 개발 (Phase 2 항목 포함)
- 기존 API 시그니처 변경 (외부 인터페이스 호환성 유지)
- 리팩토링 범위를 벗어난 추가 개선

## 기술스택
- Python 3.11+, Flask 3.x
- PyMuPDF, pdfplumber, EasyOCR, OpenCV, numpy, Pillow
- macOS Apple Vision (pyobjc)
- 아키텍처: `domain/ports.py` (Protocol), `processing/` (순수 로직), `adapters/` (외부 의존), `usecases/`

## 핵심 기능 (수정 항목 상세)

### P0 (Critical — 반드시 수정)

#### C1: `_detect_skew()` — processing 레이어 OpenCV 금지 위반
- **파일**: `processing/image_diagnostics.py:109`
- **문제**: `cv2.Canny`, `cv2.HoughLinesP` 직접 호출. processing 레이어는 순수 Python/numpy만 허용
- **수정 방법**:
  - Option A (권장): numpy로 Sobel 엣지 감지 + Hough 변환 직접 구현
  - Option B: `ImagePreprocessor` adapter에 `detect_skew_angle(image: RawImage) -> float` 메서드를 추가하고 processing 레이어에서 Protocol을 통해 호출
- **완료 기준**: `import cv2`가 `image_diagnostics.py`에 존재하지 않음. 기존 skew 감지 동작(반환값 타입, 범위) 동일 유지. 관련 테스트 통과

#### C2: `_measure_noise()` — processing 레이어 OpenCV 금지 위반
- **파일**: `processing/image_diagnostics.py:174`
- **문제**: `cv2.Laplacian` 직접 호출
- **수정 방법**: numpy 3x3 Laplacian kernel(`[[0,1,0],[1,-4,1],[0,1,0]]`)로 `scipy.signal.convolve2d` 또는 순수 numpy 슬라이싱 구현으로 대체
- **완료 기준**: `cv2.Laplacian` 호출 없음. 노이즈 측정값 정합성 유지 (기존 테스트 통과)

#### C3: `_create_auto()` — OCR 백엔드 우선순위 오류
- **파일**: `adapters/ocr_factory.py:63-64`
- **문제**: macOS에서도 EasyOCR를 먼저 시도함. 설계 의도: macOS → Apple Vision 우선, Windows/Linux → EasyOCR 우선
- **수정 방법**:
  ```python
  import platform
  if platform.system() == "Darwin":
      # Apple Vision → EasyOCR 순서
  else:
      # EasyOCR → (다른 백엔드) 순서
  ```
- **완료 기준**: macOS에서 Apple Vision이 우선 시도됨. Windows에서 EasyOCR가 우선 시도됨. 단위 테스트로 분기 검증

#### C4: 후처리 순서 미분기
- **파일**: `usecases/parse_pdf.py:184-216`
- **문제**: page_type 무관하게 동일 후처리 순서(`classify_block → merge_lines`) 적용. 설계 의도:
  - (A) 디지털 텍스트: `text_structurer → line_merger`
  - (B) OCR 결과: `line_merger → text_structurer`
- **수정 방법**: `page_type` 값으로 분기 블록 추가
  ```python
  if page_type == PageType.DIGITAL:
      result = text_structurer.structure(raw)
      result = line_merger.merge(result)
  else:  # SCANNED / OCR
      result = line_merger.merge(raw)
      result = text_structurer.structure(result)
  ```
- **완료 기준**: DIGITAL/SCANNED 페이지 각각 올바른 순서로 처리됨. 기존 통합 테스트 200개 전원 통과

### P1 (Important — 개선 필요)

#### I1: `ports.py` `Any` 타입 제거
- **파일**: `domain/ports.py` 전체
- **문제**: `Any` 타입 13회 사용
- **수정 방법**:
  - `PDFReader.doc: Any` → `TypeVar('PDFDoc')` 또는 opaque 타입 alias `PDFDoc = object` + 주석
  - `OCREngine.recognize(image: Any)` → `RawImage` 타입 (이미 `value_objects`에 정의된 경우 import)
  - `TableExtractor` 파라미터 → 구체 타입 또는 `RawImage`로 교체
- **완료 기준**: `from typing import Any` import 제거 또는 사용 횟수 0. `mypy --strict` 또는 `pyright` 통과

#### I2: `preprocessing_router.py` Protocol 타입 적용
- **파일**: `processing/preprocessing_router.py:32-33`
- **문제**: `ocr_engine: Any`, `preprocessor: Any`
- **수정 방법**: `from domain.ports import OCREngine, ImagePreprocessor` import 후 타입 교체
- **완료 기준**: 해당 파라미터에 `Any` 없음. 타입 체커 통과

#### I3: `quality_gate.py` Protocol 타입 적용
- **파일**: `processing/quality_gate.py:25`
- **문제**: `ocr_engine: Any`
- **수정 방법**: `OCREngine` Protocol로 교체
- **완료 기준**: 해당 파라미터에 `Any` 없음. 타입 체커 통과

#### I4: `value_objects.py` `quality_report` 필수 파라미터화
- **파일**: `domain/value_objects.py:130`
- **문제**: `quality_report: ImageQualityReport | None = None` — 설계상 필수값인데 Optional 기본값 보유
- **수정 방법**: `= None` 기본값 제거. 호출부 전체에서 반드시 값을 전달하도록 수정
- **주의**: 호출부 변경 범위 파악 후 수정. 테스트 호출부도 포함
- **완료 기준**: `quality_report` 파라미터에 기본값 없음. 전체 테스트 통과

#### I5: `value_objects.py` `winning_blocks` forward reference 적용
- **파일**: `domain/value_objects.py:155`
- **문제**: `winning_blocks: tuple[object, ...] = ()` — 타입 정보 손실
- **수정 방법**: `TYPE_CHECKING` guard + forward reference 사용
  ```python
  from __future__ import annotations
  from typing import TYPE_CHECKING
  if TYPE_CHECKING:
      from domain.value_objects import TextBlock
  winning_blocks: tuple[TextBlock, ...] = ()
  ```
- **완료 기준**: `tuple[TextBlock, ...]` 타입 명시. 순환 import 없음

#### I6: `parse_pdf.py` 데드 코드 삭제
- **파일**: `usecases/parse_pdf.py:413-432`
- **문제**: `_create_ocr_engine()` 함수가 `ocr_factory` 도입 이후 미사용
- **수정 방법**: 함수 전체 삭제. grep으로 호출부 없음 확인 후 삭제
- **완료 기준**: `_create_ocr_engine` 식별자가 코드베이스에 존재하지 않음

#### I7: SCANNED 경로 PIL Image / RawImage 타입 혼용 정리
- **파일**: `usecases/parse_pdf.py:187-198`
- **문제**: `ocr_engine.recognize()`에 PIL Image를 전달하는 경로와 RawImage를 전달하는 경로가 혼재
- **수정 방법**: SCANNED 경로에서 PIL Image를 `RawImage`로 변환하는 단일 지점을 확보한 후 `recognize()`에 전달. `RawImage`가 `numpy.ndarray` 기반이면 `np.array(pil_image)` 변환 일원화
- **완료 기준**: `ocr_engine.recognize()` 호출부에서 인자 타입이 항상 `RawImage`. PIL Image 직접 전달 경로 없음

#### I8: `ocr_factory.py` 모든 백엔드 실패 시 예외 raise
- **파일**: `adapters/ocr_factory.py:81-84`
- **문제**: 모든 백엔드 실패 시 unavailable 상태의 EasyOCR 인스턴스 반환 (묵음 실패)
- **수정 방법**: `raise RuntimeError("No OCR backend available: all backends failed to initialize")`
- **완료 기준**: 모든 백엔드 실패 시 `RuntimeError` 발생. 해당 시나리오 단위 테스트 추가

#### I9: `apple_vision_engine.py` OCR 실패 로깅 격상
- **파일**: `adapters/apple_vision_engine.py:69-70`
- **문제**: OCR 실패를 빈 리스트 `[]`로 조용히 반환. 실패 원인 미기록
- **수정 방법**:
  ```python
  except Exception as e:
      logger.warning("Apple Vision OCR failed: %s", e, exc_info=True)
      return []
  ```
- **완료 기준**: OCR 실패 시 `WARNING` 레벨 이상으로 로깅. 실패 예외 정보 포함

#### I10: `config.py` DPI 기본값 수정
- **파일**: `config.py:48`
- **문제**: `dpi: int = 200` — 설계 문서 "이미지 렌더링 300 DPI"와 불일치
- **수정 방법**: `dpi: int = 300`으로 변경
- **주의**: DPI 변경이 기존 테스트 픽셀 값 기반 assertion에 영향 줄 수 있음. 영향 범위 확인 후 테스트 수치 업데이트
- **완료 기준**: `dpi` 기본값 300. 관련 테스트 일관성 유지

#### I11: `test_preprocessing.py` 누락 테스트 추가
- **파일**: `tests/test_preprocessing.py`
- **문제**: 두 가지 엣지 케이스 테스트 누락
  - Case 3: `PREP_CHAR_GAIN` — 전처리 후 문자 수가 증가한 경우 라우팅 동작 검증
  - Case 4: `orig_chars == 0 and prep_chars == 0` — 원본/전처리 양쪽 모두 빈 결과인 경우 처리 검증
- **수정 방법**: Given-When-Then 패턴으로 두 케이스 테스트 함수 추가
  ```python
  def test_prep_char_gain_routes_to_preprocessed():
      # Given: 전처리 후 문자 수 증가
      # When: 라우터 호출
      # Then: 전처리 결과 선택

  def test_both_empty_results_handled_gracefully():
      # Given: orig_chars==0, prep_chars==0
      # When: 라우터 호출
      # Then: 예외 없이 빈 결과 반환 또는 fallback 동작
  ```
- **완료 기준**: 두 테스트 케이스 추가. 전체 테스트 스위트 통과

## 제약사항
- **기존 테스트 200개 전원 통과 필수**: 어떤 수정도 기존 테스트를 깨뜨려서는 안 됨. 테스트 수치 변경이 필요한 경우(I10 DPI 변경 등) 수치만 업데이트하고 테스트 로직은 유지
- **외부 API 시그니처 유지**: Flask 엔드포인트, usecase 공개 메서드 시그니처 변경 금지
- **단계적 수정**: Critical 4건 → Important 11건 순서로 진행. 각 수정 후 테스트 실행으로 회귀 확인
- **순환 import 금지**: I5 forward reference 적용 시 특히 주의
- **플랫폼 테스트**: C3 플랫폼 분기는 `unittest.mock.patch("platform.system")` 활용하여 macOS/Windows 양쪽 검증

## 성공 기준
1. `pytest` 실행 결과: 기존 200개 + 신규 추가 테스트 전원 GREEN
2. `image_diagnostics.py`에 `import cv2` 없음
3. `ports.py`에서 `Any` 타입 사용 0건
4. `ocr_factory.py`에서 `_create_auto()` 플랫폼 분기 존재
5. `parse_pdf.py`에서 page_type 기반 후처리 분기 존재
6. `parse_pdf.py`에서 `_create_ocr_engine` 함수 없음
7. 모든 백엔드 실패 시 `RuntimeError` raise 확인 테스트 통과
8. `config.py` `dpi` 기본값 300 확인

## 특이사항
- 파이프라인 타입: `bugfix` (기능 추가 없음, 기존 코드 교정)
- 이 requirement는 `requirement.md` (Phase 1 상용화)와 독립적으로 관리됨
- C1/C2 수정 시 numpy 구현의 성능이 OpenCV 대비 저하될 수 있음. 허용 범위: 처리 시간 2배 이내 (이미지 진단은 크리티컬 패스 외)
- I4 `quality_report` 필수화는 호출부 변경 범위가 클 수 있음. 수정 전 `grep -rn "quality_report"` 로 호출부 전수 파악 필요
- I10 DPI 300 변경은 스캔 이미지 처리 속도에 영향을 줄 수 있음 (300 DPI = 메모리/처리 시간 약 2.25배). 성능 테스트 있으면 함께 업데이트
