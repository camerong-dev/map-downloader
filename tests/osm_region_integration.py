"""Whole-map cropping, holes and islands in lossless and JPEG exports."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from osgeo import gdal
from qgis.core import QgsApplication,QgsProject,QgsCoordinateReferenceSystem,QgsRectangle,QgsMapSettings
from qgis.PyQt.QtGui import QColor
from osm_region import export_region
from hybrid_worker import render_raster

app=QgsApplication([],False)
app.initQgis()
gdal.UseExceptions()
project=QgsProject.instance()
project.setCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
coverage={'type':'FeatureCollection','crs':{'type':'name','properties':{'name':'EPSG:3857'}},'features':[
    {'type':'Feature','properties':{},'geometry':{'type':'Polygon','coordinates':[
        [[500,800],[2500,800],[2500,2000],[3500,2000],[3500,3200],[500,3200],[500,800]],
        [[1000,1400],[1000,1800],[1400,1800],[1400,1400],[1000,1400]]]}},
    {'type':'Feature','properties':{},'geometry':{'type':'Polygon','coordinates':[
        [[3000,900],[3400,900],[3400,1300],[3000,1300],[3000,900]]]}}
]}
with tempfile.TemporaryDirectory(prefix='osm-region-test-') as temporary:
    root=Path(temporary)
    for compression in ('DEFLATE','JPEG'):
        folder=root/compression
        folder.mkdir()
        (folder/'terrain-coverage.geojson').write_text(json.dumps(coverage))
        job={'cancel':str(root/'cancel'),'compression':compression,'jpeg_quality':90,'render_block_size':128}
        extent=export_region(folder,{},QgsRectangle(0,0,4000,4000),10,project,job,lambda _:None)
        assert extent==QgsRectangle(500,800,3500,3200)
        settings=QgsMapSettings()
        settings.setDestinationCrs(project.crs())
        settings.setTransformContext(project.transformContext())
        settings.setBackgroundColor(QColor('#f7f5ef'))
        settings.setOutputDpi(96)
        render_raster('osm',[],settings,extent,300,240,10,folder,job)
        dataset=gdal.Open(str(folder/'osm.tif'))
        assert (dataset.RasterXSize,dataset.RasterYSize)==(300,240), 'Export still contains excess margins'
        assert dataset.GetGeoTransform()==(500,10,0,3200,0,-10)
        mask=dataset.GetRasterBand(1).GetMaskBand().ReadAsArray()
        for x,y,expected in ((800,2000,255),(3200,1100,255),(1200,1600,0),(2800,1600,0),(3450,850,0)):
            assert mask[int((3200-y)/10),int((x-500)/10)]==expected,(compression,x,y)
        assert mask.min()==0 and mask.max()==255
        assert dataset.GetRasterBand(1).GetOverviewCount()>0
        dataset=None
    project.clear()
    app.exitQgis()
print('OSM region integration passed: cropped grid, transparency, holes, islands, block seams and JPEG mask.')
