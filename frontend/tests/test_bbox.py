import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from fastapi import HTTPException

from frontend import main


class BboxFilterTests(unittest.TestCase):
    def test_builds_intersects_envelope(self):
        self.assertEqual(
            main.build_bbox_filter("0.1,52.1,0.2,52.3"),
            'coverage_srpts:"Intersects(ENVELOPE(0.1, 0.2, 52.3, 52.1))"'
            ' OR origin_srpts:"Intersects(ENVELOPE(0.1, 0.2, 52.3, 52.1))"',
        )

    def test_allows_antimeridian_crossing(self):
        self.assertEqual(
            main.build_bbox_filter("170,-50,-170,-30"),
            'coverage_srpts:"Intersects(ENVELOPE(170.0, -170.0, -30.0, -50.0))"'
            ' OR origin_srpts:"Intersects(ENVELOPE(170.0, -170.0, -30.0, -50.0))"',
        )

    def test_rejects_malformed_input(self):
        for bbox in [
            "",
            "1,2,3",
            "1,2,3,4,5",
            "a,b,c,d",
            "0,0,1,1) OR *:*",
            "nan,0,1,1",
            "inf,0,1,1",
        ]:
            with self.subTest(bbox=bbox):
                with self.assertRaises(HTTPException) as ctx:
                    main.build_bbox_filter(bbox)
                self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_out_of_range_coordinates(self):
        for bbox in ["-181,0,1,1", "0,0,181,1", "0,-91,1,1", "0,0,1,91", "0,10,1,5"]:
            with self.subTest(bbox=bbox):
                with self.assertRaises(HTTPException):
                    main.build_bbox_filter(bbox)


if __name__ == "__main__":
    unittest.main()
