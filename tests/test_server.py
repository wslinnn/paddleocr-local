import asyncio
import base64
import importlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter


class ServerTaskApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        os.environ["PANDOCR_TASK_DATA_DIR"] = cls.temp_dir.name
        os.environ["PANDOCR_MAX_UPLOAD_MB"] = "1"
        os.environ["PANDOCR_MODEL_CONTROL"] = "none"
        os.environ["PANDOCR_API_TOKEN"] = ""
        cls.server = importlib.import_module("server")
        cls.client = TestClient(cls.server.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.temp_dir.cleanup()

    def test_task_list_returns_summaries_and_detail_endpoint_returns_full_task(self):
        task = {
            "id": "task_123",
            "name": "sample.pdf",
            "sourceKind": "pdf",
            "modelId": "pp-ocrv6",
            "modelName": "PP-OCRv6",
            "size": 1200,
            "createdAt": 100,
            "updatedAt": 200,
            "status": "processing",
            "pageCount": 3,
            "sourceDataUrl": "data:application/pdf;base64,JVBERi0=",
            "batches": [
                {"id": "b1", "status": "completed", "pageCount": 1},
                {"id": "b2", "status": "pending", "pageCount": 2},
            ],
            "markdown": "# Result",
            "images": {"ocr_images/a.jpg": "abc"},
            "ocrResults": [{"markdown": {"text": "# Result"}}],
        }

        put_response = self.client.put("/api/tasks/task_123", json=task)
        self.assertEqual(put_response.status_code, 200)

        list_response = self.client.get("/api/tasks")
        self.assertEqual(list_response.status_code, 200)
        summary = list_response.json()["tasks"][0]
        self.assertEqual(summary["id"], "task_123")
        self.assertEqual(summary["modelId"], "pp-ocrv6")
        self.assertEqual(summary["modelName"], "PP-OCRv6")
        self.assertEqual(summary["completedPages"], 1)
        self.assertTrue(summary["hasMarkdown"])
        self.assertNotIn("sourceDataUrl", summary)
        self.assertNotIn("batches", summary)
        self.assertNotIn("ocrResults", summary)

        detail_response = self.client.get("/api/tasks/task_123")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["sourceDataUrl"], task["sourceDataUrl"])
        self.assertEqual(detail["batches"], task["batches"])
        self.assertTrue(detail["detailLoaded"])

    def test_task_list_sorts_mixed_timestamp_formats(self):
        numeric_dir = Path(self.temp_dir.name) / "task_sort_numeric"
        iso_dir = Path(self.temp_dir.name) / "task_sort_iso"
        numeric_dir.mkdir(parents=True, exist_ok=True)
        iso_dir.mkdir(parents=True, exist_ok=True)
        (numeric_dir / "task.json").write_text(
            json.dumps({"id": "task_sort_numeric", "updatedAt": 4102444800}),
            encoding="utf-8",
        )
        (iso_dir / "task.json").write_text(
            json.dumps({"id": "task_sort_iso", "updatedAt": "1970-01-01T00:01:00Z"}),
            encoding="utf-8",
        )

        response = self.client.get("/api/tasks")
        self.assertEqual(response.status_code, 200)
        ids = [task["id"] for task in response.json()["tasks"]]
        self.assertLess(ids.index("task_sort_numeric"), ids.index("task_sort_iso"))

    def test_model_list_includes_vl_and_ppocrv6(self):
        response = self.client.get("/api/models")
        self.assertEqual(response.status_code, 200)
        model_ids = [model["id"] for model in response.json()["data"]]
        self.assertIn("paddleocr-vl-1.6", model_ids)
        self.assertIn("pp-ocrv6", model_ids)
        self.assertNotIn("unlimited-ocr", model_ids)

    def test_model_runtime_reports_both_models(self):
        with patch.object(self.server, "fetch_http_health", new=AsyncMock(return_value=(False, {}))):
            response = self.client.get("/api/model-runtime")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("models", payload)
        self.assertIn("paddleocr-vl-1.6", payload["models"])
        self.assertIn("pp-ocrv6", payload["models"])
        self.assertIn("controlAvailable", payload)
        self.assertIn("ocrActiveCount", payload)
        self.assertIn("maxConcurrentOcr", payload)

    def test_model_catalog_can_include_all_independent_models(self):
        with (
            patch.object(
                self.server,
                "MODEL_CATALOG_ENV",
                "paddleocr-vl-1.6,pp-ocrv6,unlimited-ocr,ovisocr2",
            ),
            patch.dict(
                os.environ,
                {"PANDOCR_MODEL_CATALOG": "paddleocr-vl-1.6,pp-ocrv6,unlimited-ocr,ovisocr2"},
            ),
        ):
            self.assertEqual(
                self.server.parse_model_catalog(),
                ["paddleocr-vl-1.6", "pp-ocrv6", "unlimited-ocr", "ovisocr2"],
            )
        self.assertEqual(
            {
                "paddleocr-vl-1.6": self.server.services_for_model_deploy("paddleocr-vl-1.6"),
                "pp-ocrv6": self.server.services_for_model_deploy("pp-ocrv6"),
                "unlimited-ocr": self.server.services_for_model_deploy("unlimited-ocr", "transformers"),
                "ovisocr2": self.server.services_for_model_deploy("ovisocr2"),
            },
            {
                "paddleocr-vl-1.6": ["paddleocr-vlm-server", "paddleocr-vl-api"],
                "pp-ocrv6": ["paddleocr-ocr-api"],
                "unlimited-ocr": ["unlimited-ocr-api"],
                "ovisocr2": ["ovisocr2-api"],
            },
        )

    def test_model_runtime_switch_requires_docker_control(self):
        with patch.object(self.server, "API_TOKEN", "t"), \
             patch.object(self.server, "model_control_available", return_value=False):
            response = self.client.post(
                "/api/model-runtime/switch",
                json={"modelId": "pp-ocrv6"},
                headers={"Authorization": "Bearer t"},
            )
        self.assertEqual(response.status_code, 503)

    def test_dynamic_docker_build_context_uses_project_dockerfiles(self):
        context = self.server.make_docker_build_context("unlimited-ocr-sglang")
        with tarfile.open(fileobj=io.BytesIO(context), mode="r") as tar:
            self.assertIn("Dockerfile", tar.getnames())
            self.assertIn("unlimited_ocr_adapter.py", tar.getnames())
            dockerfile = tar.extractfile("Dockerfile").read()

        expected = (self.server.PROJECT_ROOT / "Dockerfile.unlimited-ocr-sglang").read_bytes()
        self.assertEqual(dockerfile, expected)
        self.assertEqual(
            self.server.docker_build_args_for("unlimited-ocr-sglang"),
            {"UNLIMITED_OCR_SGLANG_WHEEL_URL": self.server.UNLIMITED_OCR_SGLANG_WHEEL_URL},
        )
        self.assertEqual(
            self.server.docker_build_args_for("paddleocr-ocr-api"),
            {"API_IMAGE_TAG_SUFFIX": self.server.API_IMAGE_TAG_SUFFIX},
        )

    def test_ovisocr2_build_context_and_runtime_service(self):
        context = self.server.make_docker_build_context("ovisocr2-api")
        with tarfile.open(fileobj=io.BytesIO(context), mode="r") as tar:
            self.assertIn("Dockerfile", tar.getnames())
            self.assertIn("ovisocr2_adapter.py", tar.getnames())

        self.assertEqual(self.server.docker_image_name_for("ovisocr2-api"), "pandocr-ovisocr2:latest")
        self.assertEqual(self.server.services_for_model_deploy("ovisocr2"), ["ovisocr2-api"])
        web_dockerfile = (self.server.PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY ovisocr2_adapter.py .", web_dockerfile)
        self.assertIn("COPY Dockerfile.ovisocr2 ./", web_dockerfile)
        container_config = self.server.container_payload_for(
            "ovisocr2-api",
            host_root=str(self.server.PROJECT_ROOT),
            network_name="test-network",
        )
        self.assertIn(f"OVISOCR2_KV_CACHE_MEMORY_MB={self.server.OVISOCR2_KV_CACHE_MEMORY_MB}", container_config["Env"])
        self.assertIn(
            f"OVISOCR2_STARTUP_MEMORY_FRACTION={self.server.OVISOCR2_STARTUP_MEMORY_FRACTION}",
            container_config["Env"],
        )
        self.assertIn(f"OVISOCR2_MAX_MODEL_LEN={self.server.OVISOCR2_MAX_MODEL_LEN}", container_config["Env"])
        self.assertIn(f"OVISOCR2_MAX_NUM_SEQS={self.server.OVISOCR2_MAX_NUM_SEQS}", container_config["Env"])
        self.assertIn(
            f"OVISOCR2_GDN_PREFILL_BACKEND={self.server.OVISOCR2_GDN_PREFILL_BACKEND}",
            container_config["Env"],
        )
        self.assertFalse(any(value.startswith("OVISOCR2_GPU_MEMORY_UTILIZATION=") for value in container_config["Env"]))

    def test_runtime_settings_can_persist_unlimited_ocr_backend(self):
        settings_path = self.server.RUNTIME_SETTINGS_FILE
        previous = settings_path.read_text(encoding="utf-8") if settings_path.exists() else None
        try:
            self.server.save_runtime_settings({"unlimitedOcrBackend": "sglang"})

            self.assertEqual(self.server.load_runtime_settings()["unlimitedOcrBackend"], "sglang")
            self.assertEqual(self.server.initial_unlimited_ocr_backend(), "sglang")
        finally:
            if previous is None:
                settings_path.unlink(missing_ok=True)
            else:
                settings_path.write_text(previous, encoding="utf-8")

    def test_unlimited_ocr_backend_switch_restores_previous_backend_on_failure(self):
        previous_backend = self.server.unlimited_ocr_runtime_backend
        previous_lock = self.server.model_runtime_lock
        self.server.unlimited_ocr_runtime_backend = "sglang"
        self.server.model_runtime_lock = asyncio.Lock()
        ensure_mock = AsyncMock(side_effect=[RuntimeError("preload failed"), None])
        try:
            with (
                patch.object(self.server, "model_runtime_status", new=AsyncMock(return_value={"running": True})),
                patch.object(self.server, "ensure_unlimited_ocr_backend_runtime", new=ensure_mock),
                patch.object(self.server.logger, "exception"),
            ):
                asyncio.run(self.server.activate_unlimited_ocr_backend("transformers"))

            self.assertEqual(self.server.unlimited_ocr_runtime_backend, "sglang")
            self.assertEqual([call.args[0] for call in ensure_mock.await_args_list], ["transformers", "sglang"])
            self.assertEqual(self.server.model_runtime_operation["state"], "error")
        finally:
            self.server.unlimited_ocr_runtime_backend = previous_backend
            self.server.model_runtime_lock = previous_lock
            self.server.set_model_runtime_operation("idle", "", "paddleocr-vl-1.6")

    def test_cross_origin_mutation_is_rejected_without_allowlisted_origin(self):
        response = self.client.post(
            "/api/model-runtime/switch",
            json={"modelId": "pp-ocrv6"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_allowlisted_origin_can_reach_api(self):
        with patch.object(self.server, "API_TOKEN", "t"), \
             patch.object(self.server, "model_control_available", return_value=False):
            response = self.client.post(
                "/api/model-runtime/switch",
                json={"modelId": "pp-ocrv6"},
                headers={"Origin": "http://localhost:8000", "Authorization": "Bearer t"},
            )
        self.assertEqual(response.status_code, 503)

    def test_invalid_task_id_is_rejected(self):
        response = self.client.get("/api/tasks/bad!")
        self.assertEqual(response.status_code, 400)

    def test_oversized_request_is_rejected_before_proxying(self):
        large_payload = {"image": "x" * (2 * 1024 * 1024), "fileType": 1}
        response = self.client.post("/api/paddleocr-vl-1.6", json=large_payload)
        self.assertEqual(response.status_code, 413)

    def test_ppocr_response_is_normalized_for_existing_frontend(self):
        response = self.server.parse_ppocr_response(
            {
                "result": {
                    "ocrResults": [
                        {
                            "inputImage": "base64-page-image",
                            "prunedResult": {
                                "page_index": 0,
                                "rec_texts": ["Hello", "World"],
                                "rec_scores": [0.98, 0.95],
                                "rec_boxes": [[1, 2, 30, 10], [1, 14, 40, 22]],
                            }
                        }
                    ]
                }
            }
        )

        self.assertEqual(response["markdown"], "Hello\nWorld")
        self.assertEqual(len(response["layoutParsingResults"]), 1)
        page = response["layoutParsingResults"][0]
        self.assertEqual(page["parser"], "pp-ocrv6")
        self.assertEqual(page["pageImage"], "base64-page-image")
        self.assertEqual(page["ocrLines"][0]["text"], "Hello")
        self.assertEqual(page["ocrLines"][0]["box"], [1, 2, 30, 10])

    def test_unlimited_ocr_response_is_normalized_for_existing_frontend(self):
        response = self.server.parse_unlimited_ocr_response(
            {
                "markdown": "# Parsed\n\nBody",
                "layoutParsingResults": [
                    {
                        "parser": "unlimited-ocr",
                        "markdown": {"text": "# Parsed\n\nBody", "images": {}},
                    }
                ],
            }
        )

        self.assertEqual(response["markdown"], "# Parsed\n\nBody")
        self.assertEqual(response["images"], {})
        self.assertEqual(response["layoutParsingResults"][0]["parser"], "unlimited-ocr")

    def test_unlimited_ocr_layout_tags_are_converted_to_markdown(self):
        raw = (
            "<|det|>header [1, 2, 3, 4]<|/det|>Baidu "
            "<|det|>title [10, 20, 30, 40]<|/det|>Unlimited OCR Works "
            "<|det|>title [10, 50, 30, 70]<|/det|>Abstract "
            "<|det|>text [10, 80, 90, 120]<|/det|>Body text. "
            "<|det|>image_caption [10, 130, 90, 150]<|/det|>Figure 1. Caption."
        )
        response = self.server.parse_unlimited_ocr_response({"markdown": raw})

        self.assertNotIn("<|det|>", response["markdown"])
        self.assertNotIn("Baidu", response["markdown"])
        self.assertIn("# Unlimited OCR Works", response["markdown"])
        self.assertIn("## Abstract", response["markdown"])
        self.assertIn("Body text.", response["markdown"])
        self.assertIn("*Figure 1. Caption.*", response["markdown"])

    def test_unlimited_ocr_stream_position_tracks_page_reset(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        raw = (
            "<|det|>text [10, 850, 900, 930]<|/det|>End of page one. "
            "<|det|>text [10, 30, 900, 90]<|/det|>Start of page two."
        )
        position = adapter.streaming_source_position(raw, 2)

        self.assertEqual(position["pageIndex"], 1)
        self.assertEqual(position["pageNumber"], 2)
        self.assertLess(position["pageProgress"], 0.1)
        self.assertEqual(position["bbox"], [10.0, 30.0, 900.0, 90.0])
        self.assertEqual(position["pageWidth"], 1000)
        self.assertEqual(position["pageHeight"], 1000)

    def test_unlimited_ocr_stream_position_uses_pdf_text_anchor_for_batch_pages(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        raw = (
            "<|det|>text [100, 700, 900, 760]<|/det|>"
            "Traditional OCR models adopt a pipeline architecture. "
            "<|det|>image [100, 100, 500, 400]<|/det|>"
        )
        page_texts = [
            adapter.normalize_anchor_text("Introduction and summary text."),
            adapter.normalize_anchor_text("Traditional OCR models adopt a pipeline architecture for document parsing."),
        ]

        position = adapter.streaming_source_position(raw, 2, page_texts)

        self.assertEqual(position["pageIndex"], 1)
        self.assertEqual(position["pageNumber"], 2)
        self.assertEqual(position["pageConfidence"], "text")

    def test_unlimited_ocr_adapter_exposes_layout_blocks_for_frontend_mapping(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        raw = (
            "<|det|>title [10, 20, 300, 60]<|/det|>Unlimited OCR Works "
            "<|det|>text [20, 100, 900, 180]<|/det|>Body text."
        )
        response = adapter.build_adapter_response(raw, 1, 0, {"backend": "test"})
        page = response["layoutParsingResults"][0]

        self.assertEqual(page["parser"], "unlimited-ocr")
        self.assertEqual(page["width"], 1000)
        self.assertEqual(page["height"], 1000)
        self.assertEqual(page["parsing_res_list"][0]["block_label"], "title")
        self.assertEqual(page["parsing_res_list"][0]["block_bbox"], [10.0, 20.0, 300.0, 60.0])

    def test_unlimited_ocr_image_crop_uses_independent_normalized_axes(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        from PIL import Image

        box = adapter.scaled_crop_box([100, 200, 500, 600], Image.new("RGB", (2000, 3000)))

        self.assertEqual(box, (184, 576, 1016, 1824))

    def test_ovisocr2_visual_regions_are_cropped_and_rewritten(self):
        adapter = importlib.import_module("ovisocr2_adapter")
        from PIL import Image

        markdown, images, blocks = adapter.crop_visual_regions(
            'Before\n\n<img src="images/bbox_100_200_500_600.jpg" />\n\nAfter',
            Image.new("RGB", (2000, 3000), "white"),
            0,
        )

        self.assertIn("![image](ocr_images/ovisocr2_p1_image_1.jpg)", markdown)
        self.assertEqual(list(images), ["ocr_images/ovisocr2_p1_image_1.jpg"])
        self.assertEqual(blocks[0]["block_bbox"], [100, 200, 500, 600])

    def test_ovisocr2_mlx_backend_dispatches_to_native_parser(self):
        adapter = importlib.import_module("ovisocr2_adapter")
        sentinel = object()
        with (
            patch.object(adapter, "BACKEND", "mlx"),
            patch.object(adapter, "MlxOvisOCR2Parser", return_value=sentinel) as parser,
        ):
            self.assertIs(adapter.create_parser(), sentinel)
        parser.assert_called_once_with(adapter.MODEL_NAME)

    def test_unlimited_ocr_streaming_markdown_can_include_images_once(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        from PIL import Image

        image_buffer = io.BytesIO()
        Image.new("RGB", (1000, 1000), "white").save(image_buffer, format="PNG")
        raw = (
            "<|det|>image [100, 100, 500, 500]<|/det|>"
            "<|det|>image_caption [100, 520, 500, 560]<|/det|>Figure 1. Caption."
        )

        markdown, images = adapter.render_streaming_markdown(raw, [image_buffer.getvalue()])
        sent_images = {}

        self.assertIn("![image](ocr_images/unlimited_p1_image_1.png)", markdown)
        self.assertIn("ocr_images/unlimited_p1_image_1.png", images)
        self.assertEqual(adapter.unsent_images(images, sent_images), images)
        self.assertEqual(adapter.unsent_images(images, sent_images), {})

    def test_unlimited_ocr_sglang_payload_reserves_context_for_input(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")

        payload = adapter.build_sglang_payload([b"not-real-image"], 1)

        self.assertEqual(payload["images_config"]["backend"], "sglang")
        self.assertLess(payload["max_tokens"], adapter.MAX_TOKENS)
        self.assertIn("custom_logit_processor", payload)
        self.assertEqual(payload["custom_params"]["ngram_size"], adapter.NO_REPEAT_NGRAM_SIZE)

    def test_unlimited_ocr_sglang_context_error_can_reduce_max_tokens(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        payload = {"max_tokens": 32768, "images_config": {"backend": "sglang"}}
        error_body = (
            "Requested token count exceeds the model's maximum context length of 32768 tokens. "
            "You requested a total of 35505 tokens: 2737 tokens from the input messages "
            "and 32768 tokens for the completion."
        )

        adjusted = adapter.adjust_sglang_payload_for_context_error(payload, error_body)

        self.assertIsNotNone(adjusted)
        self.assertEqual(adjusted["max_tokens"], 32768 - 2737 - adapter.SGLANG_CONTEXT_TOKEN_RESERVE)
        self.assertEqual(adjusted["images_config"]["max_tokens_adjusted_from"], 32768)

    def test_unlimited_ocr_repetition_guard_flags_degenerate_output(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        repeated = " ".join(["attention weight normalization"] * 20)

        self.assertEqual(adapter.detect_degenerate_repetition(repeated), "attention weight normalization")

    def test_unlimited_ocr_repetition_guard_flags_dense_numbered_loop(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        repeated = " ".join(f"attention weight normalization {index}" for index in range(20))

        self.assertEqual(adapter.detect_degenerate_repetition(repeated), "attention weight normalization")

    def test_unlimited_ocr_repetition_guard_allows_reference_arxiv_phrase(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        references = " ".join(
            (
                f"[{index}] A. Author, B. Researcher, and C. Writer. "
                f"A useful method for document parsing and visual models. "
                f"arXiv preprint arXiv:{2400 + index}.01234, 2025."
            )
            for index in range(20)
        )

        self.assertIsNone(adapter.detect_degenerate_repetition(references))

    def test_unlimited_ocr_extracts_layout_from_transformers_stdout(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        stdout = (
            "INFO:     127.0.0.1:123 - \"GET /health HTTP/1.1\" 200 OK\n"
            "image: 100%|##########| 1/1 [00:00<00:00, 10it/s]\n"
            "<|det|>title [10, 20, 30, 40]<|/det|>Title\n"
            "<|det|>image [40, 50, 80, 100]<|/det|>\n"
            "===============save results:===============\n"
        )
        extracted = adapter.extract_layout_text_from_transformers_stdout(stdout)

        self.assertIn("<|det|>title", extracted)
        self.assertIn("<|det|>image", extracted)
        self.assertNotIn("GET /health", extracted)
        self.assertNotIn("save results", extracted)

    def test_unlimited_ocr_endpoint_is_disabled_by_default(self):
        response = self.client.post(
            "/api/unlimited-ocr",
            json={"image": "AA==", "fileType": 1},
        )
        self.assertEqual(response.status_code, 404)

    def test_task_source_is_stored_outside_task_json_and_page_ranges_can_be_read(self):
        writer = PdfWriter()
        for _ in range(3):
            writer.add_blank_page(width=72, height=72)
        pdf_buffer = io.BytesIO()
        writer.write(pdf_buffer)
        pdf_bytes = pdf_buffer.getvalue()

        upload_response = self.client.post(
            "/api/tasks/task_src/source",
            files={"file": ("source.pdf", pdf_bytes, "application/pdf")},
        )
        self.assertEqual(upload_response.status_code, 200)
        self.assertEqual(upload_response.json()["url"], "/api/tasks/task_src/source")

        page_response = self.client.get("/api/tasks/task_src/source/pages?start_page=2&end_page=3")
        self.assertEqual(page_response.status_code, 200)
        subset = PdfReader(io.BytesIO(page_response.content))
        self.assertEqual(len(subset.pages), 2)

    def test_task_save_strips_heavy_fields_when_external_source_exists(self):
        self.client.post(
            "/api/tasks/task_big/source",
            files={"file": ("source.pdf", b"%PDF-1.4\n", "application/pdf")},
        )
        task = {
            "id": "task_big",
            "name": "big.pdf",
            "sourceKind": "pdf",
            "sourceUrl": "/api/tasks/task_big/source",
            "sourceDataUrl": "data:application/pdf;base64," + ("x" * 1000),
            "batches": [
                {
                    "id": "b1",
                    "status": "pending",
                    "pageCount": 20,
                    "payloadDataUrl": "data:application/pdf;base64," + ("y" * 1000),
                }
            ],
        }

        response = self.client.put("/api/tasks/task_big", json=task)
        self.assertEqual(response.status_code, 200)

        detail = self.client.get("/api/tasks/task_big").json()
        self.assertEqual(detail["sourceUrl"], "/api/tasks/task_big/source")
        self.assertNotIn("sourceDataUrl", detail)
        self.assertNotIn("payloadDataUrl", detail["batches"][0])

    def test_task_save_splits_results_into_sidecar_and_preserves_them_on_metadata_save(self):
        task = {
            "id": "task_side",
            "name": "sidecar.pdf",
            "sourceKind": "pdf",
            "status": "processing",
            "pageCount": 1,
            "batches": [
                {"id": "b1", "status": "completed", "pageCount": 1, "markdown": "Batch text"}
            ],
            "markdown": "# Heavy Markdown",
            "images": {"ocr_images/a.jpg": "base64-image"},
            "ocrResults": [{"markdown": {"text": "# Heavy Markdown"}}],
        }

        response = self.client.put("/api/tasks/task_side", json=task)
        self.assertEqual(response.status_code, 200)

        task_path = Path(self.temp_dir.name) / "task_side" / "task.json"
        result_path = Path(self.temp_dir.name) / "task_side" / "result.json"
        stored = json.loads(task_path.read_text(encoding="utf-8"))
        self.assertNotIn("markdown", stored)
        self.assertNotIn("images", stored)
        self.assertNotIn("ocrResults", stored)
        self.assertTrue(result_path.exists())

        metadata_only = {
            "id": "task_side",
            "name": "sidecar.pdf",
            "sourceKind": "pdf",
            "status": "completed",
            "pageCount": 1,
            "batches": [{"id": "b1", "status": "completed", "pageCount": 1}],
            "_preserveResult": True,
        }
        response = self.client.put("/api/tasks/task_side", json=metadata_only)
        self.assertEqual(response.status_code, 200)

        detail = self.client.get("/api/tasks/task_side").json()
        self.assertEqual(detail["markdown"], "# Heavy Markdown")
        self.assertEqual(detail["images"], {"ocr_images/a.jpg": "base64-image"})
        self.assertEqual(detail["ocrResults"], [{"markdown": {"text": "# Heavy Markdown"}}])
        self.assertEqual(detail["batches"][0]["markdown"], "Batch text")

    def test_batch_markdown_only_task_is_marked_as_having_markdown(self):
        task = {
            "id": "task_batch_markdown",
            "name": "batch-only.pdf",
            "batches": [{"id": "b1", "status": "completed", "pageCount": 1, "markdown": "Batch text"}],
        }

        response = self.client.put("/api/tasks/task_batch_markdown", json=task)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["task"]["hasMarkdown"])

    def test_clear_tasks_only_removes_task_directories(self):
        task_dir = Path(self.temp_dir.name) / "task_keep"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "task.json").write_text('{"id":"task_keep"}', encoding="utf-8")
        keep_file = Path(self.temp_dir.name) / "keep.txt"
        keep_file.write_text("keep", encoding="utf-8")
        keep_dir = Path(self.temp_dir.name) / "docs"
        keep_dir.mkdir(exist_ok=True)

        response = self.client.delete("/api/tasks")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(task_dir.exists())
        self.assertTrue(keep_file.exists())
        self.assertTrue(keep_dir.exists())

    def test_model_runtime_switch_is_rejected_while_ocr_is_active(self):
        self.server.ocr_active_count = 1
        try:
            with patch.object(self.server, "API_TOKEN", "t"), \
                 patch.object(self.server, "model_control_available", return_value=True):
                response = self.client.post(
                    "/api/model-runtime/switch",
                    json={"modelId": "pp-ocrv6"},
                    headers={"Authorization": "Bearer t"},
                )
            self.assertEqual(response.status_code, 409)
        finally:
            self.server.ocr_active_count = 0

    def test_ocr_request_is_rejected_during_model_switch(self):
        self.server.set_model_runtime_operation("switching", "Switching to pp-ocrv6", "pp-ocrv6")
        try:
            response = self.client.post(
                "/api/paddleocr-vl-1.6",
                json={"image": "AA==", "fileType": 1},
            )
            self.assertEqual(response.status_code, 409)
        finally:
            self.server.set_model_runtime_operation("idle", "", "paddleocr-vl-1.6")

    def test_host_config_requests_gpu_by_default(self):
        cfg = self.server.host_config(network_name="net", binds=[])
        self.assertEqual(len(cfg["DeviceRequests"]), 1)
        self.assertEqual(cfg["DeviceRequests"][0]["Driver"], "nvidia")

    def test_host_config_can_disable_gpu_for_cpu_services(self):
        cfg = self.server.host_config(network_name="net", binds=[], use_gpu=False)
        self.assertEqual(cfg["DeviceRequests"], [])

    def test_parse_ppocr_response_defaults_to_ppocrv6_parser(self):
        sample = {"result": {"ocrResults": [{"prunedResult": {
            "rec_texts": ["hi"], "rec_scores": [0.9], "rec_boxes": [[0, 0, 1, 1]],
            "rec_polys": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
        }}]}}
        page = self.server.parse_ppocr_response(sample)["layoutParsingResults"][0]
        self.assertEqual(page["parser"], "pp-ocrv6")

    def test_parse_ppocr_response_accepts_custom_model_and_parser(self):
        sample = {"result": {"ocrResults": [{"prunedResult": {
            "rec_texts": ["hi"], "rec_scores": [0.9], "rec_boxes": [[0, 0, 1, 1]],
            "rec_polys": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
        }}]}}
        page = self.server.parse_ppocr_response(
            sample, model_name="PP-OCRv6_medium_rapid", parser_name="pp-ocrv6-rapid"
        )["layoutParsingResults"][0]
        self.assertEqual(page["parser"], "pp-ocrv6-rapid")
        self.assertEqual(page["model"], "PP-OCRv6_medium_rapid")

    def test_model_catalog_includes_rapid_when_enabled(self):
        with (
            patch.object(self.server, "MODEL_CATALOG_ENV", "pp-ocrv6-rapid"),
            patch.object(self.server, "MODEL_CATALOG_IDS", ["pp-ocrv6-rapid"]),
            patch.object(
                self.server,
                "MODEL_RUNTIME_CONFIG",
                {"pp-ocrv6-rapid": {"containers": ["rapidocr-api"], "health_url": "http://localhost:8085/health"}},
            ),
        ):
            response = self.client.get("/api/models")
        ids = [m["id"] for m in response.json()["data"]]
        self.assertIn("pp-ocrv6-rapid", ids)
        rapid = next(m for m in response.json()["data"] if m["id"] == "pp-ocrv6-rapid")
        self.assertEqual(rapid["kind"], "text_ocr")
        self.assertEqual(rapid["endpoint"], "/api/pp-ocrv6-rapid")

    def test_rapidocr_deploy_is_cpu_only(self):
        self.assertEqual(self.server.services_for_model_deploy("pp-ocrv6-rapid"), ["rapidocr-api"])
        self.assertEqual(self.server.docker_image_name_for("rapidocr-api"), "pandocr-rapidocr:latest")
        payload = self.server.container_payload_for("rapidocr-api", host_root="/repo", network_name="net")
        self.assertEqual(payload["Cmd"][:2], ["uvicorn", "rapidocr_adapter:app"])
        self.assertEqual(payload["HostConfig"]["DeviceRequests"], [])
        env = " ".join(payload["Env"])
        self.assertIn("RAPIDOCR_MODEL_TIER", env)
        bindings = payload["HostConfig"]["PortBindings"]["8080/tcp"][0]
        self.assertEqual(bindings["HostPort"], self.server.RAPIDOCR_API_PORT)

    def test_rapid_ocr_route_proxies_and_tags_parser(self):
        upstream = {"result": {"ocrResults": [{"prunedResult": {
            "rec_texts": ["x"], "rec_scores": [0.5], "rec_boxes": [[0, 0, 1, 1]],
            "rec_polys": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
        }}]}}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, **kwargs):
                resp = Mock()
                resp.status_code = 200
                resp.json.return_value = upstream
                resp.text = ""
                return resp

        with (
            patch.object(self.server.httpx, "AsyncClient", FakeAsyncClient),
            patch.object(self.server, "acquire_ocr_slot", new=AsyncMock()),
            patch.object(self.server, "release_ocr_slot", new=AsyncMock()),
        ):
            response = self.client.post(
                "/api/pp-ocrv6-rapid", json={"image": "AA==", "fileType": 1}
            )
        self.assertEqual(response.status_code, 200)
        page = response.json()["layoutParsingResults"][0]
        self.assertEqual(page["parser"], "pp-ocrv6-rapid")
        self.assertEqual(page["model"], self.server.RAPIDOCR_MODEL_NAME)

    def test_privileged_endpoints_blocked_when_token_empty(self):
        # API_TOKEN is "" in the test environment (setUpClass).
        response = self.client.post("/api/model-runtime/switch", json={"modelId": "pp-ocrv6"})
        self.assertEqual(response.status_code, 403)

    def test_privileged_endpoints_reject_missing_token(self):
        original = self.server.API_TOKEN
        self.server.API_TOKEN = "secret"
        try:
            response = self.client.post("/api/model-runtime/switch", json={"modelId": "pp-ocrv6"})
            self.assertEqual(response.status_code, 401)
        finally:
            self.server.API_TOKEN = original

    def test_password_gate_blocks_api_without_session(self):
        self.server.AUTH_PASSWORD = "letmein"
        self.server.AUTH_SESSIONS.clear()
        try:
            response = self.client.get("/api/tasks")
            self.assertEqual(response.status_code, 401)

            login = self.client.post("/api/auth/login", json={"password": "wrong"})
            self.assertEqual(login.status_code, 401)

            login = self.client.post("/api/auth/login", json={"password": "letmein"})
            self.assertEqual(login.status_code, 200)

            response = self.client.get("/api/tasks")
            self.assertEqual(response.status_code, 200)
        finally:
            self.server.AUTH_PASSWORD = ""
            self.server.AUTH_SESSIONS.clear()

    def test_password_gate_inactive_by_default(self):
        self.server.AUTH_PASSWORD = ""
        response = self.client.get("/api/tasks")
        self.assertEqual(response.status_code, 200)

    def test_storage_report_and_cleanup_by_count(self):
        for index in range(3):
            task = {
                "id": f"stor{index}0abcde", "name": "x.pdf", "sourceKind": "pdf",
                "modelId": "pp-ocrv6-rapid", "modelName": "Rapid", "size": 1,
                "createdAt": index, "updatedAt": index, "status": "completed",
                "ocrResults": [{"prunedResult": {"rec_texts": ["hi"], "rec_boxes": [[1, 1, 9, 9]]}}],
            }
            self.client.put(f"/api/tasks/stor{index}0abcde", json=task)

        # The test store is shared across tests in this class, so assert
        # relative counts instead of absolute values.
        report = self.client.get("/api/tasks/storage")
        self.assertEqual(report.status_code, 200)
        before_count = report.json()["taskCount"]
        self.assertGreaterEqual(before_count, 3)

        cleanup = self.client.post("/api/tasks/cleanup", json={"keepCount": 1})
        self.assertEqual(cleanup.status_code, 200)
        self.assertEqual(cleanup.json()["deleted"], before_count - 1)

        remaining = self.client.get("/api/tasks")
        self.assertEqual(len(remaining.json()["tasks"]), 1)

    def test_cleanup_requires_parameters(self):
        response = self.client.post("/api/tasks/cleanup", json={})
        self.assertEqual(response.status_code, 400)

    def _put_queued_task(self, task_id, model_id="pp-ocrv6-rapid", batch_count=2):
        batches = [
            {
                "id": f"{task_id}-b{index}",
                "label": f"第 {index + 1} 页",
                "fileType": 1,
                "startPage": index + 1,
                "endPage": index + 1,
                "pageCount": 1,
                "status": "pending",
            }
            for index in range(batch_count)
        ]
        task = {
            "id": task_id, "name": "x.png", "sourceKind": "image",
            "modelId": model_id, "modelName": "Rapid", "size": 10,
            "createdAt": 1, "updatedAt": 1, "status": "pending",
            "pageCount": batch_count, "batches": batches,
            "markdown": "", "images": {}, "ocrResults": [],
        }
        self.client.put(f"/api/tasks/{task_id}", json=task)
        self.client.post(
            f"/api/tasks/{task_id}/source",
            files={"file": ("x.png", b"\x89PNG-fake-bytes", "image/png")},
        )

    @staticmethod
    def _fake_ocr_result(page_text):
        return {
            "markdown": page_text,
            "images": {},
            "layoutParsingResults": [
                {
                    "parser": "pp-ocrv6-rapid",
                    "page_index": 0,
                    "markdown": {"text": page_text, "images": {}},
                    "ocrLines": [{"text": page_text, "score": 0.9}],
                }
            ],
        }

    def _poll_status(self, task_id, expected_states, timeout=5.0):
        import time as time_module
        deadline = time_module.time() + timeout
        while time_module.time() < deadline:
            response = self.client.get(f"/api/tasks/{task_id}/status")
            if response.status_code == 200:
                status = response.json()
                if status["state"] in expected_states:
                    return status
            time_module.sleep(0.05)
        self.fail(f"Task {task_id} never reached {expected_states}")

    def test_task_queue_processes_batches_and_persists_results(self):
        async def fake_runner(ocr_request, raw):
            return self._fake_ocr_result("hello")

        self._put_queued_task("taskq10001")
        with patch.object(self.server, "task_model_runner", return_value=fake_runner):
            enqueue = self.client.post("/api/tasks/taskq10001/process")
            self.assertEqual(enqueue.status_code, 200)
            self.assertTrue(enqueue.json()["queued"])

            status = self._poll_status("taskq10001", {"completed"})
            self.assertEqual(status["batchesDone"], 2)
            self.assertEqual(status["batchesTotal"], 2)

        detail = self.client.get("/api/tasks/taskq10001").json()
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(len(detail["ocrResults"]), 2)
        self.assertEqual(detail["ocrResults"][0]["sourcePage"], 1)
        self.assertEqual(detail["ocrResults"][1]["sourcePage"], 2)
        self.assertEqual(detail["ocrResults"][0]["batchId"], "taskq10001-b0")
        self.assertEqual(detail["markdown"], "hello\n\nhello")

    def test_task_queue_cancel_between_batches(self):
        import asyncio as asyncio_module

        started = asyncio_module.Event()

        async def slow_runner(ocr_request, raw):
            started.set()
            await asyncio_module.sleep(0.25)
            return self._fake_ocr_result("page")

        self._put_queued_task("taskq20002")
        with patch.object(self.server, "task_model_runner", return_value=slow_runner):
            self.client.post("/api/tasks/taskq20002/process")
            status = self._poll_status("taskq20002", {"processing"})
            self.assertTrue(status["batchesTotal"] >= 1)

            cancel = self.client.post("/api/tasks/taskq20002/cancel")
            self.assertTrue(cancel.json()["ok"])

            status = self._poll_status("taskq20002", {"cancelled", "completed"})
            self.assertEqual(status["state"], "cancelled")

        detail = self.client.get("/api/tasks/taskq20002").json()
        # Cancellation lands between batches: whatever hadn't started stays
        # pending (resumable); nothing is left mid-flight.
        processing = [b for b in detail["batches"] if b["status"] == "processing"]
        self.assertEqual(len(processing), 0)
        self.assertEqual(detail["status"], "pending")

    def test_task_queue_rejects_unsupported_model(self):
        self._put_queued_task("taskq30003", model_id="paddleocr-vl-1.6")
        response = self.client.post("/api/tasks/taskq30003/process")
        self.assertEqual(response.status_code, 400)

    def test_engine_settings_proxy(self):
        calls = []

        class FakeSettingsClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def _respond(self, payload):
                return Mock(status_code=200, json=Mock(return_value=payload))

            async def get(self, url):
                calls.append(("GET", url, None))
                return self._respond({"tier": "small", "det_lang": "ch", "rec_lang": "ch"})

            async def put(self, url, json=None):
                calls.append(("PUT", url, json))
                return self._respond({"tier": "medium", "det_lang": "ch", "rec_lang": "ch"})

        with patch.object(self.server, "ENABLE_RAPIDOCR", True), \
             patch.object(self.server.httpx, "AsyncClient", FakeSettingsClient):
            get_response = self.client.get("/api/engine-settings")
            self.assertEqual(get_response.status_code, 200)
            self.assertEqual(get_response.json()["tier"], "small")

            put_response = self.client.put("/api/engine-settings", json={"tier": "medium"})
            self.assertEqual(put_response.status_code, 200)
            self.assertEqual(put_response.json()["tier"], "medium")

        self.assertEqual(calls[0][0], "GET")
        self.assertTrue(calls[0][1].endswith("/engine/settings"))
        self.assertEqual(calls[1][2], {"tier": "medium"})

    def test_engine_settings_returns_404_when_rapidocr_disabled(self):
        with patch.object(self.server, "ENABLE_RAPIDOCR", False):
            response = self.client.get("/api/engine-settings")
            self.assertEqual(response.status_code, 404)

    def test_queue_pause_blocks_pickup_and_resume_completes(self):
        async def fast_runner(ocr_request, raw):
            return self._fake_ocr_result("page")

        self._put_queued_task("taskq90010")
        with patch.object(self.server, "task_model_runner", return_value=fast_runner):
            pause = self.client.post("/api/queue/pause", json={"enabled": True})
            self.assertTrue(pause.json()["paused"])

            self.client.post("/api/tasks/taskq90010/process")
            # Paused: the job must stay queued (no pickup) for a clear window.
            import time as time_module
            time_module.sleep(0.6)
            state = self.client.get("/api/tasks/taskq90010/status").json()
            self.assertEqual(state["state"], "queued")

            resume = self.client.post("/api/queue/pause", json={"enabled": False})
            self.assertFalse(resume.json()["paused"])
            status = self._poll_status("taskq90010", {"completed"})
            self.assertEqual(status["state"], "completed")

        queue_state = self.client.get("/api/queue/state").json()
        self.assertFalse(queue_state["paused"])
        self.assertEqual(queue_state["queued"], 0)
        self.assertEqual(queue_state["processing"], 0)

    def test_ephemeral_ui_fields_never_persist_and_heal_on_read(self):
        task = {
            "id": "ephem0012345", "name": "x.png", "sourceKind": "image",
            "modelId": "pp-ocrv6-rapid", "modelName": "Rapid", "size": 10,
            "createdAt": 1, "updatedAt": 1, "status": "error",
            "pageCount": 1, "batches": [], "markdown": "", "images": {}, "ocrResults": [],
            # Simulate the historic leak: session-only UI state stored on disk.
            "jobState": "queued", "jobProgress": {"done": 0, "total": 1},
            "jobEta": 12, "queueAhead": 0,
        }
        self.client.put("/api/tasks/ephem0012345", json=task)

        # Write path: stored task.json must not contain the ephemeral keys.
        stored = self.server.read_task_file(self.server.task_file_path("ephem0012345"))
        for key in ("jobState", "jobProgress", "jobEta", "queueAhead"):
            self.assertNotIn(key, stored)

        # Read path: even a contaminated document self-heals on hydrate.
        contaminated = dict(task)
        contaminated["jobState"] = "queued"
        healed = self.server.hydrate_task_detail("ephem0012345", contaminated)
        self.assertNotIn("jobState", healed)

    def test_startup_recovery_reenqueues_interrupted_tasks(self):
        async def fake_runner(ocr_request, raw):
            return self._fake_ocr_result("page")

        # Simulate a task left mid-flight by a restart: status processing,
        # first batch stuck in 'processing', second still pending.
        self._put_queued_task("taskq80009")
        stored = self.server.read_task_file(self.server.task_file_path("taskq80009"))
        stored["status"] = "processing"
        stored["batches"][0]["status"] = "processing"
        self.server.write_task_bundle("taskq80009", stored)

        with patch.object(self.server, "task_model_runner", return_value=fake_runner):
            recovered = self.server.recover_interrupted_jobs()
            self.assertEqual(recovered, 1)
            status = self._poll_status("taskq80009", {"completed"})
            self.assertEqual(status["batchesDone"], 2)

        detail = self.client.get("/api/tasks/taskq80009").json()
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(len(detail["ocrResults"]), 2)

    def test_task_queue_batch_timeout_frees_the_queue(self):
        # Deterministic: the FIRST runner call sticks (task A's first batch),
        # every later call returns instantly.
        calls = {"count": 0}

        async def runner(ocr_request, raw):
            calls["count"] += 1
            if calls["count"] == 1:
                await asyncio.sleep(5)
            return self._fake_ocr_result("page")

        self._put_queued_task("taskq60006")
        self._put_queued_task("taskq60007")
        with patch.object(self.server, "JOB_BATCH_TIMEOUT", 0.3), \
             patch.object(self.server, "task_model_runner", lambda mid: runner):
            self.client.post("/api/tasks/taskq60006/process")
            self.client.post("/api/tasks/taskq60007/process")
            status_a = self._poll_status("taskq60006", {"error"}, timeout=10)
            self.assertIn("timed out", status_a["error"])
            status_b = self._poll_status("taskq60007", {"completed", "error"}, timeout=10)
            self.assertEqual(status_b["state"], "completed")

    def test_task_queue_cancel_preempts_and_worker_survives(self):
        gate = {"open": False}

        async def gated_runner(ocr_request, raw):
            while not gate["open"]:
                await asyncio.sleep(0.02)
            return self._fake_ocr_result("page")

        async def fast_runner(ocr_request, raw):
            return self._fake_ocr_result("fast")

        runners = {"current": gated_runner}
        self._put_queued_task("taskq70008")
        self._put_queued_task("taskq70009")
        with patch.object(self.server, "task_model_runner", lambda mid: runners["current"]):
            self.client.post("/api/tasks/taskq70008/process")
            self._poll_status("taskq70008", {"processing"})
            self.client.post("/api/tasks/taskq70009/process")

            # Cancel the mid-batch head job — must land within ~1s, not after
            # the gate opens.
            import time as time_module
            cancel_started = time_module.time()
            cancel = self.client.post("/api/tasks/taskq70008/cancel")
            self.assertTrue(cancel.json()["ok"])
            status_a = self._poll_status("taskq70008", {"cancelled"}, timeout=3)
            self.assertLess(time_module.time() - cancel_started, 3)

            detail = self.client.get("/api/tasks/taskq70008").json()
            self.assertEqual(detail["status"], "pending")
            self.assertFalse([b for b in detail["batches"] if b["status"] == "processing"])

            # The worker must still be alive: a fresh fast job completes.
            gate["open"] = True
            runners["current"] = fast_runner
            status_b = self._poll_status("taskq70009", {"completed"}, timeout=10)
            self.assertEqual(status_b["state"], "completed")

    def test_task_status_reconciles_stale_queued_job_against_disk(self):
        async def fake_runner(ocr_request, raw):
            return self._fake_ocr_result("page")

        self._put_queued_task("taskq50005")
        with patch.object(self.server, "task_model_runner", return_value=fake_runner):
            self.client.post("/api/tasks/taskq50005/process")
            self._poll_status("taskq50005", {"completed"})

        # Simulate a stale in-memory queue claim (e.g. restart race): the disk
        # says completed, TASK_JOBS says queued — the status endpoint must side
        # with the disk.
        self.server.TASK_JOBS["taskq50005"]["state"] = "queued"
        status = self.client.get("/api/tasks/taskq50005/status").json()
        self.assertEqual(status["state"], "completed")
        self.assertEqual(self.server.TASK_JOBS["taskq50005"]["state"], "completed")

    def test_task_queue_process_is_idempotent(self):
        gate = {"open": False}

        async def gated_runner(ocr_request, raw):
            while not gate["open"]:
                await asyncio.sleep(0.02)
            return self._fake_ocr_result("page")

        self._put_queued_task("taskq40004")
        with patch.object(self.server, "task_model_runner", return_value=gated_runner):
            first = self.client.post("/api/tasks/taskq40004/process")
            second = self.client.post("/api/tasks/taskq40004/process")
            self.assertTrue(first.json()["queued"])
            self.assertFalse(second.json()["queued"])
            gate["open"] = True
            self._poll_status("taskq40004", {"completed"})
        self.server.TASK_JOBS.pop("taskq40004", None)

    def test_build_relaid_pdf_positions_text_by_boxes(self):
        ocr_results = [{
            "prunedResult": {
                "rec_texts": ["你好世界", "test"],
                "rec_boxes": [[10, 10, 200, 40], [10, 50, 200, 80]],
            },
        }]
        pdf_bytes = self.server.build_relaid_pdf("relaid01", ocr_results)
        self.assertGreater(len(pdf_bytes), 100)
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        self.assertEqual(len(doc), 1)
        text = doc[0].get_text()
        self.assertIn("你好世界", text)
        self.assertIn("test", text)
        doc.close()

    def test_export_task_returns_reflowed_pdf(self):
        task = {
            "id": "export1", "name": "x.pdf", "sourceKind": "pdf",
            "modelId": "pp-ocrv6-rapid", "modelName": "Rapid", "size": 1,
            "createdAt": 1, "updatedAt": 1, "status": "completed",
            "ocrResults": [{"prunedResult": {"rec_texts": ["hi"], "rec_boxes": [[10, 10, 50, 30]]}}],
        }
        self.client.put("/api/tasks/export1", json=task)
        response = self.client.get("/api/tasks/export1/export?format=pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertGreater(len(response.content), 100)

    def test_export_task_returns_searchable_pdf(self):
        import fitz
        from PIL import Image as PILImage

        buffer = io.BytesIO()
        PILImage.new("RGB", (120, 80), color=(240, 240, 240)).save(buffer, format="JPEG")
        page_image = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        task = {
            "id": "export2", "name": "x.pdf", "sourceKind": "pdf",
            "modelId": "pp-ocrv6-rapid", "modelName": "Rapid", "size": 1,
            "createdAt": 1, "updatedAt": 1, "status": "completed",
            "ocrResults": [{
                "pageImage": page_image,
                "prunedResult": {"rec_texts": ["发票号码"], "rec_boxes": [[10, 10, 100, 30]]},
            }],
        }
        self.client.put("/api/tasks/export2", json=task)
        response = self.client.get("/api/tasks/export2/export?format=searchable-pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")

        doc = fitz.open(stream=response.content, filetype="pdf")
        self.assertEqual(len(doc), 1)
        self.assertGreaterEqual(len(doc[0].get_images()), 1)
        self.assertIn("发票号码", doc[0].get_text())
        doc.close()

    def test_searchable_pdf_requires_page_images(self):
        task = {
            "id": "export3", "name": "x.pdf", "sourceKind": "pdf",
            "modelId": "pp-ocrv6-rapid", "modelName": "Rapid", "size": 1,
            "createdAt": 1, "updatedAt": 1, "status": "completed",
            "ocrResults": [{"prunedResult": {"rec_texts": ["hi"], "rec_boxes": [[1, 1, 9, 9]]}}],
        }
        self.client.put("/api/tasks/export3", json=task)
        response = self.client.get("/api/tasks/export3/export?format=searchable-pdf")
        self.assertEqual(response.status_code, 400)

    def test_export_task_returns_docx(self):
        from docx import Document

        task = {
            "id": "export4", "name": "x.pdf", "sourceKind": "pdf",
            "modelId": "pp-ocrv6-rapid", "modelName": "Rapid", "size": 1,
            "createdAt": 1, "updatedAt": 1, "status": "completed",
            "ocrResults": [
                {"ocrLines": [{"text": "第一页标题"}, {"text": "第一页正文"}]},
                {"ocrLines": [{"text": "第二页内容"}]},
            ],
        }
        self.client.put("/api/tasks/export4", json=task)
        response = self.client.get("/api/tasks/export4/export?format=docx")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            response.headers["content-type"],
        )

        document = Document(io.BytesIO(response.content))
        texts = [p.text for p in document.paragraphs if p.text.strip()]
        self.assertEqual(texts, ["第一页标题", "第一页正文", "第二页内容"])

    @staticmethod
    def _sfnt_bytes(num_tables: int, tags: list) -> bytes:
        header = b"\x00\x01\x00\x00" + num_tables.to_bytes(2, "big") + b"\x00" * 6
        records = b"".join(tag + b"\x00" * 12 for tag in tags)
        return header + records

    def _write_font_file(self, name: str, data: bytes) -> str:
        path = Path(self.temp_dir.name) / name
        path.write_bytes(data)
        return str(path)

    def test_font_is_glyf_detects_outline_flavor(self):
        font_is_glyf = self.server.font_is_glyf

        # Plain sfnt with a glyf table.
        glyf_path = self._write_font_file("plain_glyf", self._sfnt_bytes(1, [b"glyf"]))
        self.assertTrue(font_is_glyf(glyf_path))

        # OTTO/CFF flavor.
        cff_path = self._write_font_file("cff", self._sfnt_bytes(2, [b"CFF ", b"maxp"]))
        self.assertFalse(font_is_glyf(cff_path))

        # Collection whose first face is glyf: ttcf + version + numFonts +
        # one offset (16) + the inner sfnt at byte 16.
        inner = self._sfnt_bytes(1, [b"glyf"])
        ttc = b"ttcf" + b"\x00" * 4 + (1).to_bytes(4, "big") + (16).to_bytes(4, "big") + inner
        ttc_path = self._write_font_file("coll.ttc", ttc)
        self.assertTrue(font_is_glyf(ttc_path))

        # Collection whose first face is CFF.
        inner_cff = self._sfnt_bytes(1, [b"CFF "])
        ttc_cff = b"ttcf" + b"\x00" * 4 + (1).to_bytes(4, "big") + (16).to_bytes(4, "big") + inner_cff
        ttc_cff_path = self._write_font_file("coll_cff.ttc", ttc_cff)
        self.assertFalse(font_is_glyf(ttc_cff_path))

        self.assertFalse(font_is_glyf(str(Path(self.temp_dir.name) / "missing.ttf")))

    # --- Backend-owned task schema -------------------------------------------

    @staticmethod
    def _make_pdf_bytes(page_count: int) -> bytes:
        writer = PdfWriter()
        for _ in range(page_count):
            writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    def _create_task(self, content: bytes, filename: str, mime: str, **fields):
        return self.client.post(
            "/api/tasks",
            files={"file": (filename, content, mime)},
            data={key: str(value) if not isinstance(value, str) else value for key, value in fields.items()},
        )

    def test_create_task_plans_batches_and_persists_source(self):
        pdf = self._make_pdf_bytes(5)
        response = self._create_task(pdf, "doc.pdf", "application/pdf", modelId="pp-ocrv6", pdfBatchSize=2)
        self.assertEqual(response.status_code, 201)
        task = response.json()
        self.assertEqual(task["pageCount"], 5)
        self.assertEqual(task["pdfBatchSize"], 2)
        self.assertEqual([b["pageCount"] for b in task["batches"]], [2, 2, 1])
        self.assertEqual([b["startPage"] for b in task["batches"]], [1, 3, 5])
        self.assertEqual([b["endPage"] for b in task["batches"]], [2, 4, 5])
        self.assertTrue(all(b["status"] == "pending" for b in task["batches"]))
        self.assertEqual(task["modelId"], "pp-ocrv6")
        self.assertEqual(task["status"], "pending")
        task_dir = Path(self.temp_dir.name) / task["id"]
        self.assertEqual((task_dir / "source.bin").read_bytes(), pdf)
        self.assertIn(task["id"], task["sourceUrl"])

    def test_create_task_honors_selected_pages(self):
        pdf = self._make_pdf_bytes(6)
        response = self._create_task(
            pdf, "doc.pdf", "application/pdf",
            modelId="pp-ocrv6", selectedPages=json.dumps([2, 3, 5]), pdfBatchSize=2,
        )
        self.assertEqual(response.status_code, 201)
        task = response.json()
        # Contiguous run [2,3] chunks into one batch; [5] stands alone.
        self.assertEqual([(b["startPage"], b["endPage"]) for b in task["batches"]], [(2, 3), (5, 5)])
        self.assertEqual(task["selectedPages"], [2, 3, 5])

    def test_create_image_task_makes_single_batch(self):
        response = self._create_task(b"\x89PNG-fake", "x.png", "image/png", modelId="pp-ocrv6", sourceKind="image")
        self.assertEqual(response.status_code, 201)
        task = response.json()
        self.assertEqual(task["sourceKind"], "image")
        self.assertEqual(task["pageCount"], 1)
        self.assertEqual(len(task["batches"]), 1)
        self.assertEqual(task["batches"][0]["fileType"], 1)

    def test_create_task_rejects_unknown_model_and_bad_pages(self):
        pdf = self._make_pdf_bytes(2)
        bad_model = self._create_task(pdf, "doc.pdf", "application/pdf", modelId="nope")
        self.assertEqual(bad_model.status_code, 400)
        bad_pages = self._create_task(pdf, "doc.pdf", "application/pdf", modelId="pp-ocrv6", selectedPages="[9]")
        self.assertEqual(bad_pages.status_code, 400)

    def test_create_task_rejects_corrupt_pdf(self):
        response = self._create_task(b"%PDF-broken", "x.pdf", "application/pdf", modelId="pp-ocrv6")
        self.assertEqual(response.status_code, 400)

    def test_patch_replans_batch_size_and_keeps_completed_batches(self):
        pdf = self._make_pdf_bytes(4)
        created = self._create_task(pdf, "doc.pdf", "application/pdf", modelId="pp-ocrv6", pdfBatchSize=1).json()
        self.assertEqual(len(created["batches"]), 4)

        patched = self.client.patch(f"/api/tasks/{created['id']}", json={"pdfBatchSize": 2})
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(len(patched.json()["batches"]), 2)

        # Once a batch is completed, the plan is frozen (resume semantics).
        detail = self.client.get(f"/api/tasks/{created['id']}").json()
        detail["batches"][0]["status"] = "completed"
        self.client.put(f"/api/tasks/{created['id']}", json=detail)
        frozen = self.client.patch(f"/api/tasks/{created['id']}", json={"pdfBatchSize": 4})
        self.assertEqual(len(frozen.json()["batches"]), 2)

    def test_patch_updates_model_and_filters_parse_settings(self):
        created = self._create_task(self._make_pdf_bytes(1), "doc.pdf", "application/pdf", modelId="pp-ocrv6").json()
        response = self.client.patch(
            f"/api/tasks/{created['id']}",
            json={"modelId": "pp-ocrv6", "parseSettings": {"useChartRecognition": True, "bogus": 1}},
        )
        self.assertEqual(response.status_code, 200)
        task = response.json()
        self.assertEqual(task["modelId"], "pp-ocrv6")
        self.assertEqual(task["parseSettings"], {"useChartRecognition": True})

    def test_process_with_reset_replans_and_applies_settings(self):
        created = self._create_task(
            self._make_pdf_bytes(5), "doc.pdf", "application/pdf", modelId="pp-ocrv6", pdfBatchSize=1,
        ).json()

        async def fake_runner(ocr_request, raw):
            return self._fake_ocr_result("page")

        with patch.object(self.server, "task_model_runner", return_value=fake_runner):
            response = self.client.post(
                f"/api/tasks/{created['id']}/process",
                json={"resume": False, "pdfBatchSize": 5, "parseSettings": {"useChartRecognition": True}},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["queued"])
            self.assertEqual(response.json()["batchesTotal"], 1)
            self._poll_status(created["id"], {"completed"})

        detail = self.client.get(f"/api/tasks/{created['id']}").json()
        self.assertEqual(len(detail["batches"]), 1)
        self.assertEqual(detail["batches"][0]["pageCount"], 5)
        self.assertEqual(detail["parseSettings"], {"useChartRecognition": True})

    # --- Single text source (ppocr pages derive text from ocrLines) ---------

    def _fake_ppocr_runner(self, text: str):
        async def runner(ocr_request, raw):
            return {
                "markdown": f"stale snapshot {text}",
                "images": {},
                "layoutParsingResults": [{
                    "parser": "pp-ocrv6-rapid",
                    "page_index": 0,
                    "markdown": {"text": f"stale {text}", "images": {}},
                    "ocrLines": [{"text": text, "score": 0.9, "box": [0, 0, 10, 10]}],
                    "prunedResult": {
                        "rec_texts": [text],
                        "rec_scores": [0.9],
                        "rec_boxes": [[0, 0, 10, 10]],
                        "rec_polys": [[[0, 0], [10, 0], [10, 10], [0, 10]]],
                        "page_index": 0,
                    },
                }],
            }

        return runner

    def test_ppocr_results_store_single_text_source(self):
        created = self._create_task(b"\x89PNG-fake", "x.png", "image/png", modelId="pp-ocrv6", sourceKind="image").json()
        runner = self._fake_ppocr_runner("hello")
        with patch.object(self.server, "task_model_runner", return_value=runner):
            self.client.post(f"/api/tasks/{created['id']}/process")
            self._poll_status(created["id"], {"completed"})

        task_dir = Path(self.temp_dir.name) / created["id"]
        stored = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
        page = stored["ocrResults"][0]
        self.assertNotIn("rec_texts", page["prunedResult"])
        self.assertNotIn("markdown", page)
        self.assertNotIn("markdown", stored)
        self.assertNotIn("batchMarkdown", stored)
        stored_task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        self.assertNotIn("markdown", stored_task["batches"][0])

        detail = self.client.get(f"/api/tasks/{created['id']}").json()
        self.assertEqual(detail["markdown"], "hello")
        self.assertEqual(detail["ocrResults"][0]["prunedResult"]["rec_texts"], ["hello"])
        self.assertEqual(detail["ocrResults"][0]["markdown"]["text"], "hello")

    def test_ppocr_correction_round_trip_updates_derived_text(self):
        created = self._create_task(b"\x89PNG-fake", "x.png", "image/png", modelId="pp-ocrv6", sourceKind="image").json()
        runner = self._fake_ppocr_runner("hello")
        with patch.object(self.server, "task_model_runner", return_value=runner):
            self.client.post(f"/api/tasks/{created['id']}/process")
            self._poll_status(created["id"], {"completed"})

        # Correct the line the way the browser does: it PUTs the hydrated
        # form (ocrLines + derived rec_texts/markdown both updated).
        detail = self.client.get(f"/api/tasks/{created['id']}").json()
        detail["ocrResults"][0]["ocrLines"][0]["text"] = "fixed"
        detail["ocrResults"][0]["prunedResult"]["rec_texts"] = ["fixed"]
        detail["markdown"] = "fixed"
        put = self.client.put(f"/api/tasks/{created['id']}", json=detail)
        self.assertEqual(put.status_code, 200)

        task_dir = Path(self.temp_dir.name) / created["id"]
        stored = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
        self.assertNotIn("rec_texts", stored["ocrResults"][0]["prunedResult"])

        refreshed = self.client.get(f"/api/tasks/{created['id']}").json()
        self.assertEqual(refreshed["markdown"], "fixed")
        self.assertEqual(refreshed["ocrResults"][0]["prunedResult"]["rec_texts"], ["fixed"])

    # --- Page images stored as files ------------------------------------------

    @staticmethod
    def _jpeg_bytes() -> bytes:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (40, 30), (240, 240, 240)).save(buffer, format="JPEG")
        return buffer.getvalue()

    def test_page_images_persisted_as_files_and_served(self):
        jpeg = self._jpeg_bytes()
        data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        created = self._create_task(b"\x89PNG-fake", "x.png", "image/png", modelId="pp-ocrv6", sourceKind="image").json()

        async def runner(ocr_request, raw):
            return {
                "markdown": "",
                "images": {},
                "layoutParsingResults": [{
                    "parser": "pp-ocrv6-rapid",
                    "page_index": 0,
                    "pageImage": data_url,
                    "ocrLines": [{"text": "hi", "score": 0.9}],
                    "prunedResult": {
                        "rec_texts": ["hi"], "rec_scores": [0.9],
                        "rec_boxes": [[1, 1, 20, 10]], "rec_polys": [], "page_index": 0,
                    },
                }],
            }

        with patch.object(self.server, "task_model_runner", return_value=runner):
            self.client.post(f"/api/tasks/{created['id']}/process")
            self._poll_status(created["id"], {"completed"})

        task_dir = Path(self.temp_dir.name) / created["id"]
        page_file = task_dir / "pages" / "p0001.jpg"
        self.assertTrue(page_file.is_file())
        self.assertEqual(page_file.read_bytes(), jpeg)
        stored = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["ocrResults"][0]["pageImage"], "pages/p0001.jpg")

        detail = self.client.get(f"/api/tasks/{created['id']}").json()
        self.assertEqual(detail["ocrResults"][0]["pageImage"], f"/api/tasks/{created['id']}/pages/p0001.jpg")

        # Corrections PUT the hydrated form back (pageImage as URL) — the
        # write side must normalize it to the storage path again.
        detail["ocrResults"][0]["ocrLines"][0]["text"] = "corrected"
        put = self.client.put(f"/api/tasks/{created['id']}", json=detail)
        self.assertEqual(put.status_code, 200)
        stored = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["ocrResults"][0]["pageImage"], "pages/p0001.jpg")

        served = self.client.get(f"/api/tasks/{created['id']}/pages/p0001.jpg")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers["content-type"], "image/jpeg")
        self.assertEqual(served.content, jpeg)

        missing = self.client.get(f"/api/tasks/{created['id']}/pages/nope.jpg")
        self.assertEqual(missing.status_code, 404)

        export = self.client.get(f"/api/tasks/{created['id']}/export?format=searchable-pdf")
        self.assertEqual(export.status_code, 200)
        self.assertTrue(export.content.startswith(b"%PDF"))

    def test_legacy_inline_page_image_still_exports(self):
        # Tasks written before the file migration keep data-URL page images.
        jpeg = self._jpeg_bytes()
        task = {
            "id": "legacyimg1", "name": "x.png", "sourceKind": "image",
            "modelId": "pp-ocrv6-rapid", "modelName": "Rapid", "size": 1,
            "createdAt": 1, "updatedAt": 1, "status": "completed",
            "ocrResults": [{
                "parser": "pp-ocrv6-rapid",
                "pageImage": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii"),
                "ocrLines": [{"text": "hi"}],
                "prunedResult": {"rec_scores": [0.9], "rec_boxes": [[1, 1, 20, 10]], "rec_polys": []},
            }],
        }
        self.client.put("/api/tasks/legacyimg1", json=task)
        # The write-side normalization migrates it to a file reference.
        task_dir = Path(self.temp_dir.name) / "legacyimg1"
        stored = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["ocrResults"][0]["pageImage"], "pages/p0001.jpg")
        export = self.client.get("/api/tasks/legacyimg1/export?format=searchable-pdf")
        self.assertEqual(export.status_code, 200)


if __name__ == "__main__":
    unittest.main()
