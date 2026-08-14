import unittest

from PIL import Image

from rapidocr_adapter import image_to_data_url, to_ppocr_page


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
