"""QGIS checks: inherit satellite JPEG encoding; keep the vector overlay lossless."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from osgeo import gdal, osr
from hybrid_worker import run, inspect_imagery
from qgis.core import QgsProject

gdal.UseExceptions()
with tempfile.TemporaryDirectory(prefix='hybrid-jpeg-test-') as temporary:
    root = Path(temporary)
    raw = root / 'raw.tif'
    source = root / 'satellite-jpeg.tif'
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    ds = gdal.GetDriverByName('GTiff').Create(str(raw), 512, 512, 4, gdal.GDT_Byte)
    ds.SetProjection(srs.ExportToWkt())
    ds.SetGeoTransform((0, 5, 0, 2560, 0, -5))
    yy, xx = np.indices((512, 512))
    noise = np.random.default_rng(42).normal(0, 7, (512, 512))
    base = np.clip(60 + xx / 5 + yy / 8 + noise, 0, 255).astype(np.uint8)
    for i, scale in enumerate((1, .9, .7), 1):
        ds.GetRasterBand(i).WriteArray((base * scale).astype(np.uint8))
    alpha = np.full((512, 512), 255, dtype=np.uint8)
    alpha[:80, :80] = 0
    ds.GetRasterBand(4).WriteArray(alpha)
    ds.GetRasterBand(4).SetColorInterpretation(gdal.GCI_AlphaBand)
    ds = None
    gdal.SetConfigOption('GDAL_TIFF_INTERNAL_MASK', 'YES')
    ds = gdal.Translate(str(source), str(raw), bandList=[1, 2, 3], maskBand=4,
                        creationOptions=['TILED=YES', 'COMPRESS=JPEG', 'JPEG_QUALITY=85', 'PHOTOMETRIC=YCBCR'])
    ds = None
    gdal.SetConfigOption('GDAL_TIFF_INTERNAL_MASK', None)
    job = {'imagery': str(source), 'resolution': 5, 'bounds': inspect_imagery(source), 'cancel': str(root / 'cancel'),
           'compression': 'SOURCE', 'render_block_size': 256}
    empty = json.dumps({'type': 'FeatureCollection', 'features': []})
    for name, compression in [('lossless', 'DEFLATE'), ('matched', 'SOURCE')]:
        folder = root / name
        folder.mkdir()
        for layer in ('roads', 'places'):
            (folder / f'{layer}.geojson').write_text(empty, encoding='utf-8')
        run(dict(job, folder=str(folder), compression=compression))
        QgsProject.instance().clear()
    lossless = gdal.Open(str(root / 'lossless/hybrid.tif'))
    matched = gdal.Open(str(root / 'matched/hybrid.tif'))
    overlay = gdal.Open(str(root / 'matched/overlay.tif'))
    assert matched.RasterCount == 3
    assert 'JPEG' in matched.GetMetadataItem('COMPRESSION', 'IMAGE_STRUCTURE')
    assert matched.GetMetadataItem('EXPORT_JPEG_QUALITY') == '85'
    assert matched.GetRasterBand(1).GetMaskFlags() == gdal.GMF_PER_DATASET
    assert matched.GetGeoTransform() == lossless.GetGeoTransform()
    assert matched.RasterXSize == lossless.RasterXSize and matched.RasterYSize == lossless.RasterYSize
    assert overlay.RasterCount == 4 and overlay.GetMetadataItem('COMPRESSION', 'IMAGE_STRUCTURE') == 'DEFLATE'
    expected_mask = (lossless.GetRasterBand(4).ReadAsArray() > 0).astype(np.uint8) * 255
    np.testing.assert_array_equal(matched.GetRasterBand(1).GetMaskBand().ReadAsArray(), expected_mask)
    rgb = lossless.ReadAsArray()[:3].astype(float)
    error = np.abs(matched.ReadAsArray().astype(float) - rgb)
    assert error[:, expected_mask > 0].mean() < 8
    assert (root / 'matched/hybrid.tif').stat().st_size < (root / 'lossless/hybrid.tif').stat().st_size
    assert not (root / 'matched/hybrid.tif.msk').exists()
    assert 'alphaBand="4"' in (root / 'matched/hybrid.qml').read_text()
    print('Hybrid JPEG checks passed. File sizes:', (root / 'lossless/hybrid.tif').stat().st_size,
          (root / 'matched/hybrid.tif').stat().st_size)
    lossless = matched = overlay = None
