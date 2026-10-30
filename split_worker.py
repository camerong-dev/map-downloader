"""QGIS/GDAL subprocess: make a small preview or export pixel-aligned grid areas."""
import json
import math
from pathlib import Path
import sys
import tempfile

import numpy as np
from osgeo import gdal, osr, ogr
from raster_output import web_mercator_view, finish_output, write_rgb_style, export_geotiff, validate_export

gdal.UseExceptions()


def emit(kind, **values):
    print(json.dumps({'kind': kind, **values}), flush=True)


def check_cancel(job):
    if Path(job['cancel']).exists():
        raise InterruptedError('Cancelled. Completed areas were kept.')


def export_overview(source, projected, output, resolution, job):
    """Downsample directly to a whole-region file; never resize the detailed grid."""
    gt = projected.GetGeoTransform()
    if not math.isfinite(resolution) or resolution < max(abs(gt[1]), abs(gt[5])):
        raise ValueError('Overview spacing must be at least the full-resolution pixel spacing.')
    width = math.ceil(projected.RasterXSize * gt[1] / resolution)
    height = math.ceil(projected.RasterYSize * abs(gt[5]) / resolution)
    bounds = [gt[0], gt[3] - height * resolution, gt[0] + width * resolution, gt[3]]
    target = output / f'region_overview_{resolution:g}m.tif'
    partial = target.with_name(target.stem + '.part.tif')
    algorithm = 'mode' if source.GetRasterBand(1).GetColorTable() is not None else 'average'
    emit('status', message=f'Creating whole-region overview at {resolution:g} m/pixel…', progress=95)
    def callback(fraction, message, data):
        return 0 if Path(job['cancel']).exists() else 1
    result = None
    try:
        check_cancel(job)
        # Go directly from the source to the overview grid, avoiding a huge
        # full-resolution intermediate when the source CRS is geographic.
        virtual_path = partial.with_suffix('.vrt')
        view = gdal.Warp(str(virtual_path), source, format='VRT', dstSRS='EPSG:3857',
                           outputBounds=bounds, width=width, height=height,
                           dstAlpha=source.GetRasterBand(1).GetMaskFlags() != gdal.GMF_ALL_VALID,
                           resampleAlg=algorithm, overviewLevel='NONE', callback=callback)
        if view is None:
            raise RuntimeError('Could not prepare the overview grid.')
        try:
            for index in range(1, min(source.RasterCount, view.RasterCount) + 1):
                src, dst = source.GetRasterBand(index), view.GetRasterBand(index)
                if src.GetScale() is not None:
                    dst.SetScale(src.GetScale())
                if src.GetOffset() is not None:
                    dst.SetOffset(src.GetOffset())
            view.FlushCache()
            result = export_geotiff(view, partial, job, callback=callback)
        finally:
            view.Close()
            virtual_path.unlink(missing_ok=True)
        if result is None:
            raise RuntimeError('Could not create the regional overview.')
        preserved_bands = range(1, min(source.RasterCount, result.RasterCount) + 1) if job.get('compression', 'DEFLATE').upper() != 'JPEG' else []
        for index in preserved_bands:
            src, dst = source.GetRasterBand(index), result.GetRasterBand(index)
            if src.GetScale() is not None:
                dst.SetScale(src.GetScale())
            if src.GetOffset() is not None:
                dst.SetOffset(src.GetOffset())
        finish_output(result, callback)
        check_cancel(job)
        write_rgb_style(target, result)
        result.Close()
        result = None
        partial.rename(target)
        return {'file': target.name, 'pixel_spacing': resolution, 'crs': 'EPSG:3857',
                'width': width, 'height': height, 'bounds': bounds, 'resampling': algorithm}
    except Exception:
        if result is not None:
            result.Close()
            result = None
        partial.unlink(missing_ok=True)
        target.with_suffix('.qml').unlink(missing_ok=True)
        check_cancel(job)
        raise


def grid_windows(width, height, rows, columns):
    if not 1 <= rows <= min(30, height) or not 1 <= columns <= min(30, width):
        raise ValueError('Rows and columns must be 1–30 and no greater than the image dimensions.')
    return [(r, c, c * width // columns, r * height // rows,
             (c + 1) * width // columns - c * width // columns,
             (r + 1) * height // rows - r * height // rows)
            for r in range(rows) for c in range(columns)]


def open_region(paths, vrt_path):
    if not paths:
        raise ValueError('Add one or more TIFF files.')
    reference = None
    for path in paths:
        ds = gdal.Open(str(path))
        gt, projection = ds.GetGeoTransform(can_return_null=True), ds.GetProjection()
        if not gt or not projection or gt[1] <= 0 or gt[5] >= 0 or gt[2] or gt[4]:
            raise ValueError(f'{Path(path).name}: georeferenced north-up TIFFs are required.')
        srs = osr.SpatialReference(wkt=projection)
        signature = [(ds.GetRasterBand(b).DataType, ds.GetRasterBand(b).GetScale(),
                      ds.GetRasterBand(b).GetOffset(), ds.GetRasterBand(b).GetColorInterpretation(),
                      ds.GetRasterBand(b).GetNoDataValue()) for b in range(1, ds.RasterCount + 1)]
        if reference:
            rg, rs, sig = reference
            if not srs.IsSame(rs) or signature != sig or not all(math.isclose(gt[b], rg[b], rel_tol=1e-6) for b in [1, 5]):
                raise ValueError('The group must have matching CRS, resolution, bands, data types, scales and nodata. Prepare compatible TIFFs in QGIS first.')
        else:
            reference = gt, srs, signature
        ds = None
    if len(paths) == 1:
        return gdal.Open(str(paths[0]))
    ds = gdal.BuildVRT(str(vrt_path), list(map(str, paths)), options=gdal.BuildVRTOptions(strict=True, resolution='highest'))
    if ds is None:
        raise ValueError('Could not create the virtual mosaic.')
    return ds


def preview(ds, path, source=None):
    factor = min(720 / ds.RasterXSize, 360 / ds.RasterYSize, 1)
    width, height = max(1, round(ds.RasterXSize * factor)), max(1, round(ds.RasterYSize * factor))
    if source is not None and source is not ds:
        # Warp directly to thumbnail dimensions. Reading a full-resolution warped
        # VRT and shrinking afterwards can otherwise process billions of pixels.
        gt = ds.GetGeoTransform()
        small = gdal.Warp('', source, format='MEM', dstSRS='EPSG:3857', width=width, height=height,
                          outputBounds=[gt[0], gt[3] + ds.RasterYSize * gt[5],
                                        gt[0] + ds.RasterXSize * gt[1], gt[3]],
                          overviewLevel='AUTO', resampleAlg='near')
        for index in range(1, min(source.RasterCount, small.RasterCount) + 1):
            scale, offset = source.GetRasterBand(index).GetScale(), source.GetRasterBand(index).GetOffset()
            if scale is not None:
                small.GetRasterBand(index).SetScale(scale)
            if offset is not None:
                small.GetRasterBand(index).SetOffset(offset)
    else:
        small = gdal.Translate('', ds, format='MEM', width=width, height=height)
    valid = np.ones((height, width), dtype=bool)
    bands = [1, 2, 3] if small.RasterCount >= 3 else [1, 1, 1]
    rgb = []
    palette = small.GetRasterBand(1).GetColorTable()
    if palette is not None:
        indexes = small.GetRasterBand(1).ReadAsArray()
        colors = np.array([palette.GetColorEntry(i) for i in range(palette.GetCount())], dtype=np.uint8)
        rgba = colors[np.clip(indexes, 0, len(colors) - 1)]
        rgb = [rgba[:, :, c] for c in range(3)]
        valid &= rgba[:, :, 3] > 0
        valid &= small.GetRasterBand(1).GetMaskBand().ReadAsArray() != 0
    else:
        for index in bands:
            band = small.GetRasterBand(index)
            data = band.ReadAsArray().astype(float)
            mask = band.GetMaskBand().ReadAsArray() != 0
            valid &= mask & np.isfinite(data)
            if band.DataType == gdal.GDT_Byte:
                lo, hi = 0, 255
            elif band.GetScale() is not None:
                data = data * band.GetScale() + (band.GetOffset() or 0)
                lo, hi = 0.005, 0.40
            else:
                good = data[mask & np.isfinite(data)]
                lo, hi = np.percentile(good, [2, 98]) if good.size else (0, 1)
                hi = max(hi, lo + 1)
            rgb.append(np.nan_to_num(np.clip((data - lo) * 255 / (hi - lo), 0, 255)).astype(np.uint8))
    for index in range(1, small.RasterCount + 1):
        band = small.GetRasterBand(index)
        if band.GetColorInterpretation() == gdal.GCI_AlphaBand:
            valid &= band.ReadAsArray() > 0
    # Checkerboard makes missing coverage visible on the map.
    yy, xx = np.indices((height, width))
    background = np.where((xx // 12 + yy // 12) % 2, 210, 235).astype(np.uint8)
    image = gdal.GetDriverByName('MEM').Create('', width, height, 3, gdal.GDT_Byte)
    for c in range(3):
        image.GetRasterBand(c + 1).WriteArray(np.where(valid, rgb[c], background))
    result = gdal.Translate(str(path), image, format='PNG')
    result = image = small = None



def polygon_selection(points, width, height):
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError('Draw at least three outline points.')
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for point in points:
        if len(point) != 2 or any(not math.isfinite(v) for v in point):
            raise ValueError('Invalid outline coordinates.')
        x, y = point
        if not 0 <= x <= width or not 0 <= y <= height:
            raise ValueError('Outline points must be inside the map.')
        ring.AddPoint_2D(x, y)
    ring.CloseRings()
    geometry = ogr.Geometry(ogr.wkbPolygon)
    geometry.AddGeometry(ring)
    if not geometry.IsValid() or geometry.GetArea() < 1:
        raise ValueError('Outline must enclose at least one pixel and must not cross itself. Undo points to correct it.')
    xmin, xmax, ymin, ymax = geometry.GetEnvelope()
    x, y = math.floor(xmin), math.floor(ymin)
    return geometry, (0, 0, x, y, math.ceil(xmax)-x, math.ceil(ymax)-y)


def combined_grid_selection(windows):
    """Keep exact cell boundaries, including holes and disconnected selections."""
    geometry = ogr.Geometry(ogr.wkbGeometryCollection)
    for _, _, x, y, w, h in windows:
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for px, py in [(x, y), (x+w, y), (x+w, y+h), (x, y+h), (x, y)]:
            ring.AddPoint_2D(px, py)
        polygon = ogr.Geometry(ogr.wkbPolygon)
        polygon.AddGeometry(ring)
        geometry.AddGeometry(polygon)
    xmin, xmax, ymin, ymax = geometry.GetEnvelope()
    return geometry, (0, 0, int(xmin), int(ymin), int(xmax-xmin), int(ymax-ymin))


def mask_polygon(result, source, geometry, x0, y0, job):
    # Rasterize in source pixel coordinates in bounded blocks, retaining the
    # original nodata and alpha validity in a lossless internal TIFF mask.
    # GDAL 3.11 unified the raster/vector memory drivers under MEM.
    driver = ogr.GetDriverByName('MEM') or ogr.GetDriverByName('Memory')
    vector = driver.CreateDataSource('')
    layer = vector.CreateLayer('outline', geom_type=ogr.wkbUnknown)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(geometry)
    layer.CreateFeature(feature)
    previous = gdal.GetConfigOption('GDAL_TIFF_INTERNAL_MASK')
    try:
        gdal.SetConfigOption('GDAL_TIFF_INTERNAL_MASK', 'YES')
        if not result.GetRasterBand(1).GetMaskFlags() & gdal.GMF_PER_DATASET:
            result.CreateMaskBand(gdal.GMF_PER_DATASET)
        mask = result.GetRasterBand(1).GetMaskBand()
        for y in range(0, result.RasterYSize, 512):
            check_cancel(job)
            for x in range(0, result.RasterXSize, 512):
                w, h = min(512, result.RasterXSize-x), min(512, result.RasterYSize-y)
                tile = gdal.GetDriverByName('MEM').Create('', w, h, 1, gdal.GDT_Byte)
                tile.SetGeoTransform((x0+x, 1, 0, y0+y, 0, 1))
                layer.ResetReading()
                gdal.RasterizeLayer(tile, [1], layer, burn_values=[255])
                valid = tile.ReadAsArray() > 0
                for index in range(1, source.RasterCount+1):
                    band = source.GetRasterBand(index)
                    valid &= band.GetMaskBand().ReadAsArray(x0+x, y0+y, w, h) > 0
                    if band.GetColorInterpretation() == gdal.GCI_AlphaBand:
                        valid &= band.ReadAsArray(x0+x, y0+y, w, h) > 0
                mask.WriteArray(valid.astype(np.uint8)*255, x, y)
                for index in range(1, result.RasterCount+1):
                    band = result.GetRasterBand(index)
                    if band.GetColorInterpretation() == gdal.GCI_AlphaBand:
                        alpha = band.ReadAsArray(x, y, w, h)
                        band.WriteArray(np.where(valid, alpha, 0), x, y)
    finally:
        gdal.SetConfigOption('GDAL_TIFF_INTERNAL_MASK', previous)


def run(job):
    check_cancel(job)
    paths = [Path(p).resolve() for p in job['inputs']]
    with tempfile.TemporaryDirectory(prefix='split-work-', dir=job['workspace']) as folder:
        source = open_region(paths, Path(folder) / 'region.vrt')
        ds = web_mercator_view(source, Path(folder) / 'projected.vrt')
        try:
            width, height = ds.RasterXSize, ds.RasterYSize
            if job['action'] == 'preview':
                preview(ds, job['preview'], source)
                info = {'width': width, 'height': height, 'transform': ds.GetGeoTransform(),
                        'projection': ds.GetProjection(),
                        # GetDataTypeSize returns bits and is available in the
                        # GDAL bindings shipped by Ubuntu 24.04 as well as QGIS 4.
                        'bytes_per_pixel': sum((gdal.GetDataTypeSize(ds.GetRasterBand(b).DataType) + 7) // 8
                                               for b in range(1, ds.RasterCount + 1))}
                emit('preview', info=info)
                return
            validate_export(ds, job)
            geometry = None
            if job.get('polygon') is not None:
                geometry, window = polygon_selection(job['polygon'], width, height)
                windows = [window]
            else:
                windows = grid_windows(width, height, job['rows'], job['columns'])
                selected = set(job['selected'])
                windows = [w for i, w in enumerate(windows) if i in selected]
            if not windows:
                raise ValueError('Select at least one area on the preview.')
            combined = job.get('polygon') is None and job.get('combine_grid', False)
            if combined:
                geometry, window = combined_grid_selection(windows)
                windows = [window]
            overview_resolution = float(job.get('overview_resolution', 100)) if job.get('create_overview', False) else None
            if overview_resolution is not None and (not math.isfinite(overview_resolution) or overview_resolution < max(abs(ds.GetGeoTransform()[1]), abs(ds.GetGeoTransform()[5]))):
                raise ValueError('Choose overview spacing at least as large as the detailed pixel spacing.')
            output = Path(job['output']).resolve()
            if output.exists():
                raise ValueError('Choose a new output subfolder. Existing folders are kept.')
            output.mkdir(parents=True)
            manifest = {'inputs': list(map(str, paths)), 'rows': job['rows'], 'columns': job['columns'],
                        'crs_wkt': ds.GetProjection(), 'areas': [], 'polygon_pixels': job.get('polygon'),
                        'combine_grid': combined, 'selected': job.get('selected', []),
                        'compression': job.get('compression', 'DEFLATE'),
                        'jpeg_quality': job.get('jpeg_quality', 85) if job.get('compression', 'DEFLATE').upper() == 'JPEG' else None}
            try:
                for i, (r, c, x, y, w, h) in enumerate(windows):
                    check_cancel(job)
                    name = ('area_selected.tif' if combined else 'area_polygon.tif' if geometry is not None
                            else f'area_row{r + 1:02d}_col{c + 1:02d}.tif')
                    partial = output / (name + '.part.tif')
                    target = output / name
                    emit('status', message=f'Exporting {i + 1}/{len(windows)}: {name}', progress=100 * i / len(windows))
                    def callback(fraction, message, data):
                        return 0 if Path(job['cancel']).exists() else 1
                    try:
                        result = export_geotiff(ds, partial, job, src_win=[x, y, w, h], callback=callback)
                        if result is None:
                            raise RuntimeError('GDAL did not create the area.')
                        if geometry is not None:
                            mask_polygon(result, ds, geometry, x, y, job)
                        finish_output(result, callback)
                        write_rgb_style(target, result)
                        result = None
                        check_cancel(job)
                        partial.rename(target)
                    except Exception:
                        if 'result' in locals() and result is not None:
                            result.Close()
                            result = None
                        partial.unlink(missing_ok=True)
                        check_cancel(job)
                        raise
                    gt = ds.GetGeoTransform()
                    manifest['areas'].append({'file': name, 'pixel_window': [x, y, w, h],
                                              'bounds': [gt[0] + x * gt[1], gt[3] + (y + h) * gt[5],
                                                         gt[0] + (x + w) * gt[1], gt[3] + y * gt[5]]})
                if overview_resolution is not None:
                    manifest['overview'] = export_overview(source, ds, output, overview_resolution, job)
            finally:
                (output / 'areas.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            suffix = ' + whole-region overview' if overview_resolution is not None else ''
            emit('status', message=f'Complete: {len(windows)} detailed areas{suffix} in {output}', progress=100)
        finally:
            if ds is not source:
                ds.Close()
            source.Close()
            ds = None


if __name__ == '__main__':
    try:
        run(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
    except Exception as error:
        emit('error', message=str(error))
        sys.exit(2 if isinstance(error, InterruptedError) else 1)
