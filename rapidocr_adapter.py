import asyncio
import base64
import io
import json
import logging
import os
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
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
# OCR languages (engine-level config — changing language loads different model
# files). Det supports ch / en / multi; Rec language availability depends on
# the RapidOCR model list (ch, ch_doc, en, ...). Defaults to Chinese.
LANG_DET = os.getenv("RAPIDOCR_LANG_DET", "ch").strip().lower()
LANG_REC = os.getenv("RAPIDOCR_LANG_REC", "ch").strip().lower()

# --- Runtime engine settings (tier / language hot switching) ----------------
# Settings persist to the /app/data volume so they survive container rebuilds;
# env vars act as defaults. Engines are cached per (tier, det_lang, rec_lang)
# key with a small LRU cap so switching back to a recent tier is instant.

VALID_TIERS = ("tiny", "small", "medium")
VALID_DET_LANGS = ("ch", "en", "multi")
VALID_REC_LANGS = ("ch", "ch_doc", "en")
ENGINE_CACHE_LIMIT = 3
SETTINGS_PATH = Path(os.getenv("RAPIDOCR_SETTINGS_PATH", "/app/data/engine-settings.json"))


def default_engine_settings() -> dict:
    return {
        "tier": MODEL_TIER if MODEL_TIER in VALID_TIERS else "small",
        "det_lang": LANG_DET if LANG_DET in VALID_DET_LANGS else "ch",
        "rec_lang": LANG_REC if LANG_REC in VALID_REC_LANGS else "ch",
    }


def validate_engine_settings(payload: dict) -> dict:
    """Merge + validate a partial settings payload; raises ValueError on bad values."""
    merged = dict(CURRENT_SETTINGS)
    if "tier" in payload:
        if payload["tier"] not in VALID_TIERS:
            raise ValueError(f"tier must be one of {VALID_TIERS}")
        merged["tier"] = payload["tier"]
    if "det_lang" in payload:
        if payload["det_lang"] not in VALID_DET_LANGS:
            raise ValueError(f"det_lang must be one of {VALID_DET_LANGS}")
        merged["det_lang"] = payload["det_lang"]
    if "rec_lang" in payload:
        if payload["rec_lang"] not in VALID_REC_LANGS:
            raise ValueError(f"rec_lang must be one of {VALID_REC_LANGS}")
        merged["rec_lang"] = payload["rec_lang"]
    return merged


def load_engine_settings() -> dict:
    settings = default_engine_settings()
    try:
        stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        settings = validate_engine_settings(stored)
    except FileNotFoundError:
        pass
    except Exception as error:
        logger.warning("Ignoring invalid engine settings file %s: %s", SETTINGS_PATH, error)
    return settings


def save_engine_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = SETTINGS_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(SETTINGS_PATH)


CURRENT_SETTINGS: dict = default_engine_settings()
ENGINE_CACHE: "OrderedDict[tuple, Any]" = OrderedDict()
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
    logger.debug("[timing] image_to_data_url: jpeg_save %.3fs | base64 %.3fs | jpeg_size=%dKB",
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


def create_engine(settings: dict):
    """Load the RapidOCR engine for the given tier/language settings.

    Engine type, thread count and rec batch come from env (deployment-level);
    tier and languages are runtime-switchable.
    """
    from rapidocr import RapidOCR

    logger.info("Loading RapidOCR (PP-OCRv6, tier=%s, engine=%s, lang=%s/%s, threads=%d, rec_batch=%d)",
                settings["tier"], ENGINE_TYPE, settings["det_lang"], settings["rec_lang"],
                NUM_THREADS, REC_BATCH_NUM)
    return RapidOCR(params=coerce_engine_params(build_engine_params(settings)))


def coerce_engine_params(raw_params: dict) -> dict:
    """Convert string config values to the enums RapidOCR's params API requires.

    The params dict path validates strictly for Enum instances (unlike the
    yaml config path, which parses strings), so build_engine_params stays a
    pure string function and this bridges it at runtime.
    """
    from rapidocr import EngineType, LangDet, LangRec, ModelType

    enum_by_key = {
        "Det.engine_type": EngineType,
        "Det.model_type": ModelType,
        "Det.lang_type": LangDet,
        "Rec.engine_type": EngineType,
        "Rec.model_type": ModelType,
        "Rec.lang_type": LangRec,
    }
    params = {}
    for key, value in raw_params.items():
        enum_cls = enum_by_key.get(key)
        params[key] = enum_cls(str(value)) if enum_cls is not None else value
    return params


def build_engine_params(settings: dict) -> dict:
    """Assemble RapidOCR params from env config + runtime settings.

    Values are plain strings — the same form the stock config.yaml uses — so
    this stays a pure function, testable without rapidocr installed.
    """
    engine_type = ENGINE_TYPE if ENGINE_TYPE in {"onnxruntime", "openvino"} else "onnxruntime"
    params = {
        "Det.engine_type": engine_type,
        "Det.model_type": settings["tier"],
        "Det.lang_type": settings["det_lang"],
        "Rec.engine_type": engine_type,
        "Rec.model_type": settings["tier"],
        "Rec.lang_type": settings["rec_lang"],
        "Rec.rec_batch_num": REC_BATCH_NUM,
    }
    if NUM_THREADS > 0:
        if ENGINE_TYPE == "openvino":
            params["EngineConfig.openvino.num_threads"] = NUM_THREADS
        else:
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = NUM_THREADS
            params["EngineConfig.onnxruntime.inter_op_num_threads"] = 1
    return params


def engine_cache_key(settings: dict) -> tuple:
    return (settings["tier"], settings["det_lang"], settings["rec_lang"])


async def get_engine():
    """Return the engine for CURRENT_SETTINGS, building it lazily.

    Callers hold INFERENCE_LOCK, so a settings change swaps the engine strictly
    between requests — an in-flight OCR job never sees a half-reloaded engine.
    """
    global ENGINE_ERROR
    key = engine_cache_key(CURRENT_SETTINGS)
    cached = ENGINE_CACHE.get(key)
    if cached is not None:
        ENGINE_CACHE.move_to_end(key)
        return cached
    async with ENGINE_LOCK:
        cached = ENGINE_CACHE.get(key)
        if cached is not None:
            ENGINE_CACHE.move_to_end(key)
            return cached
        ENGINE_ERROR = None
        try:
            engine = await asyncio.to_thread(create_engine, dict(CURRENT_SETTINGS))
        except Exception as error:
            ENGINE_ERROR = str(error) or error.__class__.__name__
            logger.exception("Failed to load RapidOCR for %s", key)
            raise
        ENGINE_CACHE[key] = engine
        while len(ENGINE_CACHE) > ENGINE_CACHE_LIMIT:
            ENGINE_CACHE.popitem(last=False)
        return engine


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
    logger.debug("[timing] run_one: np.array %.3fs | engine(infer) %.3fs | parse %.3fs | lines=%d",
                t1 - t0, t2 - t1, t3 - t2, len(txts) if txts else 0)
    return txts, scores, boxes


@asynccontextmanager
async def lifespan(_: FastAPI):
    global CURRENT_SETTINGS
    CURRENT_SETTINGS = load_engine_settings()
    logger.info("Engine settings: %s", CURRENT_SETTINGS)
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
    if not ENGINE_CACHE:
        raise HTTPException(status_code=503, detail=ENGINE_ERROR or "RapidOCR is loading")
    return {
        "status": "ok",
        "tier": CURRENT_SETTINGS["tier"],
        "det_lang": CURRENT_SETTINGS["det_lang"],
        "rec_lang": CURRENT_SETTINGS["rec_lang"],
        "modelLoaded": True,
    }


@app.get("/engine/settings")
async def get_engine_settings():
    return dict(CURRENT_SETTINGS)


@app.put("/engine/settings")
async def put_engine_settings(request: Request):
    """Switch tier/language at runtime; persisted to the data volume.

    The engine itself reloads lazily inside INFERENCE_LOCK on the next OCR
    request, so in-flight jobs finish on the engine they started with.
    """
    global CURRENT_SETTINGS
    try:
        payload = await request.json()
    except Exception as error:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Settings payload must be an object")
    try:
        merged = validate_engine_settings(payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    save_engine_settings(merged)
    CURRENT_SETTINGS = merged
    logger.info("Engine settings updated: %s", merged)
    return dict(CURRENT_SETTINGS)


@app.post("/ocr")
async def ocr(request: Request):
    t_start = time.perf_counter()
    file_bytes, file_type = await read_input(request)
    t_input = time.perf_counter()
    pages, resolved_type = prepare_images(file_bytes, file_type)
    if not pages:
        raise HTTPException(status_code=400, detail="No images were produced for OCR")
    t_prep = time.perf_counter()
    logger.debug("[timing] request: read_input %.3fs | prepare_images(%d pages) %.3fs",
                t_input - t_start, len(pages), t_prep - t_input)
    pages_result = []
    async with INFERENCE_LOCK:
        engine = await get_engine()
        t_engine = time.perf_counter()
        for index, image in enumerate(pages):
            t_page = time.perf_counter()
            txts, scores, boxes = await asyncio.to_thread(run_one, engine, image)
            t_run = time.perf_counter()
            img_b64 = await asyncio.to_thread(image_to_data_url, image)
            t_enc = time.perf_counter()
            pages_result.append(to_ppocr_page(txts, scores, boxes, page_index=index, input_image_b64=img_b64))
            t_done = time.perf_counter()
            logger.debug("[timing] page %d/%d: run_one %.3fs | image_to_data_url %.3fs | to_ppocr %.3fs | page_total %.3fs",
                        index + 1, len(pages), t_run - t_page, t_enc - t_run, t_done - t_enc, t_done - t_page)
    t_infer = time.perf_counter()
    result = build_response(pages_result, resolved_type)
    t_build = time.perf_counter()
    logger.info("[timing] DONE: inference_loop %.3fs | build_response %.3fs | request_total %.3fs",
                t_infer - t_engine, t_build - t_infer, t_build - t_start)
    return result
