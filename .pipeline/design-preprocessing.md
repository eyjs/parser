# OCR 전처리/후처리 파이프라인 설계

## 설계 원칙

1. **측정 후 판단** — 전처리를 무조건 적용하지 않는다. 이미지 품질을 측정하고, 문제가 있는 항목에만 선택적으로 적용한다.
2. **원본 우선** — 전처리 결과가 원본보다 낫다는 증거가 없으면 원본을 채택한다.
3. **불변성** — 모든 중간 결과는 frozen dataclass. 원본 이미지를 변경하지 않고 새 이미지를 생성한다.
4. **기존 아키텍처 준수** — domain/ports.py에 프로토콜 정의, processing/에 로직, adapters/에 외부 라이브러리 의존.
5. **OCR 최소 호출** — 품질 게이트에서 수행한 OCR 결과를 재사용한다. 동일 이미지에 대한 중복 OCR 호출을 금지한다.
6. **안전한 실패** — 전처리 실패 시 원본으로 폴백한다. 전처리 오류가 전체 파싱을 중단시키지 않는다.

---

## 전체 흐름

```
PDF 페이지
 │
 ▼
PageClassifier (기존)
 ├── DIGITAL → PyMuPDF 텍스트 추출 → 후처리 파이프라인 (A)
 │                                    (전처리 완전 건너뜀)
 │
 ├── MIXED → 이미지 렌더링 (300 DPI)
 │            + PyMuPDF 텍스트 추출 (텍스트 레이어 병합용)
 │            └─→ 전처리 파이프라인 (아래와 동일)
 │
 └── SCANNED → 이미지 렌더링 (300 DPI)
                │
                ▼
           ┌─────────────┐
           │ diagnose_   │  이미지 품질 진단
           │ image()     │  (5개 항목 독립 측정)
           └──────┬──────┘
                  │
           ┌──────▼──────┐
           │ 모두 양호?  │
           └──────┬──────┘
             YES/ \NO
             │     │
             │     ▼
             │  ┌──────────────┐
             │  │ Selective    │  문제 항목만
             │  │ Preprocessor │  선택적 전처리
             │  └──────┬───────┘
             │         │ (실패 시 원본 폴백)
             │         ▼
             │  ┌──────────────┐
             │  │ Quality Gate │  원본 vs 전처리
             │  │ (A/B 비교)   │  OCR 결과 비교
             │  └──────┬───────┘
             │    ORIG/ \PREP
             │    │      │
             ▼    ▼      ▼
           ┌─────────────────┐
           │  winning_blocks │  게이트에서 이미 확보한
           │  (OCR 재호출 X) │  OCR 결과를 그대로 사용
           └────────┬────────┘
                    │
                    ▼
             후처리 파이프라인 (B)
```

### 후처리 파이프라인 분기

```
(A) 디지털 텍스트 후처리:
    text_structurer → line_merger → ocr_corrector → markdown_assembler
    (구조가 이미 정돈된 텍스트 → 구조 인식 먼저)

(B) OCR 결과 후처리:
    line_merger → text_structurer → ocr_corrector → markdown_assembler
    (물리적 줄 단위 → 논리 문장 복원 먼저, 그 후 구조 인식)
```

---

## 도메인 모델

### value_objects.py에 추가

```python
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


# --- 이미지 래퍼 (Any 타입 제거) ---

@dataclass(frozen=True)
class RawImage:
    """도메인에서 사용하는 이미지 래퍼. 외부 라이브러리 타입 직접 노출을 방지."""
    data: NDArray[np.uint8]
    width: int
    height: int
    channels: int  # 1=grayscale, 3=RGB


# --- 품질 정책 (임계값 외부화) ---

@dataclass(frozen=True)
class ImageQualityPolicy:
    """품질 판단 임계값. ParserConfig에서 주입."""
    min_dpi: float = 200.0
    max_skew_degrees: float = 0.5
    min_contrast_ratio: float = 0.3
    max_noise_score: float = 0.4
    max_bg_nonuniformity: float = 0.5
    confidence_margin: float = 0.02
    char_loss_threshold: float = 0.8
    char_gain_threshold: float = 1.2


# --- 품질 진단 결과 ---

@dataclass(frozen=True)
class ImageQualityReport:
    """이미지 품질 진단 결과. 각 항목은 독립적으로 측정."""

    dpi_estimated: float          # 추정 DPI (텍스트 높이 기반, 텍스트 없으면 -1.0)
    skew_angle: float             # 기울기 각도 (도 단위)
    contrast_ratio: float         # 대비 비율 (0.0~1.0)
    noise_score: float            # 노이즈 수준 (0.0=깨끗, 1.0=심함)
    background_uniformity: float  # 배경 균일성 (0.0=균일, 1.0=불균일)

    def needs_upscale(self, policy: ImageQualityPolicy) -> bool:
        return self.dpi_estimated != -1.0 and self.dpi_estimated < policy.min_dpi

    def needs_deskew(self, policy: ImageQualityPolicy) -> bool:
        return abs(self.skew_angle) > policy.max_skew_degrees

    def needs_contrast(self, policy: ImageQualityPolicy) -> bool:
        return self.contrast_ratio < policy.min_contrast_ratio

    def needs_denoise(self, policy: ImageQualityPolicy) -> bool:
        return self.noise_score > policy.max_noise_score

    def needs_binarize(self, policy: ImageQualityPolicy) -> bool:
        return self.background_uniformity > policy.max_bg_nonuniformity

    def is_clean(self, policy: ImageQualityPolicy) -> bool:
        """모든 항목이 양호하면 전처리 불필요."""
        return not any([
            self.needs_upscale(policy),
            self.needs_deskew(policy),
            self.needs_contrast(policy),
            self.needs_denoise(policy),
            self.needs_binarize(policy),
        ])


# --- 전처리 판단 ---

@dataclass(frozen=True)
class PreprocessingDecision:
    """전처리 판단 결과. 어떤 전처리를 적용할지 결정."""

    apply_upscale: bool = False
    apply_deskew: bool = False
    apply_contrast: bool = False
    apply_denoise: bool = False
    apply_binarize: bool = False
    quality_report: ImageQualityReport = ...  # 필수 (None 허용 안 함)
    skew_angle: float = 0.0                   # deskew에 필요한 각도 (report에서 복사)

    @property
    def skip_all(self) -> bool:
        return not any([
            self.apply_upscale,
            self.apply_deskew,
            self.apply_contrast,
            self.apply_denoise,
            self.apply_binarize,
        ])


# --- 품질 게이트 ---

class SelectionReason(Enum):
    """품질 게이트 판정 사유."""
    ORIGINAL_DEFAULT = auto()        # 기본: 원본 유지
    PREP_CHAR_LOSS = auto()          # 전처리 기각: 문자 소실
    PREP_CONFIDENCE_UP = auto()      # 전처리 채택: 신뢰도 향상
    PREP_CHAR_GAIN = auto()          # 전처리 채택: 인식량 증가
    PREP_RESCUED_EMPTY = auto()      # 전처리 채택: 원본 인식 0 → 전처리가 텍스트 복원
    PREPROCESSING_FAILED = auto()    # 전처리 실패: 원본 폴백


@dataclass(frozen=True)
class QualityGateResult:
    """품질 게이트 A/B 비교 결과. winning_blocks를 포함하여 OCR 재호출 방지."""

    use_preprocessed: bool
    original_confidence: float
    preprocessed_confidence: float
    original_char_count: int
    preprocessed_char_count: int
    reason: SelectionReason
    reason_detail: str                # 사람이 읽을 수 있는 판정 사유
    winning_blocks: list[TextBlock]   # 채택된 쪽의 OCR 결과 (OCR 재호출 불필요)
```

### ports.py에 추가

```python
class ImageDiagnostics(Protocol):
    """Port for image quality diagnosis. 측정만, 판단 안 함."""

    def diagnose(self, image: RawImage) -> ImageQualityReport:
        ...


class ImagePreprocessor(Protocol):
    """Port for image preprocessing operations. 전처리만."""

    def preprocess(self, image: RawImage, decision: PreprocessingDecision) -> RawImage:
        """판단 결과에 따라 선택적 전처리를 적용한다. 새 이미지를 반환."""
        ...
```

> **변경 사유 (C3 해결):** `diagnose()`와 `preprocess()`를 별도 프로토콜로 분리.
> `diagnose()`는 순수 함수로 processing 레이어에 위치하며, adapters에서 구현할 필요 없음.
> `ImagePreprocessor` 포트는 OpenCV 등 외부 라이브러리 의존 전처리만 담당.

---

## 모듈 구조

```
docforge/
├── domain/
│   ├── value_objects.py        # + RawImage, ImageQualityPolicy, ImageQualityReport,
│   │                           #   PreprocessingDecision, SelectionReason, QualityGateResult
│   └── ports.py                # + ImageDiagnostics, ImagePreprocessor (분리된 2개 프로토콜)
│
├── processing/
│   ├── image_diagnostics.py    # [신규] 품질 진단 순수 함수 (PIL/numpy만 사용)
│   │                           #   diagnose_image(gray: NDArray) → ImageQualityReport
│   │                           #   (PIL.Image → NDArray 변환은 adapter 책임)
│   ├── preprocessing_router.py # [신규] 진단 → 판단 → 전처리 → 품질 게이트 오케스트레이션
│   ├── quality_gate.py         # [신규] 원본 vs 전처리 A/B 비교 (winning_blocks 반환)
│   └── (기존 모듈들 유지)
│
├── adapters/
│   ├── opencv_preprocessor.py  # [신규] OpenCV 기반 전처리 구현체 (ImagePreprocessor 포트)
│   ├── image_converter.py      # [신규] PIL.Image ↔ RawImage 변환 유틸
│   ├── easyocr_engine.py       # (기존 유지)
│   └── apple_vision_engine.py  # (Phase 1 추가 예정)
│
└── usecases/
    └── parse_pdf.py            # ScannedPageProcessor 클래스 추출, preprocessing_router 호출
```

---

## 핵심 모듈 설계

### 1. processing/image_diagnostics.py — 측정만 한다

```python
"""이미지 품질 진단. 측정만 하고 판단은 하지 않는다.
numpy 배열만 입력받아 PIL 의존 없이 동작."""

import numpy as np
from numpy.typing import NDArray

def diagnose_image(gray: NDArray[np.uint8]) -> ImageQualityReport:
    """5개 품질 지표를 독립적으로 측정.
    gray: 2D grayscale numpy array (adapter에서 변환 후 전달).
    """
    return ImageQualityReport(
        dpi_estimated=_estimate_dpi(gray),
        skew_angle=_detect_skew(gray),
        contrast_ratio=_measure_contrast(gray),
        noise_score=_measure_noise(gray),
        background_uniformity=_measure_bg_uniformity(gray),
    )
```

> **변경 사유 (M3 해결):** PIL.Image 대신 numpy NDArray를 입력받아 processing 레이어 순수성 유지.
> PIL → NDArray 변환은 adapters/image_converter.py에서 수행.

각 측정 함수:

| 함수 | 알고리즘 | 출력 | 엣지 케이스 |
|------|----------|------|-------------|
| `_estimate_dpi` | 텍스트 라인 높이 추정 → DPI 역산 | float (DPI), 텍스트 없으면 **-1.0** | 도면/사진 등 텍스트 없는 이미지 → -1.0 반환, upscale 판단에서 제외 |
| `_detect_skew` | Hough Line Transform → 주요 직선 각도 중앙값 | float (도) | 직선 미검출 시 0.0 |
| `_measure_contrast` | 히스토그램 5th/95th percentile 차이 / 255 | float (0~1) | |
| `_measure_noise` | 라플라시안 분산 + salt-pepper 비율 | float (0~1) | |
| `_measure_bg_uniformity` | NxN 그리드 분할 → 블록 평균 밝기의 표준편차 | float (0~1) | |

### 2. processing/preprocessing_router.py — 판단 + 오케스트레이션

```python
"""전처리 라우터. 진단 → 판단 → 전처리 → 품질 게이트.
OCR 결과는 quality_gate에서 확보하며, 라우터는 OCR을 직접 호출하지 않는다."""

import logging

logger = logging.getLogger(__name__)


def process_scanned_page(
    image: RawImage,
    ocr_engine: OCREngine,
    preprocessor: ImagePreprocessor,
    policy: ImageQualityPolicy,
) -> tuple[list[TextBlock], PreprocessingDecision, QualityGateResult | None]:

    # Step 1: 진단 (numpy 배열 직접 사용)
    gray = to_grayscale(image.data)
    report = diagnose_image(gray)

    # Step 2: 판단 (policy 기반)
    decision = PreprocessingDecision(
        apply_upscale=report.needs_upscale(policy),
        apply_deskew=report.needs_deskew(policy),
        apply_contrast=report.needs_contrast(policy),
        apply_denoise=report.needs_denoise(policy),
        apply_binarize=report.needs_binarize(policy),
        quality_report=report,
        skew_angle=report.skew_angle,
    )

    # Step 3: 양호하면 원본 직행 (OCR 1회)
    if decision.skip_all:
        blocks = ocr_engine.recognize(image)
        return blocks, decision, None

    # Step 4: 선택적 전처리 (실패 시 원본 폴백)
    try:
        preprocessed = preprocessor.preprocess(image, decision)
    except Exception:
        logger.warning("전처리 실패, 원본으로 폴백", exc_info=True)
        blocks = ocr_engine.recognize(image)
        gate = QualityGateResult(
            use_preprocessed=False,
            original_confidence=0.0,
            preprocessed_confidence=0.0,
            original_char_count=0,
            preprocessed_char_count=0,
            reason=SelectionReason.PREPROCESSING_FAILED,
            reason_detail="전처리 중 예외 발생, 원본 폴백",
            winning_blocks=blocks,
        )
        return blocks, decision, gate

    # Step 5: 품질 게이트 (A/B 비교, OCR 2회 실행 후 winning_blocks 반환)
    gate_result = quality_gate(image, preprocessed, ocr_engine, policy)

    # Step 6: 게이트 결과의 winning_blocks를 그대로 사용 (OCR 재호출 없음)
    return gate_result.winning_blocks, decision, gate_result
```

> **변경 사유 (C1 해결):** `quality_gate()`가 `winning_blocks`를 반환하므로 라우터에서 OCR 재호출 제거.
> 기존: gate(OCR 2회) → 판정 → 채택된 이미지로 OCR 1회 더 = **총 3회**
> 변경: gate(OCR 2회) → 판정 → winning_blocks 그대로 사용 = **총 2회**

> **변경 사유 (I6 해결):** `preprocessor.preprocess()` 실패 시 `try/except`로 원본 폴백.

### 3. processing/quality_gate.py — 원본 vs 전처리 비교

```python
"""품질 게이트. 원본과 전처리 결과를 비교하여 더 나은 쪽을 채택.
winning_blocks를 함께 반환하여 호출자의 OCR 재호출을 방지."""


def quality_gate(
    original: RawImage,
    preprocessed: RawImage,
    ocr_engine: OCREngine,
    policy: ImageQualityPolicy,
) -> QualityGateResult:

    orig_blocks = ocr_engine.recognize(original)
    prep_blocks = ocr_engine.recognize(preprocessed)

    orig_conf = _avg_confidence(orig_blocks)
    prep_conf = _avg_confidence(prep_blocks)
    orig_chars = _total_chars(orig_blocks)
    prep_chars = _total_chars(prep_blocks)

    # --- 판정 로직 ---

    # Case 0: 원본 인식 문자가 0 → 전처리가 텍스트를 복원했으면 무조건 채택
    if orig_chars == 0 and prep_chars > 0:
        return QualityGateResult(
            use_preprocessed=True,
            original_confidence=orig_conf,
            preprocessed_confidence=prep_conf,
            original_char_count=orig_chars,
            preprocessed_char_count=prep_chars,
            reason=SelectionReason.PREP_RESCUED_EMPTY,
            reason_detail=f"원본 인식 0자 → 전처리가 {prep_chars}자 복원",
            winning_blocks=prep_blocks,
        )

    # Case 1: 전처리로 텍스트가 소실되면 탈락
    if prep_chars < orig_chars * policy.char_loss_threshold:
        return QualityGateResult(
            use_preprocessed=False,
            original_confidence=orig_conf,
            preprocessed_confidence=prep_conf,
            original_char_count=orig_chars,
            preprocessed_char_count=prep_chars,
            reason=SelectionReason.PREP_CHAR_LOSS,
            reason_detail=f"전처리 기각: 문자 소실 ({prep_chars} < {orig_chars}*{policy.char_loss_threshold})",
            winning_blocks=orig_blocks,
        )

    # Case 2: 전처리가 유의미하게 나으면 채택
    if prep_conf > orig_conf + policy.confidence_margin:
        return QualityGateResult(
            use_preprocessed=True,
            original_confidence=orig_conf,
            preprocessed_confidence=prep_conf,
            original_char_count=orig_chars,
            preprocessed_char_count=prep_chars,
            reason=SelectionReason.PREP_CONFIDENCE_UP,
            reason_detail=f"전처리 채택: 신뢰도 향상 ({orig_conf:.3f} → {prep_conf:.3f})",
            winning_blocks=prep_blocks,
        )

    # Case 3: 신뢰도 비슷하지만 문자 수가 크게 늘었으면 채택
    if prep_chars > orig_chars * policy.char_gain_threshold and prep_conf >= orig_conf:
        return QualityGateResult(
            use_preprocessed=True,
            original_confidence=orig_conf,
            preprocessed_confidence=prep_conf,
            original_char_count=orig_chars,
            preprocessed_char_count=prep_chars,
            reason=SelectionReason.PREP_CHAR_GAIN,
            reason_detail=f"전처리 채택: 인식량 증가 ({orig_chars} → {prep_chars})",
            winning_blocks=prep_blocks,
        )

    # Default: 원본 유지
    return QualityGateResult(
        use_preprocessed=False,
        original_confidence=orig_conf,
        preprocessed_confidence=prep_conf,
        original_char_count=orig_chars,
        preprocessed_char_count=prep_chars,
        reason=SelectionReason.ORIGINAL_DEFAULT,
        reason_detail="원본 유지 (전처리가 유의미한 개선 없음)",
        winning_blocks=orig_blocks,
    )
```

> **변경 사유 (I3 해결):** `orig_chars == 0` 엣지 케이스를 Case 0으로 최우선 처리.
> 저품질 스캔에서 원본 인식 0자인데 전처리가 텍스트를 복원한 경우, 기존 설계는 기각했음.

### 4. adapters/opencv_preprocessor.py — 실제 전처리 구현

```python
"""OpenCV 기반 이미지 전처리. 각 기법을 독립적으로 적용.
ImagePreprocessor 포트만 구현 (diagnose는 processing 레이어 순수 함수)."""

import cv2
import numpy as np

class OpenCVPreprocessor:
    """ImagePreprocessor 포트 구현체."""

    def preprocess(self, image: RawImage, decision: PreprocessingDecision) -> RawImage:
        img = image.data.copy()

        if decision.apply_upscale:
            img = self._upscale(img, target_dpi=300)

        if decision.apply_deskew:
            img = self._deskew(img, decision.skew_angle)

        if decision.apply_contrast:
            img = self._enhance_contrast(img)  # CLAHE

        if decision.apply_denoise:
            img = self._denoise(img)  # cv2.medianBlur, kernel=3

        if decision.apply_binarize:
            img = self._adaptive_binarize(img)  # cv2.adaptiveThreshold (Gaussian)

        h, w = img.shape[:2]
        channels = 1 if img.ndim == 2 else img.shape[2]
        return RawImage(data=img, width=w, height=h, channels=channels)
```

> **변경 사유 (I4 해결):** `decision.quality_report.skew_angle` 대신 `decision.skew_angle`을 직접 사용.
> `PreprocessingDecision`에 `skew_angle` 필드를 추가하여 `quality_report` None 접근 위험 제거.

> **변경 사유 (Adapters 리뷰):** Sauvola → `cv2.adaptiveThreshold(ADAPTIVE_THRESH_GAUSSIAN_C)` 사용.
> OpenCV 내장이므로 scikit-image 추가 의존성 불필요.

---

## parse_pdf.py 통합 지점

```python
# ScannedPageProcessor로 추출하여 파라미터 수 제한 (M4 해결)

@dataclass
class ScannedPageProcessor:
    """스캔 페이지 전처리 + OCR 처리 담당."""
    ocr_engine: OCREngine
    preprocessor: ImagePreprocessor
    policy: ImageQualityPolicy

    def process(self, image: RawImage) -> tuple[list[TextBlock], PreprocessingDecision, QualityGateResult | None]:
        return process_scanned_page(
            image=image,
            ocr_engine=self.ocr_engine,
            preprocessor=self.preprocessor,
            policy=self.policy,
        )


# parse_pdf.py 내부
if page_type in (PageType.SCANNED, PageType.MIXED):
    page_image = converter.to_raw_image(
        reader.render_page_image(doc, page_idx, config.dpi)
    )
    blocks, prep_decision, gate_result = scanned_processor.process(page_image)

    # MIXED 페이지: 텍스트 레이어와 OCR 결과 병합
    if page_type == PageType.MIXED:
        digital_blocks = extract_text_layer(doc, page_idx)
        blocks = merge_digital_and_ocr(digital_blocks, blocks)

    if gate_result:
        logger.info(
            "Page %d: %s (%s)",
            page_idx, gate_result.reason.name, gate_result.reason_detail,
        )
```

> **변경 사유 (I5 해결):** MIXED 페이지를 SCANNED과 동일 경로로 처리 후, 텍스트 레이어와 병합.

---

## 후처리 파이프라인 (입력 소스별 분기)

```
(A) 디지털 텍스트 (PyMuPDF 추출):
    ┌──────────────┐
    │ TextBlock[]  │  이미 정돈된 텍스트
    └──────┬───────┘
           ▼
    1. text_structurer   ← 제/조/항/호/목 구조 인식
           ▼
    2. line_merger        ← 줄바꿈 병합
           ▼
    3. ocr_corrector      ← 유니코드 정규화만 (OCR 교정 불필요)
           ▼
    4. markdown_assembler ← 최종 마크다운 조립
           ▼
    최종 마크다운


(B) OCR 결과 (EasyOCR / AppleVision):
    ┌──────────────┐
    │ TextBlock[]  │  물리적 줄 단위 (깨진 줄바꿈)
    └──────┬───────┘
           ▼
    1. noise_detector     ← 헤더/푸터/워터마크 제거
           ▼
    2. line_merger         ← 줄바꿈 병합 (물리적 → 논리적 문장 복원)
           ▼
    3. text_structurer     ← 제/조/항/호/목 구조 인식 (복원된 문장 기반)
           ▼
    4. ocr_corrector       ← 전체 교정
       ├── 유니코드 정규화 (NFC, 자모 결합)
       ├── 도메인 사전 매칭 (보험/법률 용어)
       ├── 정규식 패턴 교정 (날짜, 금액, 조항번호)
       └── [Phase 2] LLM 프롬프트 기반 교정 (저신뢰 구간)
           ▼
    5. markdown_assembler  ← 최종 마크다운 조립
           ▼
    최종 마크다운
```

> **변경 사유 (C2 해결):** OCR 결과는 물리적 줄 단위이므로 `line_merger`가 `text_structurer`보다 먼저 실행.
> 디지털 텍스트는 이미 정돈되어 있으므로 `text_structurer` → `line_merger` 순서 유지.

---

## 성능 고려사항

**품질 게이트의 OCR 실행 비용 (C1 수정 반영):**
- 품질 진단에서 `is_clean=True`면 게이트 건너뜀 → OCR **1회**
- 전처리 필요한 페이지: gate에서 OCR **2회** → winning_blocks 재사용 → 추가 호출 **0회**
- ~~기존 설계: gate 2회 + 채택 이미지 재호출 1회 = 3회~~ → 수정 후 최대 **2회**
- 스캔 비중 50% × 전처리 필요 비율 ~30% = 전체 페이지의 ~15%만 2회 실행

**캐시 전략:**
- 동일 PDF 재파싱 시 ImageQualityReport 캐시 가능 (이미지 해시 기반)
- 품질 게이트 결과도 캐시 → 재파싱 시 판정 재활용

**에러 복구:**
- 전처리 실패 → 원본 폴백 (로그 기록, SelectionReason.PREPROCESSING_FAILED)
- 진단 실패 → 전처리 건너뜀 (원본 직행)
- 개별 전처리 단계 독립 → 하나 실패해도 나머지 적용 가능 (Phase 2 고려)

---

## 리뷰 반영 추적

| ID | 심각도 | 문제 | 해결 | 상태 |
|----|--------|------|------|------|
| C1 | Critical | OCR 3회 호출 | winning_blocks 반환으로 2회로 축소 | ✅ |
| C2 | Critical | 후처리 순서 오류 | 입력 소스별 분기 (디지털 vs OCR) | ✅ |
| C3 | Critical | Port-Adapter 불일치 | ImageDiagnostics/ImagePreprocessor 분리 | ✅ |
| I1 | Important | `image: Any` 타입 | RawImage wrapper 도입 | ✅ |
| I2 | Important | 임계값 하드코딩 | ImageQualityPolicy 추출 | ✅ |
| I3 | Important | orig_chars==0 엣지 | Case 0 최우선 처리 (PREP_RESCUED_EMPTY) | ✅ |
| I4 | Important | quality_report None | skew_angle을 decision에 직접 포함, report 필수화 | ✅ |
| I5 | Important | MIXED 미정의 | SCANNED 동일 경로 + 텍스트 레이어 병합 | ✅ |
| I6 | Important | 전처리 실패 폴백 없음 | try/except + PREPROCESSING_FAILED 사유 | ✅ |
| M1 | Minor | reason: str | SelectionReason enum 도입 + reason_detail | ✅ |
| M2 | Minor | _estimate_dpi 텍스트 없음 | -1.0 반환, needs_upscale에서 제외 | ✅ |
| M3 | Minor | PIL 의존 in processing | NDArray 입력으로 변경 | ✅ |
| M4 | Minor | 파라미터 4개+ | ScannedPageProcessor 클래스 추출 | ✅ |
