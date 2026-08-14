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
MODEL_CACHE_DIR = os.getenv("RAPIDOCR_MODEL_CACHE_DIR", "/root/.cache/rapidocr")
MODEL_NAME = os.getenv("RAPIDOCR_MODEL_NAME", "PP-OCRv6_medium_rapid")
PDF_DPI = int(os.getenv("RAPIDOCR_PDF_DPI", "200"))
MAX_PAGES = int(os.getenv("RAPIDOCR_MAX_PAGES_PER_REQUEST", "50"))

ENGINE = None
ENGINE_ERROR: str | None = None
ENGINE_LOCK = asyncio.Lock()
INFERENCE_LOCK = asyncio.Lock()


def boxes_to_bbox(box):
    """4-point polygon -> axis-aligned bounding box [x1, y1, x2, y2]."""
    xs = [float(point[0]) for point in box]
    ys = [float(point[1]) for point in box]
    return [min(xs), min(ys), max(xs), max(ys)]


def image_to_data_url(image: Image.Image) -> str:
    """PIL.Image -> JPEG base64 data URL for the frontend visualization layer.

    The frontend positions text boxes as box.x / img.naturalWidth, so the image
    returned here MUST be the exact image OCR ran on (same pixel dimensions as the
    box coordinate space) — do not resize, only re-encode.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def to_ppocr_page(txts, scores, boxes, page_index: int = 0, input_image_b64: str | None = None) -> dict:
    """Convert one RapidOCR page result into the paddleocr-ocr-api prunedResult shape.

    RapidOCR returns boxes as a numpy array (N×4×2) and txts/scores as tuples,
    so we avoid `boxes or []` (numpy's truth value is ambiguous on multi-element
    arrays) and coerce every value to a native Python type for JSON serialization.
    """
    box_list = list(boxes) if boxes is not None else []
    return {
        "prunedResult": {
            "rec_texts": [str(text) for text in (txts or ())],
            "rec_scores": [float(score) for score in (scores or ())],
            "rec_boxes": [boxes_to_bbox(box) for box in box_list],
            "rec_polys": [[[float(p[0]), float(p[1])] for p in box] for box in box_list],
            "page_index": page_index,
        },
        "inputImage": input_image_b64,
    }


def build_response(pages_result: list[dict], file_type: int) -> dict[str, Any]:
    return {
        "result": {"ocrResults": pages_result},
        "model": MODEL_NAME,
        "fileType": file_type,
    }


def create_engine():
    """Load the RapidOCR engine configured for PP-OCRv6 at the requested tier.

    RapidOCR() defaults to PP-OCRv6 small; to honor RAPIDOCR_MODEL_TIER we pass
    explicit model_type via params. Global.model_root_dir points at the docker
    volume so downloaded models survive container rebuilds.
    """
    from rapidocr import ModelType, RapidOCR

    tier_map = {"tiny": ModelType.TINY, "small": ModelType.SMALL, "medium": ModelType.MEDIUM}
    model_type = tier_map.get(MODEL_TIER, ModelType.MEDIUM)
    logger.info("Loading RapidOCR (PP-OCRv6, tier=%s, cache=%s)", MODEL_TIER, MODEL_CACHE_DIR)
    return RapidOCR(
        params={
            "Global.model_root_dir": MODEL_CACHE_DIR,
            "Det.model_type": model_type,
            "Rec.model_type": model_type,
        }
    )


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
            img_b64 = await asyncio.to_thread(image_to_data_url, image)
            pages_result.append(to_ppocr_page(txts, scores, boxes, page_index=index, input_image_b64=img_b64))
    return build_response(pages_result, resolved_type)
