import unittest
from types import SimpleNamespace

from app import App, parse_bounds
from downloader import Bounds, PRODUCTS


class Variable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class DownloadCoordinatesTests(unittest.TestCase):
    def test_pasted_example_sorts_longitudes(self):
        self.assertEqual(parse_bounds('0.514984,50.966211,-0.199127,51.109558'),
                         Bounds(-0.199127, 50.966211, 0.514984, 51.109558))

    def test_individual_edges_are_sorted_and_displayed(self):
        form = SimpleNamespace(
            coords={key: Variable(value) for key, value in zip(
                ('west', 'south', 'east', 'north'), ('0.5', '51.2', '-0.2', '50.9'))},
            year=Variable('2021'), product=Variable(next(iter(PRODUCTS))))
        bounds, year, product = App.values(form)
        self.assertEqual(bounds, Bounds(-0.2, 50.9, 0.5, 51.2))
        self.assertEqual([v.get() for v in form.coords.values()], ['-0.2', '50.9', '0.5', '51.2'])
        self.assertEqual(year, 2021)
        self.assertEqual(product, 'S2RGBNIR')


if __name__ == '__main__':
    unittest.main()
