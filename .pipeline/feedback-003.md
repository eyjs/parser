# DocForge 추가 기능 요구사항 — 병렬 페이지 처리 & LLM Fallback

## 생성일시
2026-04-29 (기존 requirement.md 기반 증분 정의)

## 문서 위치
`.pipeline/feedback-003.md` — 기존 `requirement.md` 덮어쓰지 않음

---

## 배경

기존 Phase 1/2 요구사항(`requirement.md`)에서 예고한 기능 중,
구현 우선순위가 높아진 2건을 구체적 스펙으로 확정한다.

| # | 기능 | 현재 상태 | 목표 |
|---|------|----------|------|
| 1 | 병렬 페이지 처리 | `parse_pdf.py:158` 순차 루프 | ThreadPoolExecutor 기반 병렬화 |
| 2 | LLM Fallback | 저신뢰 페이지에 `[low OCR confidence]` 표식만 추가 | Qwen2-VL-7B MLX로 자동 교정 |

---

## 기능 1: 병렬 페이지 처리

### 문제 정의

`parse_pdf.py` 158번 줄의 `for page_idx in range(total_pages)` 루프는
100페이지 이상 문서에서 처리 시간이 페이지 수에 정비례하여 증가한다.
OCR이 필요한 스캔 페이지의 경우 1페이지당 최대 5초로, 100페이지 문서는 최대 500초 소요.

### 제약사항 분석

| 항목 | 위험 | 해결 방향 |
|------|------|-----------|
| PyMuPDF `doc` 객체 thread-safety | `fitz.Document`는 스레드 간 공유 불가 | 각 워커에서 `reader.open(pdf_path)` 독립 호출, 워커 완료 후 close |
| pdfplumber `plumber_doc` 동일 위험 | 동일 | 워커별 독립 open/close |
| EasyOCR GPU 메모리 공유 | 동시 호출 시 CUDA OOM 가능 | `Semaphore(max_ocr_workers)`로 OCR 동시 실행 수 제한 |
| Apple Vision (macOS) | GCD 기반, 멀티스레드 안전 | 제한 불필요 (기본 max_ocr_workers = max_workers) |
| 페이지 순서 보장 | 병렬 완료 순서 불규칙 | 결과를 `page_num` 기준 정렬 후 조립 |
| `on_progress` 콜백 | 멀티스레드에서 동시 호출 가능 | `threading.Lock`으로 직렬화 |
| `on_page_done` 콜백 | 동일 | 동일 Lock 사용 |

### 설계 방향

#### 실행 모드

- `config.max_workers == 1` (기본값): 기존 순차 루프 유지. 코드 분기 없이 executor 사용 (workers=1이면 자연스럽게 순차)
- `config.max_workers >= 2`: ThreadPoolExecutor 병렬 실행

#### 병렬화 단위

페이지 처리 전체(분류 → OCR → 노이즈 제거 → 구조화)를 단일 워커 함수로 추출.
Steps 1~3(프로파일링, 노이즈 패턴 학습, 문서 통계)은 병렬화 대상에서 제외하고 사전 순차 실행.
Steps 5~6(테이블 병합, 마크다운 조립)도 순차 실행 유지.

```
[순차] Step 1: profile_document
[순차] Step 2: learn_patterns
[순차] Step 3: document statistics
[병렬] Step 4: 각 페이지 처리 (ThreadPoolExecutor)
         ├── _process_single_page(page_idx, pdf_path, ...)
         └── 결과: PageContent | None (NOISE 페이지는 None)
[순차] Step 5: merge cross-page tables
[순차] Step 6: assemble markdown
```

#### 워커 함수 시그니처

```python
def _process_single_page(
    page_idx: int,
    pdf_path: Path,
    config: ParserConfig,
    patterns: NoisePatterns,
    avg_font_size: float,
    avg_line_gap: float,
    force_ocr: bool,
    progress_lock: threading.Lock,
    ocr_semaphore: threading.Semaphore,
    on_progress: Callable[[str], None] | None,
) -> PageContent | None:
    """단일 페이지 처리. doc/plumber_doc을 독립 open/close."""
    reader = PyMuPDFReader()
    doc = reader.open(pdf_path)
    table_extractor = PdfplumberTableExtractor(config)
    plumber_doc = table_extractor.open(pdf_path)
    try:
        # ... 기존 페이지 처리 로직 이동 ...
        # OCR 호출 전후로 ocr_semaphore.acquire/release
    finally:
        reader.close(doc)
        table_extractor.close(plumber_doc)
```

### 수정 대상 파일

| 파일 | 변경 유형 | 내용 |
|------|----------|------|
| `docforge/infrastructure/config.py` | 필드 추가 | `max_workers: int = 1`, `max_ocr_workers: int = 1` |
| `docforge/usecases/parse_pdf.py` | 리팩터링 | Step 4 루프를 `_process_single_page` + `ThreadPoolExecutor`로 교체 |
| `docforge/usecases/parse_pdf.py` | 신규 함수 | `_process_single_page(...)` 모듈 내 private 함수 추가 |

### 수정 방법 (parse_pdf.py Step 4)

```python
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

progress_lock = threading.Lock()
ocr_semaphore = threading.Semaphore(config.max_ocr_workers)

futures = {}
with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
    for page_idx in range(total_pages):
        future = executor.submit(
            _process_single_page,
            page_idx, pdf_path, config, patterns,
            avg_font_size, avg_line_gap, force_ocr,
            progress_lock, ocr_semaphore, on_progress,
        )
        futures[future] = page_idx

# 완료 수집 — page_num 기준 정렬 보장
raw_results: list[tuple[int, PageContent | None]] = []
for future in as_completed(futures):
    page_idx = futures[future]
    result = future.result()  # 예외 전파
    raw_results.append((page_idx, result))

raw_results.sort(key=lambda x: x[0])
parsed_pages = [r for _, r in raw_results if r is not None]
```

### 완료 기준

- [ ] `config.max_workers=1`일 때 기존 동작 100% 동일 (기존 테스트 220개 통과)
- [ ] `config.max_workers=4`일 때 4페이지 이상 문서에서 순차 대비 처리 시간 50% 이상 단축 (M1 Max 기준)
- [ ] 100페이지 테스트 문서에서 결과 마크다운이 순차 실행과 동일 (텍스트 내용, 페이지 순서)
- [ ] OCR 동시 실행 수가 `max_ocr_workers`를 초과하지 않음 (Semaphore 검증)
- [ ] `on_progress` / `on_page_done` 콜백이 스레드 안전하게 호출됨 (중복/누락 없음)
- [ ] NOISE 페이지 건너뜀 로직이 병렬 환경에서도 정확히 동작
- [ ] 기존 테스트 220개 전부 통과 (test_web.py 제외, 해당 파일은 기존 수집 오류)

---

## 기능 2: LLM Fallback (Qwen2-VL-7B MLX)

### 문제 정의

현재 `confidence_scorer.score_page()`가 낮은 점수를 반환하는 페이지에 대해
마크다운 조립 시 `[low OCR confidence]` 표식을 달아 경고만 할 뿐,
텍스트 품질을 실제로 개선하는 수단이 없다.

Qwen2-VL-7B MLX를 로컬에서 실행하여 저신뢰 페이지를 자동 교정한다.
모델은 Mac Studio M1 Max 64GB RAM에서 완전 오프라인 실행한다.

### 아키텍처 개요

```
[confidence_scorer] → overall < llm_confidence_threshold?
       YES ↓                         NO ↓
[llm_fallback_router]         기존 결과 유지
       ↓
[VisionLLMEngine.correct_page(image, ocr_blocks)]
       ↓
[품질 게이트: LLM 결과 vs 기존 OCR 비교]
       ↓ LLM 우세          ↓ LLM 열위
  LLM 결과 채택       기존 OCR 결과 유지 (graceful degradation)
```

### 도메인 모델 추가

#### `domain/ports.py`에 추가

```python
class VisionLLMEngine(Protocol):
    """Port for Vision LLM — page-level text correction."""

    def correct_page(
        self,
        image: RawImage,
        ocr_blocks: list[TextBlock],
        prompt_hint: str = "",
    ) -> list[TextBlock]:
        """저신뢰 페이지 이미지를 입력받아 교정된 TextBlock 목록 반환.
        
        ocr_blocks: 기존 OCR 결과 (컨텍스트 힌트로 활용)
        prompt_hint: 도메인 힌트 (예: "보험약관", "법률 문서")
        반환값: 교정된 TextBlock 목록 (confidence는 LLM 응답 품질 기반)
        """
        ...

    def is_available(self) -> bool:
        """MLX 모델이 로드되어 실행 가능한지 확인."""
        ...
```

#### `domain/models.py`에 추가 (기존 `PageContent` 확인 후 필요 시 추가)

```python
@dataclass(frozen=True)
class LLMFallbackRecord:
    """LLM 폴백 발생 기록. ParseResult에 포함하여 감사 로그 역할."""
    page_num: int
    trigger_confidence: float          # 폴백 트리거 당시 OCR 신뢰도
    llm_confidence: float              # LLM 결과 신뢰도
    adopted: bool                      # LLM 결과 채택 여부
    reason: str                        # 채택/기각 사유
```

### 신규 모듈 구조

```
docforge/
├── domain/
│   └── ports.py                       # + VisionLLMEngine Protocol
│
├── processing/
│   └── llm_fallback_router.py         # [신규] 신뢰도 기반 라우팅 + 품질 게이트
│
└── adapters/
    └── vision_llm_engine.py           # [신규] Qwen2-VL-7B MLX 구현체
```

### `processing/llm_fallback_router.py` 설계

```python
"""LLM Fallback 라우터.
신뢰도 임계값 미만 페이지에 대해 VisionLLM 교정을 시도.
LLM 불가 또는 결과 열위 시 graceful degradation."""

import logging
from docforge.domain.models import PageContent, LLMFallbackRecord, TextBlock
from docforge.domain.value_objects import RawImage
from docforge.infrastructure.config import ParserConfig

logger = logging.getLogger(__name__)


def should_invoke_llm(page: PageContent, config: ParserConfig) -> bool:
    """LLM 폴백 트리거 조건 판단."""
    if not config.llm_fallback_enabled:
        return False
    if page.confidence is None:
        return False
    return page.confidence.overall < config.llm_confidence_threshold


def run_llm_fallback(
    page: PageContent,
    page_image: RawImage,
    llm_engine: "VisionLLMEngine",
    config: ParserConfig,
) -> tuple[list[TextBlock], LLMFallbackRecord]:
    """LLM 교정 시도 + 품질 게이트.
    
    반환:
        (채택된 TextBlock 목록, 폴백 기록)
    """
    original_blocks = list(page.blocks)
    original_confidence = page.confidence.overall if page.confidence else 0.0

    try:
        llm_blocks = llm_engine.correct_page(
            image=page_image,
            ocr_blocks=original_blocks,
            prompt_hint=config.llm_domain_hint,
        )
    except Exception:
        logger.warning("LLM 교정 실패 (page %d), 기존 OCR 유지", page.page_num, exc_info=True)
        return original_blocks, LLMFallbackRecord(
            page_num=page.page_num,
            trigger_confidence=original_confidence,
            llm_confidence=0.0,
            adopted=False,
            reason="LLM 호출 실패 — 기존 OCR 유지",
        )

    llm_confidence = _avg_confidence(llm_blocks)
    llm_char_count = sum(len(b.text) for b in llm_blocks)
    orig_char_count = sum(len(b.text) for b in original_blocks)

    # 품질 게이트: 문자 소실 탈락
    if llm_char_count < orig_char_count * config.llm_char_loss_threshold:
        return original_blocks, LLMFallbackRecord(
            page_num=page.page_num,
            trigger_confidence=original_confidence,
            llm_confidence=llm_confidence,
            adopted=False,
            reason=f"LLM 기각 — 문자 소실 ({llm_char_count} < {orig_char_count}×{config.llm_char_loss_threshold})",
        )

    # 품질 게이트: LLM이 유의미하게 더 나을 때만 채택
    if llm_confidence > original_confidence + config.llm_confidence_margin:
        return llm_blocks, LLMFallbackRecord(
            page_num=page.page_num,
            trigger_confidence=original_confidence,
            llm_confidence=llm_confidence,
            adopted=True,
            reason=f"LLM 채택 — 신뢰도 향상 ({original_confidence:.3f} → {llm_confidence:.3f})",
        )

    # default: 기존 OCR 유지
    return original_blocks, LLMFallbackRecord(
        page_num=page.page_num,
        trigger_confidence=original_confidence,
        llm_confidence=llm_confidence,
        adopted=False,
        reason="LLM 기각 — 유의미한 개선 없음, 기존 OCR 유지",
    )


def _avg_confidence(blocks: list[TextBlock]) -> float:
    if not blocks:
        return 0.0
    return sum(b.confidence for b in blocks) / len(blocks)
```

### `adapters/vision_llm_engine.py` 설계

```python
"""Qwen2-VL-7B MLX 기반 Vision LLM 어댑터.
VisionLLMEngine 포트 구현. Apple Silicon 전용."""

import logging
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo, RawImage

logger = logging.getLogger(__name__)

_MODEL_ID = "mlx-community/Qwen2-VL-7B-Instruct-4bit"

# 보험/법률 도메인 기본 프롬프트 템플릿
_PROMPT_TEMPLATE = """\
다음 문서 페이지 이미지를 읽고, 정확한 텍스트를 추출하세요.
도메인: {domain_hint}
요구사항:
- 원문 텍스트를 그대로 추출 (요약/변형 금지)
- 표는 행|열 구조로 표현
- 조항 번호(제1조, 제2항 등) 정확히 유지
- 출력: 추출된 텍스트만 (설명 없이)
"""


class Qwen2VLMLXEngine:
    """VisionLLMEngine 포트 구현체 — Qwen2-VL-7B MLX."""

    def __init__(self, model_id: str = _MODEL_ID, max_new_tokens: int = 2048):
        self._model_id = model_id
        self._max_new_tokens = max_new_tokens
        self._model = None
        self._processor = None

    def is_available(self) -> bool:
        try:
            import mlx.core  # noqa: F401
            from mlx_vlm import load  # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure_loaded(self) -> None:
        """지연 로딩 — is_available() 확인 후 최초 호출 시 로드."""
        if self._model is not None:
            return
        from mlx_vlm import load
        self._model, self._processor = load(self._model_id)

    def correct_page(
        self,
        image: RawImage,
        ocr_blocks: list[TextBlock],
        prompt_hint: str = "보험약관",
    ) -> list[TextBlock]:
        """이미지 + OCR 힌트 → 교정된 TextBlock 목록."""
        self._ensure_loaded()

        prompt = _PROMPT_TEMPLATE.format(domain_hint=prompt_hint or "문서")
        # OCR 결과를 컨텍스트로 추가 (LLM이 참조 가능)
        if ocr_blocks:
            ocr_preview = " ".join(b.text for b in ocr_blocks[:10])
            prompt += f"\n참고 OCR 텍스트(일부): {ocr_preview[:200]}"

        pil_image = _raw_image_to_pil(image)
        corrected_text = self._run_inference(pil_image, prompt)
        return _text_to_blocks(corrected_text, image)

    def _run_inference(self, pil_image: object, prompt: str) -> str:
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config

        config = load_config(self._model_id)
        formatted = apply_chat_template(
            self._processor, config, prompt, num_images=1,
        )
        result = generate(
            self._model, self._processor, pil_image,
            formatted, max_tokens=self._max_new_tokens, verbose=False,
        )
        return result


def _raw_image_to_pil(image: RawImage) -> object:
    """RawImage → PIL.Image 변환."""
    from PIL import Image
    import numpy as np
    data = image.data
    if image.channels == 1:
        return Image.fromarray(data.squeeze(), mode="L")
    return Image.fromarray(data.astype(np.uint8), mode="RGB")


def _text_to_blocks(text: str, image: RawImage) -> list[TextBlock]:
    """LLM 출력 텍스트를 TextBlock 목록으로 변환.
    LLM은 bbox를 제공하지 않으므로 전체 페이지 bbox를 사용하고 confidence=0.9 고정."""
    from docforge.domain.enums import BlockType
    from docforge.domain.models import TextBlock
    from docforge.domain.value_objects import BBox, FontInfo

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    full_bbox = BBox(x0=0.0, y0=0.0, x1=float(image.width), y1=float(image.height))
    default_font = FontInfo(size=10.0, is_bold=False, name="unknown")

    return [
        TextBlock(
            text=line,
            bbox=full_bbox,
            font=default_font,
            block_type=BlockType.PARAGRAPH,
            heading_level=None,
            confidence=0.9,
        )
        for line in lines
    ]
```

### `infrastructure/config.py`에 추가할 필드

```python
# LLM Fallback
llm_fallback_enabled: bool = False          # 기본 비활성 (opt-in)
llm_confidence_threshold: float = 0.7       # 이 값 미만 페이지에 LLM 투입
llm_confidence_margin: float = 0.05         # LLM 채택 최소 개선폭
llm_char_loss_threshold: float = 0.8        # LLM 기각: 문자 소실 비율 기준
llm_domain_hint: str = "보험약관"            # VisionLLM 프롬프트 도메인 힌트
```

### `usecases/parse_pdf.py` 통합 지점

Step 4 페이지 처리 루프 내에서 `confidence_scorer.score_page()` 직후 삽입:

```python
# 기존 신뢰도 계산 (변경 없음)
page_confidence = confidence_scorer.score_page(
    merged_blocks, page_type, width, height, page_gate_result,
)

# [신규] LLM Fallback
from docforge.processing.llm_fallback_router import should_invoke_llm, run_llm_fallback

page_content_draft = PageContent(
    page_num=page_idx + 1, page_type=page_type,
    blocks=tuple(merged_blocks), tables=tuple(page_tables),
    raw_text=raw_text, width=width, height=height,
    confidence=page_confidence,
)

if llm_engine is not None and should_invoke_llm(page_content_draft, config):
    page_image_raw = pil_to_raw_image(reader.render_page_image(doc, page_idx, config.dpi))
    final_blocks, fallback_record = run_llm_fallback(
        page_content_draft, page_image_raw, llm_engine, config,
    )
    llm_fallback_records.append(fallback_record)
    merged_blocks = final_blocks
```

`llm_engine` 초기화 (parse_pdf 함수 상단 어댑터 초기화 구간):

```python
# LLM Fallback 엔진 (opt-in, 불가 시 graceful skip)
llm_engine = None
if config.llm_fallback_enabled:
    try:
        from docforge.adapters.vision_llm_engine import Qwen2VLMLXEngine
        _candidate = Qwen2VLMLXEngine()
        if _candidate.is_available():
            llm_engine = _candidate
        else:
            logger.info("LLM fallback 비활성 — mlx_vlm 미설치")
    except Exception:
        logger.warning("LLM engine 초기화 실패, LLM fallback 비활성", exc_info=True)
```

### 수정 대상 파일 (기능 2)

| 파일 | 변경 유형 | 내용 |
|------|----------|------|
| `docforge/domain/ports.py` | Protocol 추가 | `VisionLLMEngine` |
| `docforge/domain/models.py` | 모델 추가 | `LLMFallbackRecord` frozen dataclass |
| `docforge/infrastructure/config.py` | 필드 추가 | `llm_fallback_enabled`, `llm_confidence_threshold`, `llm_confidence_margin`, `llm_char_loss_threshold`, `llm_domain_hint` |
| `docforge/processing/llm_fallback_router.py` | 신규 | 신뢰도 기반 라우팅 + 품질 게이트 |
| `docforge/adapters/vision_llm_engine.py` | 신규 | Qwen2-VL-7B MLX 구현체 |
| `docforge/usecases/parse_pdf.py` | 통합 | llm_engine 초기화 + Step 4 내 fallback 호출 |

### 완료 기준

- [ ] `config.llm_fallback_enabled=False`(기본)일 때 기존 동작 100% 동일 (기존 테스트 220개 통과)
- [ ] `VisionLLMEngine` Protocol이 `domain/ports.py`에 정의되어 있고, mypy strict 통과
- [ ] `Qwen2VLMLXEngine.is_available()` — mlx_vlm 미설치 시 `False` 반환 (ImportError 노출 없음)
- [ ] LLM 호출 실패 시 기존 OCR 결과 유지 (예외가 파싱 파이프라인 전체를 중단시키지 않음)
- [ ] LLM 결과가 문자 소실 기준 미달 시 기각하고 기존 OCR 결과 유지
- [ ] `LLMFallbackRecord` 목록이 로그에 기록됨 (채택/기각 사유 포함)
- [ ] 실제 Qwen2-VL-7B 모델 로드 + 저신뢰 페이지 1건 교정 통합 테스트 통과 (M1 Max 환경)

---

## 공통 제약사항

| 항목 | 내용 |
|------|------|
| 기존 테스트 | 220개 (test_web.py 수집 오류 기존 이슈, 미포함) 전부 통과 필수 |
| 아키텍처 레이어 | 기존 `domain/usecases/processing/adapters` 경계 유지 |
| 불변성 | 기존 `ParserConfig` frozen 유지. 신규 필드도 frozen dataclass 준수 |
| Graceful degradation | 병렬화 오류, LLM 불가 — 모두 기존 순차/OCR 결과로 폴백 |
| 외부 API | 완전 로컬 처리. 외부 네트워크 호출 금지 |
| Python 버전 | 3.11+ |
| 타입 안전성 | mypy strict. `any` 타입 금지 |

---

## 구현 우선순위 권장

1. **기능 1 (병렬 처리)** 먼저 구현 — config 필드 추가와 parse_pdf.py 리팩터링으로 즉각적 성능 개선 가능. LLM 의존성 없음.
2. **기능 2 (LLM Fallback)** 이후 구현 — mlx_vlm 설치 + 모델 다운로드 선행 필요. opt-in 플래그(`llm_fallback_enabled=False` 기본값)로 기존 파이프라인과 격리.

---

## 특이사항

- 기능 1에서 `_process_single_page`가 내부적으로 `reader.open()`을 호출하므로 PyMuPDF 라이선스 상 동일 프로세스 내 다중 open은 허용됨. 단, 동일 `doc` 객체 공유는 금지.
- EasyOCR GPU 메모리 제한은 `max_ocr_workers=1` 기본값으로 안전하게 시작. 실제 측정 후 상향 조정.
- Qwen2-VL-7B 4bit 양자화 모델 기준 M1 Max 64GB에서 VRAM 약 5~8GB 예상. Apple Vision과 동시 실행 가능.
- LLM bbox 정보 부재 문제: `_text_to_blocks`에서 전체 페이지 bbox를 임시 사용. Phase 3에서 LLM 출력 포맷을 구조화 JSON으로 개선하여 bbox 포함 고려.
- 기존 `requirement.md`의 Phase 2 항목("Qwen2-VL-7B MLX VisionLLMBackend 어댑터", "하이브리드 폴백 파이프라인")과 동일 기능이며, 이 문서가 구현 스펙을 확정한다.
