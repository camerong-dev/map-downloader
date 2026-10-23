"""Run under QGIS Python: offline OSM import, cache reuse and renderer parity."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from osgeo import gdal
from geofabrik_worker import extract, import_extract
from hybrid_worker import run
from osm_download import collections
from qgis.core import QgsProject

with tempfile.TemporaryDirectory(prefix='geofabrik-test-') as temporary:
    root = Path(temporary)
    source = root / 'region.osm'
    source.write_text('''<osm version="0.6" generator="test">
<node id="1" lat="51.01" lon="0.01"><tag k="name" v="Test Town"/><tag k="place" v="town"/></node>
<node id="2" lat="51.005" lon="-0.01"/><node id="3" lat="51.005" lon="0.03"/>
<node id="4" lat="51.012" lon="0.001"/><node id="5" lat="51.012" lon="0.019"/>
<node id="6" lat="51.015" lon="0.001"/><node id="7" lat="51.015" lon="0.019"/>
<node id="8" lat="53" lon="1"/><node id="9" lat="53" lon="2"/>
<node id="10" lat="53" lon="1.5"><tag k="name" v="Outside Town"/><tag k="place" v="town"/></node>
<way id="20"><nd ref="2"/><nd ref="3"/><tag k="highway" v="primary"/><tag k="ref" v="A27"/><tag k="name" v="Main Road"/></way>
<way id="21"><nd ref="4"/><nd ref="5"/><tag k="highway" v="tertiary"/><tag k="ref" v="B1234"/><tag k="name" v="Numbered Road"/></way>
<way id="22"><nd ref="6"/><nd ref="7"/><tag k="highway" v="residential"/><tag k="name" v="Residential Street"/></way>
<way id="23"><nd ref="8"/><nd ref="9"/><tag k="highway" v="primary"/><tag k="name" v="Outside Road"/></way>
</osm>''', encoding='utf-8')
    cache = root / 'cache'
    cache.mkdir()
    folder = root / 'output'
    folder.mkdir()
    job = {'source': str(source), 'cache_dir': str(cache), 'folder': str(folder),
           'bounds': {'west': 0, 'south': 51, 'east': 0.02, 'north': 51.02},
           'cancel': str(root / 'cancel')}
    extract(job)
    roads = json.loads((folder / 'roads.geojson').read_text(encoding='utf-8'))['features']
    places = json.loads((folder / 'places.geojson').read_text(encoding='utf-8'))['features']
    assert len(roads) == 3 and len(places) == 1
    by_id = {f['properties']['osm_id']: f for f in roads}
    assert by_id['way/20']['properties']['ref'] == 'A27'
    assert by_id['way/21']['properties']['road_group'] == 'b_road'
    assert by_id['way/22']['properties']['road_group'] == 'local'
    assert places[0]['properties']['name'] == 'Test Town'
    # A road crossing the bbox is retained even when its endpoints are outside.
    assert by_id['way/20']['geometry']['coordinates'][0][0] < 0
    cached = import_extract(source, cache, job)
    modified = cached.stat().st_mtime_ns
    assert import_extract(source, cache, job).stat().st_mtime_ns == modified
    render_job = dict(job, resolution=5)
    run(render_job)
    project = QgsProject.instance()
    assert all(layer.isValid() for layer in project.mapLayers().values())
    assert not any(layer.name() == 'Minor / residential / service roads' for layer in project.mapLayers().values())
    assert (folder / 'overlay.tif').is_file() and (folder / 'hybrid.qgz').is_file()
    raster = gdal.Open(str(folder / 'overlay.tif'))
    assert raster.RasterCount == 4 and raster.GetGeoTransform()[1] == 5
    imported_pixels = raster.ReadAsArray()
    raster = None
    project.clear()
    # The data adapter must leave the renderer's appearance unchanged for identical data.
    reference = root / 'reference'
    reference.mkdir()
    elements = [{'type': 'node', 'id': 1, 'lon': 0.01, 'lat': 51.01,
                 'tags': {'name': 'Test Town', 'place': 'town'}}]
    for ident, tags, y, x1, x2 in [
        (20, {'highway': 'primary', 'ref': 'A27', 'name': 'Main Road'}, 51.005, -0.01, 0.03),
        (21, {'highway': 'tertiary', 'ref': 'B1234', 'name': 'Numbered Road'}, 51.012, 0.001, 0.019),
        (22, {'highway': 'residential', 'name': 'Residential Street'}, 51.015, 0.001, 0.019)]:
        elements.append({'type': 'way', 'id': ident, 'tags': tags,
                         'geometry': [{'lon': x1, 'lat': y}, {'lon': x2, 'lat': y}]})
    for name, collection in zip(('roads', 'places'), collections(elements)):
        (reference / f'{name}.geojson').write_text(json.dumps(collection), encoding='utf-8')
    run(dict(render_job, folder=str(reference)))
    raster = gdal.Open(str(reference / 'overlay.tif'))
    import numpy as np
    assert np.array_equal(imported_pixels, raster.ReadAsArray()), 'Data source changed the hybrid rendering'
    raster = None
    project.clear()
    # Repeat with a smaller split extent: the regional database is reused.
    split = root / 'split'
    split.mkdir()
    split_job = dict(job, folder=str(split), bounds={'west': 0, 'south': 51, 'east': 0.02, 'north': 51.011})
    extract(split_job)
    assert len(json.loads((split / 'roads.geojson').read_text())['features']) == 1
    assert cached.stat().st_mtime_ns == modified
    cancel = root / 'cancel'
    cancel.touch()
    try:
        import_extract(source, cache, job)
        # Cache reuse itself need not read data; extraction checks cancellation first.
        extract(split_job)
        raise AssertionError('Cancellation was ignored')
    except InterruptedError:
        pass
print('Geofabrik integration passed: fields, filters, cached import, styling, split bounds and cancellation.')
