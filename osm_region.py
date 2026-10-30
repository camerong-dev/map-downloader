"""Shared OSM export footprint, cropped grid and bounded raster masks."""
import json
import math


def export_region(folder, sources, extent, resolution, project, job, emit):
    from qgis.core import QgsVectorLayer, QgsGeometry, QgsCoordinateTransform, QgsRectangle
    from terrain import feature_coverage, check_cancel
    coverage = folder/'terrain-coverage.geojson'
    if not coverage.exists():
        coverage = feature_coverage(folder,sources,job,emit)
    layer = QgsVectorLayer(str(coverage),'OSM region boundary','ogr')
    if not layer.isValid():
        raise ValueError('Cannot read the OSM region boundary.')
    transform = QgsCoordinateTransform(layer.crs(),project.crs(),project)
    geometries = []
    for feature in layer.getFeatures():
        check_cancel(job)
        geometry = feature.geometry()
        geometry.transform(transform)
        geometries.append(geometry)
    geometry = QgsGeometry.unaryUnion(geometries).intersection(QgsGeometry.fromRect(extent))
    if geometry.isEmpty() or geometry.area() <= 0:
        raise ValueError('The selected map area does not overlap the OSM region boundary.')
    if not geometry.isGeosValid():
        geometry = geometry.makeValid()
    target = folder/'map-coverage.geojson'
    target.write_text(json.dumps({'type':'FeatureCollection',
        'crs':{'type':'name','properties':{'name':'EPSG:3857'}},'features':[
            {'type':'Feature','properties':{},'geometry':json.loads(geometry.asJson())}]}),encoding='utf-8')
    bounds = geometry.boundingBox()
    # Stay on the original pixel grid. Cropping must not change m/pixel or
    # introduce a half-pixel shift when users align this map with other rasters.
    west = extent.xMinimum()+math.floor((bounds.xMinimum()-extent.xMinimum())/resolution)*resolution
    north = extent.yMaximum()-math.floor((extent.yMaximum()-bounds.yMaximum())/resolution)*resolution
    east = extent.xMinimum()+math.ceil((bounds.xMaximum()-extent.xMinimum())/resolution)*resolution
    south = extent.yMaximum()-math.ceil((extent.yMaximum()-bounds.yMinimum())/resolution)*resolution
    (folder/'map-region-info.json').write_text(json.dumps({
        'coverage':target.name, 'crs':'EPSG:3857', 'resolution_m':resolution,
        'projected_bounds':{'west':west,'south':south,'east':east,'north':north},
        'outside_region':'transparent', 'crop':'OSM region intersected with requested map area'
    },indent=2),encoding='utf-8')
    emit('Cropping the whole OSM map to its region; outside pixels will be transparent…')
    return QgsRectangle(west,south,east,north)


def block_mask(coverage, projection, geotransform, width, height):
    from osgeo import gdal
    source = mask = layer = None
    try:
        mask = gdal.GetDriverByName('MEM').Create('',width,height,1,gdal.GDT_Byte)
        mask.SetProjection(projection)
        mask.SetGeoTransform(geotransform)
        source = gdal.OpenEx(str(coverage),gdal.OF_VECTOR)
        layer = source.GetLayer(0)
        gdal.RasterizeLayer(mask,[1],layer,burn_values=[1])
        return mask.GetRasterBand(1).ReadAsArray().astype(bool)
    finally:
        layer = source = mask = None
