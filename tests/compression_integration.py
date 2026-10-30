"""QGIS-runtime checks for JPEG GeoTIFF exports and internal masks."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from osgeo import gdal, osr
from raster_output import export_geotiff, finish_output, write_rgb_style
from split_worker import run as split
from mosaic_worker import run as merge


class CompressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.tif'
        yy, xx = np.indices((512, 512))
        noise = np.random.default_rng(42).normal(0, 3, (512, 512))
        base = np.clip(70 + xx / 6 + yy / 10 + noise, 0, 255).astype(np.uint8)
        self.rgb = np.stack([base, np.clip(base.astype(float) * .9, 0, 255).astype(np.uint8),
                             np.clip(base.astype(float) * .7, 0, 255).astype(np.uint8)])
        self.alpha = np.full((512, 512), 255, dtype=np.uint8)
        self.alpha[:64, :64] = 0
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(3857)
        ds = gdal.GetDriverByName('GTiff').Create(str(self.source), 512, 512, 4, gdal.GDT_Byte)
        ds.SetProjection(srs.ExportToWkt())
        ds.SetGeoTransform((0, 10, 0, 5120, 0, -10))
        for b in range(3):
            ds.GetRasterBand(b + 1).WriteArray(self.rgb[b])
        ds.GetRasterBand(4).WriteArray(self.alpha)
        ds.GetRasterBand(4).SetColorInterpretation(gdal.GCI_AlphaBand)
        ds = None
        self.job = {'compression': 'JPEG', 'jpeg_quality': 85, 'cancel': str(self.root / 'cancel')}

    def tearDown(self):
        self.temp.cleanup()

    def test_jpeg_smaller_with_internal_transparency_and_detail(self):
        source = gdal.Open(str(self.source))
        lossless = export_geotiff(source, self.root / 'lossless.tif', {'compression': 'DEFLATE'})
        finish_output(lossless)
        lossless.Close()
        result = export_geotiff(source, self.root / 'jpeg.tif', self.job)
        finish_output(result)
        self.assertEqual(result.RasterCount, 3)
        self.assertIn('JPEG', result.GetMetadataItem('COMPRESSION', 'IMAGE_STRUCTURE'))
        self.assertEqual(result.GetMetadataItem('EXPORT_JPEG_QUALITY'), '85')
        np.testing.assert_array_equal(result.GetRasterBand(1).GetMaskBand().ReadAsArray(), self.alpha)
        error = np.abs(result.ReadAsArray().astype(float) - self.rgb.astype(float))
        self.assertLess(error[:, self.alpha > 0].mean(), 5)
        result.Close()
        source.Close()
        self.assertFalse((self.root / 'jpeg.tif.msk').exists())
        self.assertLess((self.root / 'jpeg.tif').stat().st_size, (self.root / 'lossless.tif').stat().st_size)

    def test_split_and_whole_region_overview_use_jpeg(self):
        job = {**self.job, 'inputs': [str(self.source)], 'workspace': str(self.root), 'action': 'export',
               'output': str(self.root / 'areas'), 'rows': 2, 'columns': 2, 'selected': [0],
               'create_overview': True, 'overview_resolution': 100}
        split(job)
        for name in ['area_row01_col01.tif', 'region_overview_100m.tif']:
            ds = gdal.Open(str(self.root / 'areas' / name))
            self.assertEqual(ds.GetSpatialRef().GetAuthorityCode(None), '3857')
            self.assertEqual(ds.RasterCount, 3)
            self.assertIn('JPEG', ds.GetMetadataItem('COMPRESSION', 'IMAGE_STRUCTURE'))
            self.assertEqual(ds.GetMetadataItem('EXPORT_JPEG_QUALITY'), '85')
            self.assertEqual(ds.GetRasterBand(1).GetMaskBand().ReadAsArray()[0, 0], 0)
            ds.Close()

    def test_merge_uses_jpeg_after_colour_rendering(self):
        output = self.root / 'merged.tif'
        merge({**self.job, 'inputs': [str(self.source)], 'output': str(output), 'mode': 'shared'})
        ds = gdal.Open(str(output))
        self.assertIn('JPEG', ds.GetMetadataItem('COMPRESSION', 'IMAGE_STRUCTURE'))
        self.assertEqual(ds.RasterCount, 3)
        self.assertEqual(ds.GetRasterBand(1).GetMaskBand().ReadAsArray()[0, 0], 0)
        self.assertGreater(ds.GetRasterBand(1).GetOverviewCount(), 0)
        ds.Close()

    def test_jpeg_master_can_be_split_with_transparent_overview(self):
        source = gdal.Open(str(self.source))
        master = self.root / 'master-jpeg.tif'
        encoded = export_geotiff(source, master, self.job)
        encoded.Close()
        source.Close()
        job = {**self.job, 'inputs': [str(master)], 'workspace': str(self.root), 'action': 'export',
               'output': str(self.root / 'areas'), 'rows': 2, 'columns': 2, 'selected': [0],
               'create_overview': True, 'overview_resolution': 100}
        split(job)
        for name in ['area_row01_col01.tif', 'region_overview_100m.tif']:
            ds = gdal.Open(str(self.root / 'areas' / name))
            mask = ds.GetRasterBand(1).GetMaskBand().ReadAsArray()
            ds.Close()
            self.assertEqual(mask[0, 0], 0)
            self.assertEqual(mask[-1, -1], 255)

    def test_lossless_export_from_jpeg_keeps_mask_inside_tiff(self):
        source = gdal.Open(str(self.source))
        master = export_geotiff(source, self.root / 'master.tif', self.job)
        source.Close()
        target = self.root / 'lossless-from-jpeg.tif'
        result = export_geotiff(master, target, {'compression': 'DEFLATE'})
        result.Close()
        master.Close()
        result = gdal.Open(str(target))
        actual = result.GetRasterBand(1).GetMaskBand().ReadAsArray()
        result.Close()
        np.testing.assert_array_equal(actual, self.alpha)
        self.assertFalse(target.with_name(target.name + '.msk').exists())

    def test_raw_reflectance_is_rendered_before_jpeg_encoding(self):
        raw = self.root / 'reflectance.tif'
        source = gdal.Open(str(self.source))
        ds = gdal.Translate(str(raw), source, outputType=gdal.GDT_UInt16)
        source.Close()
        for b in [1, 2, 3]:
            ds.GetRasterBand(b).WriteArray(self.rgb[b - 1].astype(np.uint16) * 10)
            ds.GetRasterBand(b).SetScale(0.0001)
        ds.FlushCache()
        result = export_geotiff(ds, self.root / 'rendered-jpeg.tif', self.job)
        expected = np.clip((self.rgb.astype(float) * .001 - .005) * 255 / .395, 0, 255)
        error = np.abs(result.ReadAsArray().astype(float) - expected)
        self.assertLess(error[:, self.alpha > 0].mean(), 5)
        np.testing.assert_array_equal(result.GetRasterBand(1).GetMaskBand().ReadAsArray(), self.alpha)
        result.Close()
        ds.Close()

    def test_live_jpeg_writer_can_be_cropped_to_independent_lossless_output(self):
        source = gdal.Open(str(self.source))
        master_path = self.root / 'live-jpeg.tif'
        master = export_geotiff(source, master_path, self.job)
        source.Close()
        target = self.root / 'cropped-lossless.tif'
        result = export_geotiff(master, target, {'compression': 'DEFLATE'}, src_win=[32, 32, 300, 300])
        result.Close()
        master.Close()
        decoded = gdal.Open(str(master_path))
        expected_pixels = decoded.ReadAsArray()[:, 32:332, 32:332]
        decoded.Close()
        result = gdal.Open(str(target))
        np.testing.assert_array_equal(result.ReadAsArray(), expected_pixels)
        np.testing.assert_array_equal(result.GetRasterBand(1).GetMaskBand().ReadAsArray(), self.alpha[32:332, 32:332])
        result.Close()

    def test_qgis_renders_internal_mask_as_transparent(self):
        source = gdal.Open(str(self.source))
        output = self.root / 'masked.tif'
        result = export_geotiff(source, output, self.job)
        write_rgb_style(output, result)
        result.Close()
        source.Close()
        from qgis.core import QgsApplication, QgsRasterLayer, QgsMapSettings, QgsMapRendererParallelJob
        from qgis.PyQt.QtCore import QSize
        from qgis.PyQt.QtGui import QColor
        import mosaic_worker, os
        if mosaic_worker._qgis_app is None:
            QgsApplication.setPrefixPath(os.environ['QGIS_PREFIX_PATH'], True)
            mosaic_worker._qgis_app = QgsApplication([], False)
            mosaic_worker._qgis_app.initQgis()
        layer = QgsRasterLayer(str(output), 'mask check')
        print('QGIS internal-mask provider bands:', layer.bandCount(), 'renderer alpha:', layer.renderer().alphaBand())
        settings = QgsMapSettings()
        settings.setLayers([layer])
        settings.setDestinationCrs(layer.crs())
        settings.setExtent(layer.extent())
        settings.setOutputSize(QSize(512, 512))
        settings.setBackgroundColor(QColor(0, 0, 0, 0))
        render = QgsMapRendererParallelJob(settings)
        render.start()
        render.waitForFinished()
        image = render.renderedImage()
        first_alpha, second_alpha = image.pixelColor(10, 10).alpha(), image.pixelColor(200, 200).alpha()
        settings.setLayers([])
        render = layer = None
        self.assertEqual(first_alpha, 0)
        self.assertEqual(second_alpha, 255)


if __name__ == '__main__':
    unittest.main()
