import io
import json
import threading
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from downloader import Bounds, Cancelled
from osm_download import cells, collections, fetch, query, parse_region


class OSMTests(unittest.TestCase):
    def test_busy_server_error_explains_retry(self):
        cancel = threading.Event()
        with patch('osm_download.urlopen', side_effect=HTTPError('https://example.test', 502, 'Bad Gateway', {}, None)) as request, patch.object(cancel, 'wait', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 502.*retry later'):
                fetch(Bounds(0, 0, 0.1, 0.1), cancel, lambda m: None)
            self.assertEqual(request.call_count, 3)

    def test_retry_then_cache_resume(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as cache:
            responses = [HTTPError('https://example.test', 502, 'Bad Gateway', {}, None), io.BytesIO(b'{"elements": []}')]
            with patch('osm_download.urlopen', side_effect=responses) as request, patch.object(cancel, 'wait', return_value=False):
                fetch(Bounds(0, 0, 0.1, 0.1), cancel, lambda m: None, cache_dir=cache)
                self.assertEqual(request.call_count, 2)
            with patch('osm_download.urlopen') as request:
                fetch(Bounds(0, 0, 0.1, 0.1), cancel, lambda m: None, cache_dir=cache)
                request.assert_not_called()

    def test_smaller_requests_preserve_full_warped_extent(self):
        bounds = Bounds(-1, 49.99998297832621, 1.0000170216737934, 52)
        plan = cells(bounds, step=0.25)
        self.assertEqual(len(plan), 64)
        self.assertEqual(plan[-1].east, bounds.east)
        self.assertEqual(plan[-1].north, bounds.north)

    def test_rate_limit_is_not_retried(self):
        with patch('osm_download.urlopen', side_effect=HTTPError('https://example.test', 429, 'Too many requests', {}, None)) as request:
            with self.assertRaisesRegex(RuntimeError, 'HTTP 429'):
                fetch(Bounds(0, 0, 0.1, 0.1), threading.Event(), lambda m: None)
            self.assertEqual(request.call_count, 1)

    def test_failed_batch_keeps_completed_cells(self):
        bounds = Bounds(0, 0, 0.4, 0.1)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as cache:
            responses = [io.BytesIO(b'{"elements": []}'), HTTPError('https://example.test', 400, 'Bad Request', {}, None)]
            with patch('osm_download.urlopen', side_effect=responses):
                with self.assertRaises(HTTPError):
                    fetch(bounds, threading.Event(), lambda m: None, cache_dir=cache)
            self.assertEqual(len(list(Path(cache).glob('*.json'))), 1)
            with patch('osm_download.urlopen', return_value=io.BytesIO(b'{"elements": []}')) as request:
                fetch(bounds, threading.Event(), lambda m: None, cache_dir=cache)
                self.assertEqual(request.call_count, 1)

    def test_cancel_during_backoff_stops_retry(self):
        cancel = threading.Event()
        def stop(delay):
            cancel.set()
            return True
        with patch('osm_download.urlopen', side_effect=HTTPError('https://example.test', 502, 'Bad Gateway', {}, None)) as request, patch.object(cancel, 'wait', side_effect=stop):
            with self.assertRaises(Cancelled):
                fetch(Bounds(0, 0, 0.1, 0.1), cancel, lambda m: None)
            self.assertEqual(request.call_count, 1)

    def test_reported_region_reversed_longitudes(self):
        bounds = parse_region('0.514984,50.966211,-0.199127,51.109558')
        self.assertEqual(bounds, Bounds(-0.199127, 50.966211, 0.514984, 51.109558))
        self.assertEqual(len(cells(bounds)), 2)

    def test_corner_order_and_whitespace(self):
        self.assertEqual(parse_region(' 0.5, 51.2, -0.2, 50.9 '), Bounds(-0.2, 50.9, 0.5, 51.2))
        self.assertEqual(parse_region('-0.2,50.9,0.5,51.2'), Bounds(-0.2, 50.9, 0.5, 51.2))

    def test_invalid_coordinate_text(self):
        for text in ('a,1,2,3', '1,2,3', '1,2,3,4,5', 'nan,0,1,1', '0,0,0,1',
                     '0,91,1,92', '181,0,182,1', '170,0,-170,1'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_region(text)

    def test_subdivision_covers_edges_and_rejects_large_regions(self):
        result = cells(Bounds(-1, 50, -0.2, 50.7))
        self.assertEqual(len(result), 4)
        self.assertEqual(result[-1], Bounds(-0.5, 50.5, -0.2, 50.7))
        with self.assertRaises(ValueError):
            cells(Bounds(-20, 0, 20, 40))
        with self.assertRaises(ValueError):
            cells(Bounds(0, 84, 1, 86))
        self.assertIn('(50,-1,50.7,-0.2)', query(Bounds(-1, 50, -0.2, 50.7)))

    def test_geojson_dedup_and_lon_lat_order(self):
        way = {'type': 'way', 'id': 1, 'tags': {'highway': 'primary', 'name': 'Main Road'},
               'geometry': [{'lon': -1, 'lat': 50}, {'lon': -0.9, 'lat': 50.1}]}
        node = {'type': 'node', 'id': 1, 'lon': -1, 'lat': 50, 'tags': {'place': 'town', 'name': 'Test'}}
        roads, places = collections([way, way, node])
        self.assertEqual(len(roads['features']), 1)
        self.assertEqual(places['features'][0]['geometry']['coordinates'], [-1, 50])
        way['geometry'][0] = {}
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            collections([way])

    def test_fetch_rejects_server_partial_results_and_cancel(self):
        bounds = Bounds(0, 0, 0.1, 0.1)
        with patch('osm_download.urlopen', return_value=io.BytesIO(json.dumps({'elements': [], 'remark': 'timeout'}).encode())):
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                fetch(bounds, threading.Event(), lambda m: None)
        cancel = threading.Event()
        cancel.set()
        with patch('osm_download.urlopen') as request:
            with self.assertRaises(Cancelled):
                fetch(bounds, cancel, lambda m: None)
            request.assert_not_called()

    def test_empty_area_is_valid_and_request_is_identified_post(self):
        with patch('osm_download.urlopen', return_value=io.BytesIO(b'{"elements": []}')) as request:
            roads, places = fetch(Bounds(0, 0, 0.1, 0.1), threading.Event(), lambda m: None)
        self.assertEqual(roads['features'], [])
        self.assertEqual(places['features'], [])
        self.assertEqual(request.call_args.args[0].get_method(), 'POST')
        self.assertIn('ESARegionDownloader', request.call_args.args[0].get_header('User-agent'))


if __name__ == '__main__':
    unittest.main()
