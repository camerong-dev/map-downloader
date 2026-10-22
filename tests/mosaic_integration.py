"""Run using the QGIS runtime; small synthetic rasters, no user data modified."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from osgeo import gdal, osr
from mosaic_worker import Cancelled, run


class MosaicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.folder = Path(self.temp.name)
        self.inputs = []
        self.base = np.arange(64, dtype=np.uint16).reshape(8, 8) + 100
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(3857)
        for i in range(2):
            path = self.folder / f'source-{i}.tif'
            ds = gdal.GetDriverByName('GTiff').Create(str(path), 8, 8, 4, gdal.GDT_UInt16)
            ds.SetGeoTransform((i * 8, 1, 0, 8, 0, -1))
            ds.SetProjection(srs.ExportToWkt())
            for b in range(1, 5):
                data = self.base + i * 100 + b * 10
                data = data.copy()
                data[0, 0] = 0
                ds.GetRasterBand(b).WriteArray(data)
                ds.GetRasterBand(b).SetNoDataValue(0)
            ds = None
            self.inputs.append(str(path))

    def tearDown(self):
        self.temp.cleanup()

    def job(self, mode='shared'):
        return {'inputs': self.inputs, 'output': str(self.folder / f'{mode}.tif'),
                'cancel': str(self.folder / 'cancel'), 'mode': mode}

    def test_shared_mosaic_and_transparency(self):
        job = self.job()
        run(job)
        ds = gdal.Open(job['output'])
        self.assertEqual((ds.RasterXSize, ds.RasterYSize, ds.RasterCount), (16, 8, 4))
        self.assertEqual(ds.GetGeoTransform(), (0, 1, 0, 8, 0, -1))
        alpha = ds.GetRasterBand(4).ReadAsArray()
        self.assertEqual(alpha[0, 0], 0)
        self.assertEqual(alpha[0, 8], 0)
        self.assertEqual(alpha[1, 1], 255)
        self.assertEqual(ds.GetRasterBand(1).DataType, gdal.GDT_Byte)
        ds = None
        self.assertEqual(len(list(self.folder.glob('esa-mosaic-*'))), 0)

    def test_fixed_qgis_reflectance_stretch(self):
        for path in self.inputs:
            ds = gdal.Open(path, gdal.GA_Update)
            for band in range(1, 5):
                ds.GetRasterBand(band).SetScale(0.0001)
            ds = None
        job = self.job('fixed')
        job['maximum'] = 0.20  # Verify the supplied original style independently of GUI defaults.
        run(job)
        ds = gdal.Open(job['output'])
        self.assertEqual(ds.RasterCount, 4)
        self.assertEqual(ds.GetMetadataItem('QGIS_STRETCH_MINIMUM'), '0.005')
        self.assertEqual(ds.GetRasterBand(4).ReadAsArray()[0, 0], 0)
        actual = ds.GetRasterBand(1).ReadAsArray()[1, 1]
        expected = ((float(self.base[1, 1]) + 10) * 0.0001 - 0.005) * 255 / (0.20 - 0.005)
        self.assertAlmostEqual(float(actual), expected, delta=1)
        ds = None
        from qgis.core import QgsRasterLayer, QgsContrastEnhancement
        layer = QgsRasterLayer(job['output'], 'style check')
        self.assertEqual(layer.renderer().alphaBand(), 4)
        self.assertEqual(layer.renderer().redContrastEnhancement().contrastEnhancementAlgorithm(), QgsContrastEnhancement.NoEnhancement)
        layer = None

    def test_geographic_inputs_reproject_at_source_detail(self):
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        for i, path in enumerate(self.inputs):
            ds = gdal.Open(path, gdal.GA_Update)
            ds.SetProjection(srs.ExportToWkt())
            ds.SetGeoTransform((54 + i * 0.008, 0.001, 0, 27, 0, -0.001))
            ds = None
        job = self.job()
        run(job)
        ds = gdal.Open(job['output'])
        self.assertEqual(ds.GetSpatialRef().GetAuthorityCode(None), '3857')
        self.assertAlmostEqual(ds.GetGeoTransform()[1], 111.31949, places=3)
        self.assertGreaterEqual(ds.RasterXSize, 16)
        self.assertTrue(Path(job['output']).with_suffix('.qml').exists())
        ds = None

    def test_matching_corrects_known_offset(self):
        job = self.job('match')
        run(job)
        ds = gdal.Open(job['output'])
        image = ds.ReadAsArray()
        np.testing.assert_allclose(image[:, :, :8], image[:, :, 8:], atol=1)
        ds = None

    def test_existing_output_preserved(self):
        job = self.job()
        Path(job['output']).write_bytes(b'keep')
        with self.assertRaises(ValueError):
            run(job)
        self.assertEqual(Path(job['output']).read_bytes(), b'keep')

    def test_cancel_leaves_no_output(self):
        job = self.job()
        Path(job['cancel']).touch()
        with self.assertRaises(Cancelled):
            run(job)
        self.assertFalse(Path(job['output']).exists())

    def test_cancel_during_balancing_cleans_files(self):
        job = self.job()
        def trigger(message, progress=None):
            if message.startswith('Balancing tile'):
                Path(job['cancel']).touch()
        with patch('mosaic_worker.emit', side_effect=trigger):
            with self.assertRaises(Cancelled):
                run(job)
        self.assertFalse(Path(job['output']).exists())
        self.assertFalse(list(self.folder.glob('esa-mosaic-*')))

    def test_cancel_during_merge_cleans_files(self):
        job = self.job()
        def trigger(message, progress=None):
            if message.startswith('Merging'):
                Path(job['cancel']).touch()
        with patch('mosaic_worker.emit', side_effect=trigger):
            with self.assertRaises(Cancelled):
                run(job)
        self.assertFalse(Path(job['output']).exists())
        self.assertFalse(list(self.folder.glob('esa-mosaic-*')))


if __name__ == '__main__':
    unittest.main()
