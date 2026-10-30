"""Global ocean polygons preserve harbour inlets and islands without regional coasts."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from osgeo import gdal
from qgis.core import (QgsApplication, QgsProject, QgsCoordinateReferenceSystem,
                       QgsRectangle, QgsGeometry, QgsPointXY)
from hybrid_worker import coastline_water

app = QgsApplication([], False)
app.initQgis()
gdal.UseExceptions()
project = QgsProject.instance()
project.setCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
with tempfile.TemporaryDirectory(prefix='coast-test-') as temporary:
    folder = Path(temporary)
    # Full coastline already assembled upstream, including a narrow inlet.
    clip = QgsGeometry.fromRect(QgsRectangle(0,0,1000,1000))
    land = QgsGeometry.fromWkt('POLYGON((600 0,1000 0,1000 1000,600 1000,600 650,850 650,850 350,600 350,600 0))')
    island = QgsGeometry.fromRect(QgsRectangle(200,250,550,750))
    inlet_island = QgsGeometry.fromRect(QgsRectangle(700,450,750,550))
    geometry = clip.difference(land).difference(island).difference(inlet_island)
    source = folder / 'global.geojson'
    source.write_text(json.dumps({'type':'FeatureCollection',
        'crs':{'type':'name','properties':{'name':'EPSG:3857'}}, 'features':[
            {'type':'Feature','properties':{},'geometry':json.loads(geometry.asJson())}]}))
    # No regional PBF coastline is required; in particular open county boundaries
    # cannot cause either an inland flood or an empty harbour.
    with patch('ocean.prepare_ocean_source',return_value=source) as prepare:
        layer = coastline_water(folder, project, QgsRectangle(0,0,1000,1000),
            {'cancel':str(folder/'cancel')})
        assert layer is not None
        sea = QgsGeometry.unaryUnion([f.geometry() for f in layer.getFeatures()])
        for x,y in [(50,500),(575,500),(650,500),(800,500),(575,800)]:
            assert sea.contains(QgsPointXY(x,y)), ('Missing sea/harbour', x,y)
        for x,y in [(400,500),(725,500),(900,500),(650,200),(650,800)]:
            assert not sea.contains(QgsPointXY(x,y)), ('Flooded land', x,y)
        assert (folder/'sea.fgb').is_file()
        assert json.loads((folder/'coastline-info.json').read_text())['license'] == 'ODbL'
        project.clear()
        layer = None
        # Inland maps with no ocean intersections remain land, using an empty
        # schema-bearing FGB without inventing sea around the output rectangle.
        inland = folder/'inland'
        inland.mkdir()
        layer = coastline_water(inland, project, QgsRectangle(900,100,950,200),
            {'cancel':str(folder/'cancel')})
        assert layer is None
        assert json.loads((inland/'coastline-info.json').read_text())['features'] == 0
        (folder/'cancel').touch()
        try:
            coastline_water(folder,project,QgsRectangle(0,0,1000,1000),{'cancel':str(folder/'cancel')})
            raise AssertionError('Cancellation ignored')
        except InterruptedError:
            pass
        assert prepare.call_count == 2
    project.clear()
    app.exitQgis()
print('Coastline integration passed: ocean, inlet, islands, inland area and cancellation.')
