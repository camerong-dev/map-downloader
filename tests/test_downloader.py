import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from downloader import Bounds, Cancelled, Tile, candidates, download


class DownloaderTests(unittest.TestCase):
    def test_imagery_region_includes_viewer_tiles(self):
        tiles = candidates(Bounds(-61.528931, -52.484453, -57.615051, -50.934200), 2021, 'S2RGBNIR')
        self.assertEqual(len(tiles), 15)
        urls = dict(tiles)
        for tile in ['S51W061', 'S51W062', 'S52W058', 'S52W059', 'S52W060',
                     'S52W061', 'S52W062', 'S53W059', 'S53W060', 'S53W061']:
            name = f'ESA_WorldCover_10m_2021_v200_{tile}_S2RGBNIR.tif'
            self.assertEqual(urls[name], f'https://esa-worldcover-s2.s3.eu-central-1.amazonaws.com/rgbnir/2021/{tile[:3]}/{name}')

    def test_boundary_selection(self):
        tiles = candidates(Bounds(-3, -3, 0, 0), 2021)
        self.assertEqual(len(tiles), 1)
        self.assertIn('S03W003', tiles[0][0])
        self.assertIn('/v200/2021/map/', tiles[0][1])

    def test_crossing_zero(self):
        self.assertEqual(len(candidates(Bounds(-0.1, -0.1, 0.1, 0.1), 2020)), 4)

    def test_invalid_bounds(self):
        for bounds in [Bounds(0, 0, 0, 1), Bounds(170, 0, -170, 1), Bounds(0, -91, 1, 1), Bounds(float('nan'), 0, 1, 1)]:
            with self.assertRaises(ValueError):
                bounds.validate()

    def test_complete_and_skip(self):
        with tempfile.TemporaryDirectory() as folder:
            tile = Tile('example.tif', 'https://example.test/tile', 4)
            with patch('downloader.urlopen', return_value=io.BytesIO(b'data')) as request:
                self.assertEqual(download(tile, folder, threading.Event(), lambda _: None), 'Downloaded')
                self.assertEqual(download(tile, folder, threading.Event(), lambda _: None), 'Already present')
                self.assertEqual(request.call_count, 1)
            self.assertEqual((Path(folder) / tile.name).read_bytes(), b'data')

    def test_short_transfer_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('downloader.urlopen', return_value=io.BytesIO(b'x')):
                with self.assertRaises(IOError):
                    download(Tile('a.tif', 'https://example.test/a', 10), folder, threading.Event(), lambda _: None)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_existing_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'a.tif'
            target.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):
                download(Tile('a.tif', 'https://example.test/a', 10), folder, threading.Event(), lambda _: None)
            self.assertEqual(target.read_bytes(), b'keep')

    def test_cancel(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            download(Tile('a.tif', 'https://example.test/a', 10), '.', cancel, lambda _: None)


if __name__ == '__main__':
    unittest.main()
