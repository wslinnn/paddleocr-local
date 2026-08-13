import asyncio
import base64
import io
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import fitz
from fastapi import FastAPI, HTTPException, Request
from PIL import Image

logging.basicConfig(level=os.getenv("RAPIDOCR_LOG_LEVEL", "INFO"))
logger = logging.getLogger("rapidocr-adapter")

MODEL_TIER = os.getenv("RAPIDOCR_MODEL_TIER", "medium").strip().lower()
MODEL_NAME = os.getenv("RAPIDOCR_MODEL_NAME", "PP-OCRv6_medium_rapid")
PDF_DPI = int(os.getenv("RAPIDOCR_PDF_DPI", "200"))
MAX_PAGES = int(os.getenv("RAPIDOCR_MAX_PAGES_PER_REQUEST", "50"))

ENGINE = None
ENGINE_ERROR: str | None = None
ENGINE_LOCK = asyncio.Lock()
INFERENCE_LOCK = asyncio.Lock()


def boxes_to_bbox(box):
    """4-point polygon -> axis-aligned bounding box [x1, y1, x2, y2]."""
    xs = [point[0] for point in box]
    ys = [point[1] for point in box]
    return [min(xs), min(ys), max(xs), max(ys)]


def to_ppocr_page(txts, scores, boxes, page_index: int = 0) -> dict:
    """Convert one RapidOCR page result into the paddleocr-ocr-api prunedResult shape.

    Aligns with server.parse_ppocr_response so the main service can reuse it:
    rec_texts / rec_scores / rec_boxes (axis-aligned) / rec_polys (4-point).
    """
    boxes = list(boxes or [])
    return {
        "prunedResult": {
            "rec_texts": list(txts or []),
            "rec_scores": list(scores or []),
            "rec_boxes": [boxes_to_bbox(box) for box in boxes],
            "rec_polys": [[list(point) for point in box] for box in boxes],
            "page_index": page_index,
        },
        "inputImage": None,
    }


def build_response(pages_result: list[dict], file_type: int) -> dict[str, Any]:
    return {
        "result": {"ocrResults": pages_result},
        "model": MODEL_NAME,
        "fileType": file_type,
    }


def create_engine():
    """Load the RapidOCR engine.

    RapidOCR 3.x defaults to the latest PP-OCRv6 series on CPU via onnxruntime.
    RAPIDOCR_MODEL_TIER is accepted as a hint; precise tier selection (explicit
    det/rec onnx paths) is a Docker-time tuning concern (see design §12).
    """
    from rapidocr import RapidOCR

    logger.info("Loading RapidOCR (PP-OCRv6, tier=%s)", MODEL_TIER)
    return RapidOCR()


async def get_engine():
    global ENGINE, ENGINE_ERROR
    if ENGINE is not None:
        return ENGINE
    async with ENGINE_LOCK:
        if ENGINE is not None:
            return ENGINE
        ENGINE_ERROR = None
        try:
            ENGINE = await asyncio.to_thread(create_engine)
            return ENGINE
        except Exception as error:
            ENGINE_ERROR = str(error) or error.__class__.__name__
            logger.exception("Failed to load RapidOCR")
            raise


def render_pdf(file_bytes: bytes) -> list[Image.Image]:
    scale = PDF_DPI / 72
    with fitz.open(stream=file_bytes, filetype="pdf") as document:
        if len(document) > MAX_PAGES:
            raise HTTPException(status_code=400, detail=f"PDF exceeds the {MAX_PAGES}-page request limit")
        return [
            Image.open(io.BytesIO(page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")))
            .convert("RGB")
            for page in document
        ]


def prepare_images(file_bytes: bytes, file_type: int | None) -> tuple[list[Image.Image], int]:
    resolved = file_type if file_type is not None else (0 if file_bytes.startswith(b"%PDF") else 1)
    if resolved == 0:
        return render_pdf(file_bytes), resolved
    try:
        return [Image.open(io.BytesIO(file_bytes)).convert("RGB")], resolved
    except Exception as error:
        raise HTTPException(status_code=400, detail="Unsupported or corrupt image") from error


async def read_input(request: Request) -> tuple[bytes, int | None]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if not upload or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="Missing multipart field: file")
        value = form.get("fileType")
        return await upload.read(), int(value) if value not in (None, "") else None
    try:
        payload = await request.json()
    except Exception as error:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from error
    raw_file = payload.get("file") or payload.get("image")
    if not raw_file:
        raise HTTPException(status_code=400, detail="Missing JSON field: file")
    encoded = str(raw_file).split("base64,", 1)[1] if "base64," in str(raw_file) else str(raw_file)
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as error:
        raise HTTPException(status_code=400, detail="Invalid base64 file payload") from error
    value = payload.get("fileType")
    return data, int(value) if value is not None else None


def run_one(engine, image: Image.Image):
    """Run OCR on one image, return (txts, scores, boxes).

    RapidOCR 3.x returns a RapidOCROutput dataclass (.boxes/.txts/.scores).
    The legacy rapidocr_onnxruntime package returns a (result, elapse) tuple
    where result is a list of [box, text, score]; handle both.
    """
    import numpy as np

    output = engine(np.array(image))
    if hasattr(output, "boxes"):
        boxes = output.boxes
        txts = output.txts
        scores = output.scores
    else:
        result, _elapse = output
        txts, boxes, scores = [], [], []
        for line in result or []:
            boxes.append(line[0])
            txts.append(line[1])
            scores.append(line[2])
    return txts, scores, boxes


@asynccontextmanager
async def lifespan(_: FastAPI):
    await get_engine()
    yield


app = FastAPI(title="RapidOCR Adapter", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    if ENGINE is None:
        raise HTTPException(status_code=503, detail=ENGINE_ERROR or "RapidOCR is loading")
    return {"status": "ok", "model": MODEL_NAME, "tier": MODEL_TIER, "modelLoaded": True}


@app.post("/ocr")
async def ocr(request: Request):
    file_bytes, file_type = await read_input(request)
    pages, resolved_type = prepare_images(file_bytes, file_type)
    if not pages:
        raise HTTPException(status_code=400, detail="No images were produced for OCR")
    engine = await get_engine()
    pages_result = []
    async with INFERENCE_LOCK:
        for index, image in enumerate(pages):
            txts, scores, boxes = await asyncio.to_thread(run_one, engine, image)
            pages_result.append(to_ppocr_page(txts, scores, boxes or [], page_index=index))
    return build_response(pages_result, resolved_type)
