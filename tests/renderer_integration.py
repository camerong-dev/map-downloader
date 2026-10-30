"""QGIS checks for exact direct pixel transfer and indexed map sources."""
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from osgeo import gdal, ogr
gdal.UseExceptions()
from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QImage, QColor
from hybrid_worker import write_rendered_image, prepare_render_data

app = QgsApplication([], False)
app.initQgis()
with tempfile.TemporaryDirectory(prefix='renderer-test-') as temporary:
    root = Path(temporary)
    for width in (1, 7, 128):
        image = QImage(width, 11, QImage.Format.Format_ARGB32_Premultiplied)
        for y in range(11):
            for x in range(width):
                image.setPixelColor(x, y, QColor((x*17)%256, (y*23)%256, 173, (0, 1, 64, 128, 254, 255)[(x+y)%6]))
        path = root / 'reference.png'
        assert image.save(str(path))
        reference = gdal.Open(str(path))
        expected = reference.ReadAsArray()
        for jpeg in (False, True):
            result = gdal.GetDriverByName('MEM').Create('', width+4, 15, 3 if jpeg else 4, gdal.GDT_Byte)
            if jpeg:
                result.CreateMaskBand(gdal.GMF_PER_DATASET)
            write_rendered_image(result, image, 2, 3, jpeg)
            actual = result.ReadAsArray()[:, 3:14, 2:width+2]
            np.testing.assert_array_equal(actual, expected[:3] if jpeg else expected)
            if jpeg:
                np.testing.assert_array_equal(result.GetRasterBand(1).GetMaskBand().ReadAsArray()[3:14, 2:width+2], (expected[3]>0).astype(np.uint8)*255)
            result = None
        reference = None
    import json
    feature = {'type': 'Feature', 'properties': {'name': 'Road'}, 'geometry': {'type': 'LineString', 'coordinates': [[0, 51], [.1, 51.1]]}}
    for name in ('roads', 'places'):
        (root / f'{name}.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': [feature] if name == 'roads' else []}))
    sources = prepare_render_data(root, {'cancel': str(root/'cancel')})
    assert sources['roads'].endswith('roads.fgb')
    database = gdal.OpenEx(str(root/'roads.fgb'), gdal.OF_VECTOR)
    layer = database.GetLayer(0)
    assert layer.TestCapability(ogr.OLCFastSpatialFilter)
    layer.SetSpatialFilterRect(5, 5, 6, 6)
    assert layer.GetFeatureCount() == 0
    layer = database = None
    assert sources['places'].endswith('places.geojson')
    assert not (root/'.map_data.part.gpkg').exists()
app.exitQgis()
print('Renderer integration passed: exact RGBA transfer, transparent edges, JPEG masks, spatial indexing and empty layers.')
