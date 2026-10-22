"""QGIS-runtime tests for exact raster-window splitting."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from osgeo import gdal, osr
from split_worker import grid_windows, run


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.path = self.root / 'source.tif'
        self.data = np.arange(77, dtype=np.uint16).reshape(7, 11)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(3857)
        ds = gdal.GetDriverByName('GTiff').Create(str(self.path), 11, 7, 1, gdal.GDT_UInt16)
        ds.SetProjection(srs.ExportToWkt())
        ds.SetGeoTransform((-62, 0.01, 0, -51, 0, -0.01))
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(0)
        band.SetScale(0.0001)
        band.WriteArray(self.data)
        band = ds = None

    def tearDown(self):
        self.temp.cleanup()

    def job(self, action='export'):
        return {'inputs': [str(self.path)], 'action': action, 'workspace': str(self.root),
                'cancel': str(self.root / 'cancel'), 'preview': str(self.root / 'preview.png'),
                'rows': 3, 'columns': 2, 'selected': list(range(6)), 'output': str(self.root / 'areas')}

    def test_exact_coverage_and_values(self):
        job = self.job()
        run(job)
        recovered = np.zeros_like(self.data)
        for r, c, x, y, w, h in grid_windows(11, 7, 3, 2):
            ds = gdal.Open(str(Path(job['output']) / f'area_row{r + 1:02d}_col{c + 1:02d}.tif'))
            self.assertEqual((ds.RasterXSize, ds.RasterYSize), (w, h))
            self.assertEqual(ds.GetRasterBand(1).GetScale(), 0.0001)
            self.assertEqual(ds.GetRasterBand(1).GetNoDataValue(), 0)
            self.assertAlmostEqual(ds.GetGeoTransform()[0], -62 + x * 0.01)
            self.assertAlmostEqual(ds.GetGeoTransform()[3], -51 - y * 0.01)
            recovered[y:y + h, x:x + w] = ds.ReadAsArray()
            ds = None
        np.testing.assert_array_equal(recovered, self.data)

    def test_selected_cells_only(self):
        job = self.job()
        job['selected'] = [1, 4]
        run(job)
        self.assertEqual(sorted(p.name for p in Path(job['output']).glob('*.tif')),
                         ['area_row01_col02.tif', 'area_row03_col01.tif'])

    def test_whole_region_overview_keeps_full_detail_splits(self):
        job = self.job()
        # Pixel spacing is 0.01 m in this small fixture; a coarser overview
        # should cover the whole image, including excluded detail cells.
        job.update(create_overview=True, overview_resolution=0.02, selected=[1])
        run(job)
        import json
        manifest = json.loads((Path(job['output']) / 'areas.json').read_text())
        info = manifest['overview']
        ds = gdal.Open(str(Path(job['output']) / info['file']))
        self.assertEqual(ds.GetSpatialRef().GetAuthorityCode(None), '3857')
        self.assertEqual((ds.RasterXSize, ds.RasterYSize), (6, 4))
        self.assertAlmostEqual(ds.GetGeoTransform()[1], 0.02)
        self.assertEqual(ds.GetRasterBand(1).GetScale(), 0.0001)
        ds = None
        ds = gdal.Open(str(Path(job['output']) / 'area_row01_col02.tif'))
        self.assertAlmostEqual(ds.GetGeoTransform()[1], 0.01)
        np.testing.assert_array_equal(ds.ReadAsArray(), self.data[:2, 5:11])
        ds = None

    def test_overview_averages_rgb_and_preserves_transparency(self):
        path = self.root / 'rgb.tif'
        ds = gdal.GetDriverByName('GTiff').Create(str(path), 4, 4, 4, gdal.GDT_Byte)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(3857)
        ds.SetProjection(srs.ExportToWkt())
        ds.SetGeoTransform((0, 50, 0, 200, 0, -50))
        data = np.tile(np.array([[0, 100], [200, 100]], dtype=np.uint8), (2, 2))
        for band in [1, 2, 3]:
            ds.GetRasterBand(band).WriteArray(data)
        alpha = np.full((4, 4), 255, dtype=np.uint8)
        alpha[:2, :2] = 0
        ds.GetRasterBand(4).WriteArray(alpha)
        ds.GetRasterBand(4).SetColorInterpretation(gdal.GCI_AlphaBand)
        ds = None
        job = self.job()
        job.update(inputs=[str(path)], rows=1, columns=1, selected=[0], create_overview=True, overview_resolution=100)
        run(job)
        ds = gdal.Open(str(Path(job['output']) / 'region_overview_100m.tif'))
        np.testing.assert_array_equal(ds.GetRasterBand(1).ReadAsArray(), [[0, 100], [100, 100]])
        np.testing.assert_array_equal(ds.GetRasterBand(4).ReadAsArray(), [[0, 255], [255, 255]])
        self.assertTrue((Path(job['output']) / 'region_overview_100m.qml').exists())
        ds = None

    def test_overview_rejects_finer_spacing(self):
        job = self.job()
        job.update(create_overview=True, overview_resolution=0.001)
        with self.assertRaises(ValueError):
            run(job)
        self.assertFalse(Path(job['output']).exists())

    def test_overview_cancellation_keeps_completed_details(self):
        job = self.job()
        job.update(create_overview=True, overview_resolution=0.02)
        def cancel(kind, **values):
            if values.get('message', '').startswith('Creating whole-region overview'):
                Path(job['cancel']).touch()
        with patch('split_worker.emit', side_effect=cancel):
            with self.assertRaises(InterruptedError):
                run(job)
        output = Path(job['output'])
        self.assertEqual(len(list(output.glob('area_*.tif'))), 6)
        self.assertFalse(list(output.glob('*overview*')))
        self.assertFalse(list(output.glob('*.part.tif')))

    def test_geographic_input_exports_web_mercator(self):
        ds = gdal.Open(str(self.path), gdal.GA_Update)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        ds.SetProjection(srs.ExportToWkt())
        ds = None
        job = self.job()
        job.update(rows=1, columns=1, selected=[0])
        run(job)
        ds = gdal.Open(str(Path(job['output']) / 'area_row01_col01.tif'))
        self.assertEqual(ds.GetSpatialRef().GetAuthorityCode(None), '3857')
        self.assertGreater(ds.GetGeoTransform()[1], 100)
        self.assertEqual(ds.GetRasterBand(1).GetScale(), 0.0001)
        self.assertGreater(ds.GetRasterBand(1).GetOverviewCount(), 0)
        ds = None

    def test_preview_from_group(self):
        second = self.root / 'second.tif'
        ds = gdal.Translate(str(second), str(self.path))
        ds.SetGeoTransform((-61.89, 0.01, 0, -51, 0, -0.01))
        ds = None
        job = self.job('preview')
        job['inputs'].append(str(second))
        with patch('split_worker.emit') as emit:
            run(job)
        info = emit.call_args.kwargs['info']
        self.assertEqual((info['width'], info['height']), (22, 7))
        self.assertTrue(Path(job['preview']).is_file())

    def test_existing_folder_preserved(self):
        job = self.job()
        Path(job['output']).mkdir()
        with self.assertRaises(ValueError):
            run(job)

    def test_cancel_partial_removed(self):
        job = self.job()
        def cancel(kind, **values):
            if kind == 'status':
                Path(job['cancel']).touch()
        with patch('split_worker.emit', side_effect=cancel):
            with self.assertRaises(InterruptedError):
                run(job)
        self.assertEqual(list(Path(job['output']).glob('*.tif')), [])

    def test_invalid_grid(self):
        with self.assertRaises(ValueError):
            grid_windows(11, 7, 8, 2)


if __name__ == '__main__':
    unittest.main()
