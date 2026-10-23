"""Run under QGIS Python; synthetic offline data, no network requests."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from osgeo import gdal, osr
from hybrid_worker import run, inspect_imagery
from osm_fixtures import collections


with tempfile.TemporaryDirectory(prefix='hybrid-test-') as temporary:
    folder = Path(temporary)
    elements = [
        {'type': 'node', 'id': 1, 'lon': 0.01, 'lat': 51.01, 'tags': {'place': 'town', 'name': 'Test Town'}},
        {'type': 'way', 'id': 2, 'tags': {'highway': 'primary', 'name': 'Main Road'},
         'geometry': [{'lon': 0.001, 'lat': 51.005}, {'lon': 0.019, 'lat': 51.005}]},
        {'type': 'way', 'id': 3, 'tags': {'highway': 'residential', 'name': 'Residential Street'},
         'geometry': [{'lon': 0.001, 'lat': 51.015}, {'lon': 0.019, 'lat': 51.015}]},
        {'type': 'way', 'id': 4, 'tags': {'highway': 'tertiary', 'ref': 'B1234', 'name': 'Numbered Road'},
         'geometry': [{'lon': 0.001, 'lat': 51.012}, {'lon': 0.019, 'lat': 51.012}]},
    ]
    for name, data in zip(('roads', 'places'), collections(elements)):
        (folder / f'{name}.geojson').write_text(json.dumps(data), encoding='utf-8')
    source = folder / 'satellite.tif'
    ds = gdal.GetDriverByName('GTiff').Create(str(source), 400, 400, 3, gdal.GDT_Byte)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    ds.SetGeoTransform((0, 0.00005, 0, 51.02, 0, -0.00005))
    for band, value in enumerate((40, 70, 50), 1):
        ds.GetRasterBand(band).Fill(value)
    ds = None
    bounds = inspect_imagery(source)
    assert abs(bounds['west']) < 1e-8
    assert abs(bounds['north'] - 51.02) < 1e-8
    assert abs(bounds['east'] - 0.02) < 0.0001
    assert abs(bounds['south'] - 51) < 0.0001
    # Projected input coverage is converted to longitude/latitude, not reused as degrees.
    projected_path = folder / 'projected.tif'
    projected = gdal.Warp(str(projected_path), str(source), dstSRS='EPSG:3857')
    projected = None
    projected_bounds = inspect_imagery(projected_path)
    assert abs(projected_bounds['west']) < 0.0001
    assert abs(projected_bounds['north'] - 51.02) < 0.0001
    assert abs(projected_bounds['east'] - 0.02) < 0.0002
    job = {'folder': str(folder), 'bounds': {'west': 0, 'south': 51, 'east': 0.02, 'north': 51.02},
           'resolution': 5, 'cancel': str(folder / 'cancel'), 'imagery': str(source)}
    run(job)
    overlay = gdal.Open(str(folder / 'overlay.tif'))
    hybrid = gdal.Open(str(folder / 'hybrid.tif'))
    assert overlay.RasterCount == 4
    assert overlay.GetGeoTransform() == hybrid.GetGeoTransform()
    assert overlay.GetGeoTransform()[1] == 5
    assert overlay.GetSpatialRef().GetAuthorityCode(None) == '3857'
    pixels = overlay.ReadAsArray()
    hybrid_pixels = hybrid.ReadAsArray()
    assert np.any(pixels[3] == 0), 'Transparent background missing'
    assert np.any(pixels[3, :-30] > 0), 'Roads / labels missing'
    assert np.any(pixels[:3, :-30] > 220), 'Bright labels / roads missing'
    assert (folder / 'hybrid.qgz').is_file()
    assert (folder / 'overlay.qml').is_file()
    if len(sys.argv) > 1:
        preview = gdal.Translate(sys.argv[1], hybrid, format='PNG')
        preview = None
    overlay = hybrid = None
    from qgis.core import QgsProject, QgsVectorLayer
    project = QgsProject.instance()
    project.clear()
    assert project.read(str(folder / 'hybrid.qgz'))
    assert len(project.mapLayers()) == 7
    assert all(layer.isValid() for layer in project.mapLayers().values())
    assert any(layer.labelsEnabled() for layer in project.mapLayers().values() if hasattr(layer, 'labelsEnabled'))
    from hybrid_style import ROAD_GROUPS
    assert not any(layer.name() == ROAD_GROUPS['local'] for layer in project.mapLayers().values())
    b_roads = next(layer for layer in project.mapLayers().values() if layer.name() == ROAD_GROUPS['b_road'])
    assert [f['ref'] for f in b_roads.getFeatures()] == ['B1234']
    project.clear()
    custom_folder = folder / 'custom'
    custom_folder.mkdir()
    for name in ('roads.geojson', 'places.geojson'):
        (custom_folder / name).write_bytes((folder / name).read_bytes())
    custom_job = dict(job, folder=str(custom_folder), render_rasters=False,
                      road_groups=['local'], road_labels='Off', place_types=[])
    run(custom_job)
    project.clear()
    assert project.read(str(custom_folder / 'hybrid.qgz'))
    vector_layers = [layer for layer in project.mapLayers().values() if isinstance(layer, QgsVectorLayer)]
    assert len(vector_layers) == 1
    assert not vector_layers[0].labelsEnabled()
    assert [f['name'] for f in vector_layers[0].getFeatures()] == ['Residential Street']
    project.clear()
    try:
        run(job)
        raise AssertionError('Existing outputs were not protected')
    except FileExistsError:
        pass
    # Vector-only mode never allocates raster pixels, even at very fine spacing.
    vector_folder = folder / 'vectors'
    vector_folder.mkdir()
    for name in ('roads.geojson', 'places.geojson'):
        (vector_folder / name).write_bytes((folder / name).read_bytes())
    vector_job = dict(job, folder=str(vector_folder), resolution=0.01, render_rasters=False)
    run(vector_job)
    assert (vector_folder / 'hybrid.qgz').is_file()
    assert not (vector_folder / 'overlay.tif').exists()
    project.clear()
    assert project.read(str(vector_folder / 'hybrid.qgz'))
    assert all(layer.isValid() for layer in project.mapLayers().values())
    project.clear()
    # Exceed the old per-edge limit with a shallow map: blocks must still produce
    # a single full-resolution hybrid with imagery across all block boundaries.
    wide = folder / 'wide'
    wide.mkdir()
    for name in ('roads.geojson', 'places.geojson'):
        (wide / name).write_bytes((folder / name).read_bytes())
    wide_source = folder / 'wide-source.tif'
    ds = gdal.GetDriverByName('GTiff').Create(str(wide_source), 2, 2, 3, gdal.GDT_Byte)
    ds.SetProjection(srs.ExportToWkt())
    ds.SetGeoTransform((0, 0.4, 0, 51.006, 0, -0.003))
    for band, value in enumerate((40, 70, 50), 1):
        ds.GetRasterBand(band).Fill(value)
    ds = None
    wide_job = dict(job, folder=str(wide), imagery=str(wide_source),
                    bounds={'west': 0, 'south': 51, 'east': 0.8, 'north': 51.006})
    run(wide_job)
    raster = gdal.Open(str(wide / 'hybrid.tif'))
    assert raster.RasterXSize > 16384
    assert raster.GetGeoTransform()[1] == 5
    assert raster.GetMetadataItem('EXPORT_RESOLUTION_M') == '5.0'
    rgb = raster.ReadAsArray()
    # No transparent seams or missing imagery at 4096-pixel render boundaries.
    for boundary in (4096, 8192, 12288, 16384):
        assert np.all(rgb[3, 5:100, boundary - 1:boundary + 2] == 255)
        assert np.array_equal(rgb[:3, 5:100, boundary - 1], rgb[:3, 5:100, boundary + 1])
    raster = None
    assert (wide / 'overlay.tif').is_file()
    assert (wide / 'hybrid.qml').is_file()
    project.clear()
    # Exercise both dimensions and a final block shorter than the attribution strip.
    tiled = folder / 'tiled'
    tiled.mkdir()
    for name in ('roads.geojson', 'places.geojson'):
        (tiled / name).write_bytes((folder / name).read_bytes())
    run(dict(job, folder=str(tiled), render_block_size=100))
    raster = gdal.Open(str(tiled / 'hybrid.tif'))
    tiled_pixels = raster.ReadAsArray()
    assert tiled_pixels.shape == hybrid_pixels.shape
    for boundary in (100, 200, 300, 400):
        assert np.array_equal(tiled_pixels[:, 5:50, boundary - 1:boundary + 2],
                              hybrid_pixels[:, 5:50, boundary - 1:boundary + 2])
    for boundary in (100, 200, 300, 400, 500, 600, 700):
        assert np.array_equal(tiled_pixels[:, boundary - 1:boundary + 2, 5:10],
                              hybrid_pixels[:, boundary - 1:boundary + 2, 5:10])
    assert np.array_equal(tiled_pixels[:, -24:], hybrid_pixels[:, -24:]), 'Attribution strip was cut at a render-block boundary'
    raster = None
    project.clear()
    cancelled = folder / 'cancelled'
    cancelled.mkdir()
    for name in ('roads.geojson', 'places.geojson'):
        (cancelled / name).write_bytes((folder / name).read_bytes())
    from unittest.mock import patch
    cancel_path = cancelled / 'cancel'
    def cancel_on_second_block(message):
        if message.startswith('Rendering overlay block 2/'):
            cancel_path.touch()
    with patch('hybrid_worker.emit', side_effect=cancel_on_second_block):
        try:
            run(dict(job, folder=str(cancelled), render_block_size=100, cancel=str(cancel_path)))
            raise AssertionError('Cancellation was ignored')
        except InterruptedError:
            pass
    assert (cancelled / 'hybrid.qgz').exists()
    assert not (cancelled / 'overlay.tif').exists()
    assert not list(cancelled.glob('.*.part.tif'))
    assert not list(cancelled.glob('.*.block.png'))
    project.clear()
print('Hybrid integration passed: georeferencing, alpha, rendering, project reload.')
