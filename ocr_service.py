"""Apple Vision OCR host service.

Run this on the macOS host to expose Apple Vision OCR as an HTTP endpoint.
Docker containers call this via host.docker.internal:5052.

Usage:
    python ocr_service.py                  # default port 5052
    python ocr_service.py --port 5053      # custom port
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys

from flask import Flask, request, jsonify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from docforge.adapters.apple_vision_engine import AppleVisionOCREngine
        _engine = AppleVisionOCREngine()
        if not _engine.is_available():
            logger.error("Apple Vision is not available on this system")
            sys.exit(1)
        logger.info("Apple Vision OCR engine initialized")
    return _engine


@app.route("/health", methods=["GET"])
def health():
    engine = _get_engine()
    return jsonify({"status": "ok", "engine": "apple_vision", "available": engine.is_available()})


@app.route("/recognize", methods=["POST"])
def recognize():
    if "image" not in request.files:
        return jsonify({"error": "no image file provided"}), 400

    image_file = request.files["image"]
    image_bytes = image_file.read()

    from PIL import Image
    pil_image = Image.open(io.BytesIO(image_bytes))

    engine = _get_engine()
    blocks = engine.recognize(pil_image)

    result = []
    for block in blocks:
        result.append({
            "text": block.text,
            "bbox": {
                "x0": block.bbox.x0,
                "y0": block.bbox.y0,
                "x1": block.bbox.x1,
                "y1": block.bbox.y1,
            },
            "confidence": block.confidence,
        })

    logger.info("Recognized %d blocks from image (%dx%d)", len(result), pil_image.width, pil_image.height)
    return jsonify({"blocks": result})


def main():
    parser = argparse.ArgumentParser(description="Apple Vision OCR Service")
    parser.add_argument("--port", type=int, default=5052)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    _get_engine()
    logger.info("Starting OCR service on %s:%d", args.host, args.port)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
