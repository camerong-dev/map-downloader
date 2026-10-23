import hashlib
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from downloader import Cancelled
from geofabrik import DEFAULT_SOURCE, obtain, validate_source


class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.headers = {'Content-Length': str(len(value)), 'Last-Modified': 'Test date'}


class GeofabrikTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.folder = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def responses(self, body=b'PBF fixture'):
        digest = hashlib.md5(body, usedforsecurity=False).hexdigest().encode()
        return [Response(digest + b' file.osm.pbf'), Response(body)]

    def test_download_verification_offline_reuse_and_refresh(self):
        with patch('geofabrik.urlopen', side_effect=self.responses()) as request:
            target, metadata = obtain(DEFAULT_SOURCE, self.folder, threading.Event(), lambda m: None)
            self.assertEqual(request.call_count, 2)
        self.assertEqual(target.read_bytes(), b'PBF fixture')
        self.assertEqual(metadata['size'], 11)
        with patch('geofabrik.urlopen') as request:
            cached, _ = obtain(DEFAULT_SOURCE, self.folder, threading.Event(), lambda m: None)
            request.assert_not_called()
            self.assertEqual(cached, target)
        with patch('geofabrik.urlopen', side_effect=self.responses(b'New PBF')):
            obtain(DEFAULT_SOURCE, self.folder, threading.Event(), lambda m: None, refresh=True)
        self.assertEqual(target.read_bytes(), b'New PBF')

    def test_bad_checksum_never_publishes_file(self):
        responses = [Response(b'0' * 32), Response(b'Incomplete or changed file')]
        with patch('geofabrik.urlopen', side_effect=responses):
            with self.assertRaisesRegex(ValueError, 'checksum'):
                obtain(DEFAULT_SOURCE, self.folder, threading.Event(), lambda m: None)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_local_file_is_offline_and_cancel_is_checked(self):
        source = self.folder / 'region.osm.pbf'
        source.write_bytes(b'fixture')
        with patch('geofabrik.urlopen') as request:
            result, metadata = obtain(str(source), self.folder, threading.Event(), lambda m: None)
            self.assertEqual(result, source)
            self.assertEqual(metadata['kind'], 'local OSM extract')
            request.assert_not_called()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            obtain(DEFAULT_SOURCE, self.folder, cancel, lambda m: None)

    def test_source_validation(self):
        for url in ('http://download.geofabrik.de/x.osm.pbf', 'https://example.com/x.osm.pbf',
                    'https://download.geofabrik.de/x.zip', 'https://download.geofabrik.de/x.osm.pbf?a=b'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_source(url)
