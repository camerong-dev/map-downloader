import unittest

from downloader import Bounds, parse_region


class CoordinateTests(unittest.TestCase):
    def test_corner_order_and_whitespace(self):
        expected = Bounds(-0.2, 50.9, 0.5, 51.2)
        self.assertEqual(parse_region(' 0.5, 51.2, -0.2, 50.9 '), expected)
        self.assertEqual(parse_region('-0.2,50.9,0.5,51.2'), expected)

    def test_invalid_coordinate_text(self):
        for text in ('a,1,2,3', '1,2,3', '1,2,3,4,5', 'nan,0,1,1', 'inf,0,1,1',
                     '0,0,0,1', '0,91,1,92', '181,0,182,1', '170,0,-170,1'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_region(text)
