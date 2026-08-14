import asyncio
import base64
import io
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import fitz
from fastapi import FastAPI, HTTPException, Request
from PIL import Image

logging.basicConfig(level=os.getenv("RAPIDOCR_LOG_LEVEL", "INFO"))
logger = logging.getLogger("rapidocr-adapter")

MODEL_TIER = os.getenv("RAPIDOCR_MODEL_TIER", "small").strip().lower()
MODEL_NAME = os.getenv("RAPIDOCR_MODEL_NAME", f"PP-OCRv6_{MODEL_TIER}_rapid")
PDF_DPI = int(os.getenv("RAPIDOCR_PDF_DPI", "200"))
MAX_PAGES = int(os.getenv("RAPIDOCR_MAX_PAGES_PER_REQUEST", "50"))
# Inference engine: onnxruntime (default, broad compat) or openvino (Intel CPU,
# faster for PP-OCRv6's RepLKFPN + Light-SVTR). AMD CPUs should stay on ort.
ENGINE_TYPE = os.getenv("RAPIDOCR_ENGINE_TYPE", "onnxruntime").strip().lower()
# Intra-op threads. Empty = use all logical cores; on HT CPUs prefer physical
# core count (e.g. os.cpu_count()//2) for better throughput.
_threads_env = os.getenv("RAPIDOCR_NUM_THREADS", "").strip()
NUM_THREADS = int(_threads_env) if _threads_env else (os.cpu_count() or 0)
# Recognition batch size; smaller lowers single-image latency on CPU.
REC_BATCH_NUM = int(os.getenv("RAPIDOCR_REC_BATCH_NUM", "3"))

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
    t0 = time.perf_counter()
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    t1 = time.perf_counter()
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    t2 = time.perf_counter()
    logger.info("[timing] image_to_data_url: jpeg_save %.3fs | base64 %.3fs | jpeg_size=%dKB",
                t1 - t0, t2 - t1, len(buffer.getvalue()) // 1024)
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
    """Load the RapidOCR engine.

    Engine, tier, thread count and rec batch are env-configurable so the same
    image runs on Intel (openvino) or AMD (onnxruntime) without code changes.
    Models download lazily to the RapidOCR default location on first use.
    """
    from rapidocr import EngineType, ModelType, RapidOCR

    engine_map = {"onnxruntime": EngineType.ONNXRUNTIME, "openvino": EngineType.OPENVINO}
    engine_type = engine_map.get(ENGINE_TYPE, EngineType.ONNXRUNTIME)
    tier_map = {"tiny": ModelType.TINY, "small": ModelType.SMALL, "medium": ModelType.MEDIUM}
    model_type = tier_map.get(MODEL_TIER, ModelType.SMALL)
    params = {
        "Det.engine_type": engine_type,
        "Det.model_type": model_type,
        "Rec.engine_type": engine_type,
        "Rec.model_type": model_type,
        "Rec.rec_batch_num": REC_BATCH_NUM,
    }
    if NUM_THREADS > 0:
        if ENGINE_TYPE == "openvino":
            params["EngineConfig.openvino.num_threads"] = NUM_THREADS
        else:
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = NUM_THREADS
            params["EngineConfig.onnxruntime.inter_op_num_threads"] = 1
    logger.info("Loading RapidOCR (PP-OCRv6, tier=%s, engine=%s, threads=%d, rec_batch=%d)",
                MODEL_TIER, ENGINE_TYPE, NUM_THREADS, REC_BATCH_NUM)
    return RapidOCR(params=params)


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

    t0 = time.perf_counter()
    arr = np.array(image)
    t1 = time.perf_counter()
    output = engine(arr)
    t2 = time.perf_counter()
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
    t3 = time.perf_counter()
    logger.info("[timing] run_one: np.array %.3fs | engine(infer) %.3fs | parse %.3fs | lines=%d",
                t1 - t0, t2 - t1, t3 - t2, len(txts) if txts else 0)
    return txts, scores, boxes


@asynccontextmanager
async def lifespan(_: FastAPI):
    engine = await get_engine()
    # Warm up the inference engine: the first run on a new image shape incurs
    # ~1s of onnxruntime shape inference + memory arena allocation (or OpenVINO
    # graph compilation). Pre-run a dummy A4@200DPI image so the first real
    # request doesn't pay this cost.
    try:
        import numpy as np
        dummy = np.zeros((2339, 1654, 3), dtype=np.uint8)
        await asyncio.to_thread(engine, dummy)
        logger.info("Engine warmup complete")
    except Exception as error:
        logger.warning("Engine warmup failed: %s", error)
    yield


app = FastAPI(title="RapidOCR Adapter", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    if ENGINE is None:
        raise HTTPException(status_code=503, detail=ENGINE_ERROR or "RapidOCR is loading")
    return {"status": "ok", "model": MODEL_NAME, "tier": MODEL_TIER, "modelLoaded": True}


@app.post("/ocr")
async def ocr(request: Request):
    t_start = time.perf_counter()
    file_bytes, file_type = await read_input(request)
    t_input = time.perf_counter()
    pages, resolved_type = prepare_images(file_bytes, file_type)
    if not pages:
        raise HTTPException(status_code=400, detail="No images were produced for OCR")
    t_prep = time.perf_counter()
    engine = await get_engine()
    t_engine = time.perf_counter()
    logger.info("[timing] request: read_input %.3fs | prepare_images(%d pages) %.3fs | get_engine %.3fs",
                t_input - t_start, len(pages), t_prep - t_input, t_engine - t_prep)
    pages_result = []
    async with INFERENCE_LOCK:
        for index, image in enumerate(pages):
            t_page = time.perf_counter()
            txts, scores, boxes = await asyncio.to_thread(run_one, engine, image)
            t_run = time.perf_counter()
            img_b64 = await asyncio.to_thread(image_to_data_url, image)
            t_enc = time.perf_counter()
            pages_result.append(to_ppocr_page(txts, scores, boxes, page_index=index, input_image_b64=img_b64))
            t_done = time.perf_counter()
            logger.info("[timing] page %d/%d: run_one %.3fs | image_to_data_url %.3fs | to_ppocr %.3fs | page_total %.3fs",
                        index + 1, len(pages), t_run - t_page, t_enc - t_run, t_done - t_enc, t_done - t_page)
    t_infer = time.perf_counter()
    result = build_response(pages_result, resolved_type)
    t_build = time.perf_counter()
    logger.info("[timing] DONE: inference_loop %.3fs | build_response %.3fs | request_total %.3fs",
                t_infer - t_engine, t_build - t_infer, t_build - t_start)
    return result
