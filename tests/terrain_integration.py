"""Offline elevation preparation, contours, hillshade and theme checks."""
from pathlib import Path
import sys,tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from osgeo import gdal,osr
from qgis.core import QgsApplication,QgsProject,QgsCoordinateReferenceSystem,QgsRectangle
from terrain import terrain_layers,automatic_dem,NODATA

gdal.UseExceptions()
app=QgsApplication([],False)
app.initQgis()
with tempfile.TemporaryDirectory(prefix='terrain-test-') as temporary:
    root=Path(temporary)
    source=root/'dem.tif'
    crs=osr.SpatialReference()
    crs.ImportFromEPSG(3857)
    dem=gdal.GetDriverByName('GTiff').Create(str(source),128,128,1,gdal.GDT_Float32)
    dem.SetProjection(crs.ExportToWkt())
    dem.SetGeoTransform((0,40,0,5120,0,-40))
    yy,xx=np.indices((128,128))
    heights=(500+200*np.sin(xx/12)*np.cos(yy/15)+xx*4).astype(np.float32)
    heights[10:20,10:20]=-9999
    dem.GetRasterBand(1).SetNoDataValue(-9999)
    dem.GetRasterBand(1).SetUnitType('m')
    dem.GetRasterBand(1).WriteArray(heights)
    dem.Close()
    extent=QgsRectangle(0,0,5120,5120)
    project=QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
    for style in ('Light','Dark'):
        folder=root/style
        folder.mkdir()
        job={'cancel':str(root/'cancel'),'bounds':{'west':0,'south':0,'east':.046,'north':.046},
             'terrain_enabled':True,'terrain_dem':str(source),'terrain_contours':True,
             'terrain_interval':50,'osm_style':style}
        layers=terrain_layers(folder,extent,project,job,lambda message:None)
        assert any(layer.name()=='Terrain relief' for layer in layers)
        assert any(layer.name()=='Index contours (m)' for layer in layers)
        shade=gdal.Open(str(folder/'terrain_relief.tif'))
        alpha=shade.GetRasterBand(4).ReadAsArray()
        assert np.count_nonzero(alpha)>100
        assert alpha[16,16]==0, 'DEM nodata must remain transparent'
        assert shade.GetGeoTransform()[1]>=40, 'Terrain must not be expanded to 2 m map pixels'
        shade=None
        assert all(layer.isValid() for layer in layers)
        project.clear()
    flat=root/'flat.tif'
    dem=gdal.GetDriverByName('GTiff').CreateCopy(str(flat),gdal.Open(str(source)))
    dem.GetRasterBand(1).Fill(200)
    dem.Close()
    folder=root/'flat-map'
    folder.mkdir()
    layers=terrain_layers(folder,extent,project,dict(job,terrain_dem=str(flat),terrain_contours=False),lambda message:None)
    shade=gdal.Open(str(folder/'terrain_relief.tif'))
    assert shade.GetRasterBand(4).ReadAsArray().max()<=2, 'Flat ground should not receive uniform grey shading'
    shade=None
    project.clear()
    # Terrarium decoding uses actual PNG channels without network access.
    tile=root/'tile.png'
    mem=gdal.GetDriverByName('MEM').Create('',256,256,3,gdal.GDT_Byte)
    for i,value in enumerate((128,123,128),1):
        mem.GetRasterBand(i).Fill(value)
    png=gdal.Translate(str(tile),mem,format='PNG')
    png=mem=None
    credits=root/'credits.md'
    credits.write_text('Test terrain credits')
    folder=root/'automatic'
    folder.mkdir()
    def resource(url,path,job,png=False):
        return tile if png else credits
    with patch('terrain.cached_resource',side_effect=resource):
        path,info=automatic_dem(folder,(100,100,200,200),{'cancel':str(root/'cancel')},lambda message:None)
    dem=gdal.Open(str(path))
    assert np.all(dem.ReadAsArray()==123.5)
    assert info['source']=='Mapzen Terrain Tiles on AWS'
    assert (folder/'TERRAIN_ATTRIBUTION.md').is_file()
    dem=None
    project.clear()
    layers=None
    QgsApplication.exitQgis()
print('Terrain integration passed: local DEM, contours, themes, nodata, neutral flat shading and automatic tile decoding.')
