import tempfile
import unittest
from pathlib import Path

from PIL import Image

import rapidocr_adapter
from rapidocr_adapter import build_engine_params, image_to_data_url, to_ppocr_page


class BuildEngineParamsTests(unittest.TestCase):
    def test_language_params_are_passed_through(self):
        params = build_engine_params({"tier": "small", "det_lang": "ch", "rec_lang": "ch"})
        self.assertEqual(params["Det.lang_type"], "ch")
        self.assertEqual(params["Rec.lang_type"], "ch")

    def test_params_use_runtime_settings(self):
        params = build_engine_params({"tier": "medium", "det_lang": "en", "rec_lang": "en"})
        self.assertEqual(params["Det.model_type"], "medium")
        self.assertEqual(params["Rec.model_type"], "medium")
        self.assertEqual(params["Det.lang_type"], "en")
        self.assertEqual(params["Rec.lang_type"], "en")


class EngineSettingsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._path = Path(self._tmp.name) / "engine-settings.json"
        self._original_path = rapidocr_adapter.SETTINGS_PATH
        self._original_settings = dict(rapidocr_adapter.CURRENT_SETTINGS)
        rapidocr_adapter.SETTINGS_PATH = self._path
        self.addCleanup(self._restore)

    def _restore(self):
        rapidocr_adapter.SETTINGS_PATH = self._original_path
        rapidocr_adapter.CURRENT_SETTINGS = self._original_settings

    def test_validate_merges_partial_and_rejects_invalid(self):
        base = {"tier": "small", "det_lang": "ch", "rec_lang": "ch"}
        rapidocr_adapter.CURRENT_SETTINGS = base
        merged = rapidocr_adapter.validate_engine_settings({"tier": "medium"})
        self.assertEqual(merged["tier"], "medium")
        self.assertEqual(merged["det_lang"], "ch")
        with self.assertRaises(ValueError):
            rapidocr_adapter.validate_engine_settings({"tier": "huge"})
        with self.assertRaises(ValueError):
            rapidocr_adapter.validate_engine_settings({"det_lang": "jp"})

    def test_save_and_load_round_trip(self):
        rapidocr_adapter.CURRENT_SETTINGS = {"tier": "tiny", "det_lang": "en", "rec_lang": "en"}
        rapidocr_adapter.save_engine_settings(rapidocr_adapter.CURRENT_SETTINGS)
        self.assertEqual(rapidocr_adapter.load_engine_settings(), rapidocr_adapter.CURRENT_SETTINGS)

    def test_load_returns_defaults_when_file_missing_or_corrupt(self):
        self.assertEqual(
            rapidocr_adapter.load_engine_settings(),
            rapidocr_adapter.default_engine_settings(),
        )
        self._path.write_text("{not json", encoding="utf-8")
        self.assertEqual(
            rapidocr_adapter.load_engine_settings(),
            rapidocr_adapter.default_engine_settings(),
        )


class ToPPOCRPageTests(unittest.TestCase):
    def test_maps_txts_scores_polys_and_bbox(self):
        boxes = [[[0, 0], [10, 0], [10, 5], [0, 5]]]
        page = to_ppocr_page(["hi"], [0.9], boxes, page_index=2)
        pr = page["prunedResult"]
        self.assertEqual(pr["rec_texts"], ["hi"])
        self.assertEqual(pr["rec_scores"], [0.9])
        self.assertEqual(pr["rec_polys"], [[[0.0, 0.0], [10.0, 0.0], [10.0, 5.0], [0.0, 5.0]]])
        self.assertEqual(pr["rec_boxes"], [[0.0, 0.0, 10.0, 5.0]])  # axis-aligned bbox
        self.assertEqual(pr["page_index"], 2)
        self.assertIsNone(page["inputImage"])

    def test_handles_numpy_array_boxes_like_rapidocr(self):
        """RapidOCR returns boxes as a numpy array; `boxes or []` would crash."""
        import numpy as np

        boxes = np.array([[[0, 0], [10, 0], [10, 5], [0, 5]]], dtype=float)
        page = to_ppocr_page(("hi",), (0.9,), boxes, page_index=0)
        pr = page["prunedResult"]
        self.assertEqual(pr["rec_texts"], ["hi"])
        self.assertEqual(pr["rec_scores"], [0.9])
        self.assertEqual(pr["rec_boxes"], [[0.0, 0.0, 10.0, 5.0]])
        self.assertEqual(pr["rec_polys"], [[[0.0, 0.0], [10.0, 0.0], [10.0, 5.0], [0.0, 5.0]]])

    def test_none_boxes_does_not_crash(self):
        page = to_ppocr_page(["hi"], [0.9], None)
        pr = page["prunedResult"]
        self.assertEqual(pr["rec_texts"], ["hi"])
        self.assertEqual(pr["rec_boxes"], [])
        self.assertEqual(pr["rec_polys"], [])

    def test_empty_input(self):
        page = to_ppocr_page([], [], [])
        self.assertEqual(page["prunedResult"]["rec_texts"], [])
        self.assertEqual(page["prunedResult"]["rec_boxes"], [])
        self.assertEqual(page["prunedResult"]["rec_polys"], [])

    def test_multiple_lines_bbox_independent(self):
        boxes = [
            [[0, 0], [10, 0], [10, 5], [0, 5]],
            [[20, 20], [30, 25], [28, 30], [18, 25]],
        ]
        page = to_ppocr_page(["a", "b"], [0.8, 0.7], boxes)
        self.assertEqual(page["prunedResult"]["rec_boxes"][0], [0.0, 0.0, 10.0, 5.0])
        self.assertEqual(page["prunedResult"]["rec_boxes"][1], [18.0, 20.0, 30.0, 30.0])

    def test_input_image_is_passed_through(self):
        page = to_ppocr_page(["hi"], [0.9], [], input_image_b64="data:image/jpeg;base64,AAA")
        self.assertEqual(page["inputImage"], "data:image/jpeg;base64,AAA")

    def test_image_to_data_url_returns_jpeg_data_url(self):
        url = image_to_data_url(Image.new("RGB", (4, 3), color=(255, 0, 0)))
        self.assertTrue(url.startswith("data:image/jpeg;base64,"))
        self.assertGreater(len(url.split(",", 1)[1]), 0)


if __name__ == "__main__":
    unittest.main()
