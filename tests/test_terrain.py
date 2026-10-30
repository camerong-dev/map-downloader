import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError
from terrain import TerrainOptions, terrain_tiles, cached_resource, PNG_HEADER


class TerrainTests(unittest.TestCase):
    def test_disabled_terrain_does_not_require_a_dem_or_network(self):
        self.assertFalse(TerrainOptions.from_job({'terrain_dem':'missing.tif','terrain_strength':'bad'}).enabled)

    def test_options_are_validated(self):
        for change in ({'terrain_strength':float('nan')}, {'terrain_strength':2}, {'terrain_interval':0}, {'terrain_dem':'missing.tif'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                TerrainOptions.from_job({'terrain_enabled':True, **change})
        options = TerrainOptions.from_job({'terrain_enabled':True,'terrain_contours':True})
        self.assertEqual(options.interval,50)
        self.assertEqual(options.job_options()['terrain_strength'],0.4)

    def test_tile_budget_bounds_large_exports(self):
        for bounds in ((-20037508,-20037508,20037508,20037508),(10000,10000,110000,110000)):
            zoom,(x0,y0,x1,y1) = terrain_tiles(bounds)
            self.assertLessEqual((x1-x0+1)*(y1-y0+1),256)
            self.assertTrue(0 <= x0 <= x1 < 2**zoom and 0 <= y0 <= y1 < 2**zoom)

    def test_tiles_are_cached_and_cancel_checked(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            root=Path(temporary)
            job={'cancel':str(root/'cancel')}
            with patch('terrain.urlopen',return_value=io.BytesIO(PNG_HEADER+b'fixture')) as network:
                target=root/'tile.png'
                cached_resource('https://example.test/tile',target,job,png=True)
                cached_resource('https://example.test/tile',target,job,png=True)
                network.assert_called_once()
                (root/'cancel').touch()
                with self.assertRaises(InterruptedError):
                    cached_resource('https://example.test/tile',target,job,png=True)
            self.assertFalse((root/'tile.png.part').exists())

    def test_cancellation_interrupts_retry_wait(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            root=Path(temporary)
            with patch('terrain.urlopen',side_effect=URLError('temporary')), patch('terrain.time.sleep',side_effect=lambda _: (root/'cancel').touch()):
                with self.assertRaises(InterruptedError):
                    cached_resource('https://example.test/tile',root/'tile',{'cancel':str(root/'cancel')})
