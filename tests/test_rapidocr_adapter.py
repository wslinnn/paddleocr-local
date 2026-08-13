import unittest

from rapidocr_adapter import to_ppocr_page


class ToPPOCRPageTests(unittest.TestCase):
    def test_maps_txts_scores_polys_and_bbox(self):
        boxes = [[[0, 0], [10, 0], [10, 5], [0, 5]]]
        page = to_ppocr_page(["hi"], [0.9], boxes, page_index=2)
        pr = page["prunedResult"]
        self.assertEqual(pr["rec_texts"], ["hi"])
        self.assertEqual(pr["rec_scores"], [0.9])
        self.assertEqual(pr["rec_polys"], [[[0, 0], [10, 0], [10, 5], [0, 5]]])
        self.assertEqual(pr["rec_boxes"], [[0, 0, 10, 5]])  # axis-aligned bbox
        self.assertEqual(pr["page_index"], 2)
        self.assertIsNone(page["inputImage"])

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
        self.assertEqual(page["prunedResult"]["rec_boxes"][0], [0, 0, 10, 5])
        self.assertEqual(page["prunedResult"]["rec_boxes"][1], [18, 20, 30, 30])


if __name__ == "__main__":
    unittest.main()
