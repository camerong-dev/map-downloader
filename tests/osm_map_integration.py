"""Offline pure-OSM import, opaque cartography, quality and project checks."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from osgeo import gdal
from qgis.core import QgsProject, QgsCoordinateReferenceSystem, QgsCoordinateTransform
from geofabrik_worker import extract, import_extract
from hybrid_worker import run

with tempfile.TemporaryDirectory(prefix='osm-map-test-') as temporary:
    root = Path(temporary)
    source = root / 'region.osm'
    nodes = [
        (1, .01, 51.01), (2, .001, 51.005), (3, .019, 51.005),
        (10, .002, 51.012), (11, .008, 51.012), (12, .008, 51.018), (13, .002, 51.018),
        (20, .012, 51.012), (21, .018, 51.012), (22, .018, 51.018), (23, .012, 51.018),
        (30, .003, 51.002), (31, .005, 51.002), (32, .005, 51.004), (33, .003, 51.004),
        (40, .001, 51.008), (41, .019, 51.008),
        (50, .011, 51.03), (51, .011, 50.99),
        (60, .0005, 51.010), (61, .0025, 51.010), (62, .0025, 51.012), (63, .0005, 51.012),
    ]
    parts = ['<osm version="0.6" generator="test">']
    for ident, lon, lat in nodes:
        tags = '<tag k="place" v="town"/><tag k="name" v="Local Town"/><tag k="name:en" v="English Town"/>' if ident == 1 else ''
        parts.append(f'<node id="{ident}" lon="{lon}" lat="{lat}">{tags}</node>')
    for ident, refs, tags in [
        (100, [2, 3], {'highway': 'primary', 'name': 'High Street'}),
        (108, [2, 3], {'highway': 'residential', 'name': 'Minor Street'}),
        (101, [10, 11, 12, 13, 10], {'natural': 'water', 'name': 'Lake'}),
        (102, [20, 21, 22, 23, 20], {'landuse': 'forest'}),
        (103, [30, 31, 32, 33, 30], {'building': 'yes'}),
        (104, [40, 41], {'waterway': 'river'}),
        (105, [2, 3], {'railway': 'rail'}),
        (106, [50, 51], {'natural': 'coastline'}),
        (107, [60, 61, 62, 63, 60], {'natural': 'coastline'}),
    ]:
        parts.append(f'<way id="{ident}">' + ''.join(f'<nd ref="{ref}"/>' for ref in refs)
                     + ''.join(f'<tag k="{key}" v="{value}"/>' for key, value in tags.items()) + '</way>')
    parts.append('</osm>')
    source.write_text(''.join(parts), encoding='utf-8')
    folder = root / 'map'
    folder.mkdir()
    job = {'sources': [str(source)], 'folder': str(folder), 'cache_dir': str(root / 'cache'),
           'bounds': {'west': 0, 'south': 51, 'east': .02, 'north': 51.02},
           'cancel': str(root / 'cancel'), 'map_mode': 'osm', 'resolution': 2, 'compression': 'DEFLATE'}
    extract(job)
    areas = json.loads((folder / 'map_areas.geojson').read_text())['features']
    assert len(areas) == 3
    assert {f['properties']['osm_id'] for f in areas} == {'way/101', 'way/102', 'way/103'}
    assert len(json.loads((folder / 'map_lines.geojson').read_text())['features']) == 4
    database = import_extract(source, root / 'cache', job)
    stamp = database.stat().st_mtime_ns
    assert import_extract(source, root / 'cache', job).stat().st_mtime_ns == stamp
    run(job)
    assert (folder / 'osm.tif').is_file() and (folder / 'osm.qgz').is_file()
    assert not (folder / 'hybrid.tif').exists() and not (folder / 'overlay.tif').exists()
    ds = gdal.Open(str(folder / 'osm.tif'))
    assert ds.GetSpatialRef().GetAuthorityCode(None) == '3857'
    assert ds.GetGeoTransform()[1] == 2
    assert ds.GetRasterBand(1).GetOverviewCount() > 0
    data = ds.ReadAsArray()
    assert np.all(data[3] == 255), 'Pure OSM must have an opaque map background'
    transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'),
                                       QgsCoordinateReferenceSystem('EPSG:3857'), QgsProject.instance())
    gt = ds.GetGeoTransform()
    def colour(lon, lat):
        point = transform.transform(lon, lat)
        x, y = int((point.x()-gt[0])/gt[1]), int((point.y()-gt[3])/gt[5])
        return data[:3, y, x]
    # Major road fill must remain above an overlapping minor street.
    np.testing.assert_array_equal(colour(.018, 51.005), [245, 201, 139])
    np.testing.assert_array_equal(colour(.005, 51.016), [168, 206, 224])
    np.testing.assert_array_equal(colour(.015, 51.016), [186, 211, 177])
    np.testing.assert_array_equal(colour(.004, 51.003), [211, 198, 184])
    np.testing.assert_array_equal(colour(.001, 51.019), [168, 206, 224])
    np.testing.assert_array_equal(colour(.019, 51.019), [247, 245, 239])
    np.testing.assert_array_equal(colour(.0015, 51.011), [247, 245, 239])
    if len(sys.argv) > 1:
        preview = gdal.Translate(sys.argv[1], ds, format='PNG')
        preview = None
    ds = None
    project = QgsProject.instance()
    project.clear()
    assert project.read(str(folder / 'osm.qgz'))
    assert all(layer.isValid() for layer in project.mapLayers().values())
    ordered = [layer.name() for layer in project.layerTreeRoot().customLayerOrder()]
    assert ordered.index('A roads / major roads') < ordered.index('Minor / residential / service roads')
    assert any(layer.name() == 'Water' for layer in project.mapLayers().values())
    assert not any(layer.name() == 'Satellite imagery' for layer in project.mapLayers().values())
    project.clear()
    try:
        run(job)
        raise AssertionError('Existing OSM output was overwritten')
    except FileExistsError:
        pass
    jpeg_folder = root / 'jpeg'
    jpeg_folder.mkdir()
    for name in ('roads', 'places', 'map_lines', 'map_areas'):
        (jpeg_folder / f'{name}.geojson').write_bytes((folder / f'{name}.geojson').read_bytes())
    run(dict(job, folder=str(jpeg_folder), resolution=10, compression='JPEG', jpeg_quality=90))
    jpeg = gdal.Open(str(jpeg_folder / 'osm.tif'))
    assert jpeg.RasterCount == 3 and jpeg.GetGeoTransform()[1] == 10
    assert 'JPEG' in jpeg.GetMetadataItem('COMPRESSION', 'IMAGE_STRUCTURE')
    assert np.all(jpeg.GetRasterBand(1).GetMaskBand().ReadAsArray() == 255)
    jpeg = None
    project.clear()
    dark_folder = root / 'dark'
    dark_folder.mkdir()
    for name in ('roads', 'places', 'map_lines', 'map_areas'):
        (dark_folder / f'{name}.geojson').write_bytes((folder / f'{name}.geojson').read_bytes())
    run(dict(job, folder=str(dark_folder), osm_style='Dark'))
    dark = gdal.Open(str(dark_folder / 'osm.tif'))
    assert dark.GetMetadataItem('OSM_MAP_STYLE') == 'Dark'
    data = dark.ReadAsArray()
    gt = dark.GetGeoTransform()
    assert np.all(data[3] == 255)
    np.testing.assert_array_equal(colour(.001, 51.019), [16, 45, 66])
    np.testing.assert_array_equal(colour(.019, 51.019), [21, 28, 36])
    np.testing.assert_array_equal(colour(.004, 51.003), [52, 65, 77])
    np.testing.assert_array_equal(colour(.018, 51.005), [204, 170, 118])
    dark = None
    project.clear()
    assert project.read(str(dark_folder / 'osm.qgz'))
    assert project.backgroundColor().name() == '#151c24'
    town = next(layer for layer in project.mapLayers().values() if layer.name() == 'Town')
    text = town.labeling().settings().format()
    assert text.color().lightness() > 150 and text.buffer().color().lightness() < 80
    project.clear()
    from qgis.core import QgsApplication
    QgsApplication.exitQgis()
    print('Pure OSM integration passed: import, cache, buildings, water, woodland, opaque TIFF, georeferencing and project reload.')
