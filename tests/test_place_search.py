import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from downloader import Bounds
from place_search import WebCache, buffered, parse_places, project, search, tile_bounds, unproject


def feature(name='Horsham', extent=None, kind='town', lon=-0.327, lat=51.063):
    properties = {'name': name, 'country': 'United Kingdom', 'osm_value': kind}
    if extent is not None:
        properties['extent'] = extent
    return {'properties': properties, 'geometry': {'coordinates': [lon, lat]}}


class PlaceSearchTests(unittest.TestCase):
    def test_extent_order_and_disambiguation(self):
        result = parse_places({'features': [feature(extent=[-0.4, 51.1, -0.2, 51])]})
        self.assertEqual(result[0].bounds, Bounds(-0.4, 51, -0.2, 51.1))
        self.assertIn('United Kingdom', result[0].label)
        self.assertFalse(result[0].approximate)

    def test_centre_only_is_explicit_and_country_never_guessed(self):
        result = parse_places({'features': [feature(), feature('UK', kind='country')]})
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].approximate)
        self.assertIn('5 km', result[0].label)
        self.assertLess(result[0].bounds.west, -0.327)

    def test_invalid_and_date_line_results_are_ignored(self):
        result = parse_places({'features': [feature(extent=[170, 10, -170, 0]),
                                            feature(lat=float('nan')), {},
                                            feature(extent=[0, 10, 0, 0])]})
        self.assertEqual(result, [])

    def test_buffer_and_limits(self):
        original = Bounds(-1, 50, 1, 51)
        self.assertEqual(buffered(original, 0), original)
        grown = buffered(original, 10)
        self.assertLess(grown.west, original.west)
        self.assertGreater(grown.north, original.north)
        for bad in (-1, 1001, math.inf, math.nan, 'invalid'):
            with self.assertRaises(ValueError):
                buffered(original, bad)

    def test_mercator_round_trip_and_poles(self):
        for zoom in (0, 8, 15):
            for lon, lat in ((0, 0), (-0.33, 51.06), (179, -80)):
                actual = unproject(*project(lon, lat, zoom), zoom)
                self.assertAlmostEqual(actual[0], lon)
                self.assertAlmostEqual(actual[1], lat)
        self.assertTrue(all(math.isfinite(v) for v in project(0, 90, 5)))

    def test_esa_coverage_uses_product_grid_and_hemispheres(self):
        self.assertEqual(tile_bounds('ESA_WorldCover_10m_2021_v200_S03W003_Map.tif'), Bounds(-3, -3, 0, 0))
        self.assertEqual(tile_bounds('ESA_WorldCover_10m_2021_v200_N51W001_S2RGBNIR.tif'), Bounds(-1, 51, 0, 52))
        with self.assertRaises(ValueError):
            tile_bounds('not-a-tile')

    @patch('place_search.time.sleep')
    @patch.dict('os.environ', {'ESA_MAPS_SEARCH_URL': 'https://example.test/api/'})
    def test_search_is_encoded_and_configurable(self, sleep):
        cache = Mock()
        cache.get.return_value = json.dumps({'features': [feature()]}).encode()
        self.assertEqual(len(search('Horsham, UK', cache)), 1)
        url = cache.get.call_args.args[0]
        self.assertIn('https://example.test/api/?q=Horsham%2C+UK', url)
        self.assertIn('limit=8', url)
        with self.assertRaises(ValueError):
            search('x', cache)

    @patch('place_search.urlopen')
    def test_http_cache_reuses_data_and_honours_longer_lifetime(self, urlopen):
        response = Mock()
        response.read.return_value = b'cached payload'
        response.headers = {'Cache-Control': 'max-age=1000000'}
        urlopen.return_value.__enter__.return_value = response
        with tempfile.TemporaryDirectory() as temporary:
            cache = WebCache(temporary)
            with patch('place_search.time.time', return_value=1000):
                self.assertEqual(cache.get('https://example.test/tile', 604800, 100), b'cached payload')
            with patch('place_search.time.time', return_value=900000):
                self.assertEqual(cache.get('https://example.test/tile', 604800, 100), b'cached payload')
            self.assertEqual(urlopen.call_count, 1)
            self.assertIn('ESAMapsDesktop', urlopen.call_args.args[0].get_header('User-agent'))
            record = json.loads(next(Path(temporary).iterdir()).read_text())
            self.assertEqual(record['expires'], 1001000)

    @patch('place_search.urlopen')
    def test_invalid_cache_recovers_and_oversize_responses_are_rejected(self, urlopen):
        response = Mock()
        response.read.return_value = b'oversized'
        response.headers = {}
        urlopen.return_value.__enter__.return_value = response
        with tempfile.TemporaryDirectory() as temporary:
            cache = WebCache(temporary)
            with self.assertRaises(ValueError):
                cache.get('https://example.test/tile', 10, 2)
            self.assertFalse(list(Path(temporary).iterdir()))
            with self.assertRaises(ValueError):
                cache.get('http://example.test/tile', 10, 100)


if __name__ == '__main__':
    unittest.main()
