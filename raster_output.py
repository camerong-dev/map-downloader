"""Shared EPSG:3857 grids and QGIS display metadata for processed outputs."""
import math
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET
from contextlib import contextmanager
from osgeo import gdal, osr

TARGET_CRS = 'EPSG:3857'


@contextmanager
def gdal_config(**options):
    previous = {name: gdal.GetConfigOption(name) for name in options}
    try:
        for name, value in options.items():
            gdal.SetConfigOption(name, value)
        yield
    finally:
        for name, value in previous.items():
            gdal.SetConfigOption(name, value)


def copy_dataset_mask(source, target, src_win=None, callback=None):
    """Copy a shared validity mask in bounded blocks, independent of source lifetime."""
    x0, y0 = src_win[:2] if src_win is not None else (0, 0)
    target.CreateMaskBand(gdal.GMF_PER_DATASET)
    source_mask = source.GetRasterBand(1).GetMaskBand()
    target_mask = target.GetRasterBand(1).GetMaskBand()
    for y in range(0, target.RasterYSize, 512):
        if callback is not None and not callback(y / target.RasterYSize, '', None):
            raise RuntimeError('Export cancelled.')
        for x in range(0, target.RasterXSize, 512):
            w, h = min(512, target.RasterXSize - x), min(512, target.RasterYSize - y)
            target_mask.WriteRaster(x, y, w, h, source_mask.ReadRaster(x0 + x, y0 + y, w, h))
    target_mask.FlushCache()


def compression_options(job):
    compression = job.get('compression', 'DEFLATE').upper()
    quality = job.get('jpeg_quality', 85)
    if compression not in ('DEFLATE', 'JPEG'):
        raise ValueError('Choose DEFLATE or JPEG compression.')
    if compression == 'JPEG' and (not isinstance(quality, int) or not 1 <= quality <= 100):
        raise ValueError('JPEG quality must be a whole number from 1 to 100.')
    options = ['TILED=YES', 'BIGTIFF=IF_SAFER', f'COMPRESS={compression}']
    if compression == 'JPEG':
        options += [f'JPEG_QUALITY={quality}', 'PHOTOMETRIC=YCBCR', 'INTERLEAVE=PIXEL']
    return options


def validate_export(ds, job):
    compression_options(job)
    if job.get('compression', 'DEFLATE').upper() == 'JPEG':
        if ds.RasterCount < 3 or ds.GetRasterBand(1).GetColorTable() is not None:
            raise ValueError('JPEG export needs RGB imagery. Use lossless export for classified or single-band rasters.')


def export_geotiff(ds, path, job, src_win=None, callback=None):
    """JPEG uses rendered Byte RGB and a lossless internal transparency mask."""
    validate_export(ds, job)
    options = compression_options(job)
    kwargs = {'srcWin': src_win} if src_win is not None else {}
    if job.get('compression', 'DEFLATE').upper() != 'JPEG':
        ds.FlushCache()
        read_source = ds
        if (ds.GetDriver().ShortName == 'GTiff' and
                'JPEG' in ds.GetMetadata('IMAGE_STRUCTURE').get('COMPRESSION', '').upper()):
            # A freshly written JPEG TIFF must be read through a separate
            # decoder: reusing its live writer can crash GDAL CreateCopy.
            read_source = gdal.Open(ds.GetDescription(), gdal.GA_ReadOnly)
        flags = ds.GetRasterBand(1).GetMaskFlags()
        shared_mask = bool(flags & gdal.GMF_PER_DATASET and not flags & gdal.GMF_ALPHA)
        result = None
        with gdal_config(GDAL_TIFF_INTERNAL_MASK='YES'):
            try:
                # GDAL's automatic CreateCopy mask path can crash for a live
                # JPEG TIFF with an internal mask. Copy its mask explicitly.
                mask = {'maskBand': 'none'} if shared_mask else {}
                result = gdal.Translate(str(path), read_source, format='GTiff', overviewLevel='NONE',
                                        creationOptions=options, callback=callback, **kwargs, **mask)
                if result is not None:
                    if shared_mask:
                        copy_dataset_mask(ds, result, src_win, callback)
                    result.FlushCache()
                return result
            except Exception:
                if result is not None:
                    result.Close()
                raise
            finally:
                if read_source is not ds:
                    read_source.Close()
    with tempfile.TemporaryDirectory(prefix='jpeg-render-', dir=Path(path).parent) as folder:
        source = ds
        render = None
        if any(ds.GetRasterBand(i).DataType != gdal.GDT_Byte or
               ds.GetRasterBand(i).GetScale() not in (None, 1) or
               ds.GetRasterBand(i).GetOffset() not in (None, 0) for i in [1, 2, 3]):
            # Match the fixed-colour preview for raw RGBNIR files. Already
            # rendered byte maps are encoded directly, without another stretch.
            from mosaic_worker import render_fixed_qgis
            window = Path(folder) / 'window.vrt'
            view = gdal.Translate(str(window), ds, format='VRT', overviewLevel='NONE', **kwargs)
            view.Close()
            rendered = Path(folder) / 'rendered.tif'
            render_fixed_qgis(window, rendered, [1, 2, 3], 0.005, 0.40, job['cancel'])
            render = gdal.Open(str(rendered))
            source = render
            kwargs = {}
        previous = gdal.GetConfigOption('GDAL_TIFF_INTERNAL_MASK')
        try:
            gdal.SetConfigOption('GDAL_TIFF_INTERNAL_MASK', 'YES')
            # YCbCr JPEG requires three colour bands. Preserve alpha/nodata as
            # a separate internal mask instead of JPEG-compressing the alpha.
            result = gdal.Translate(str(path), source, format='GTiff', bandList=[1, 2, 3],
                                    maskBand='mask,1', overviewLevel='NONE', creationOptions=options,
                                    callback=callback, **kwargs)
            if result is not None:
                # Restore the original validity explicitly, including non-byte
                # alpha that some QGIS/GDAL render paths do not propagate.
                import numpy as np
                alpha = next((i for i in range(1, ds.RasterCount + 1)
                              if ds.GetRasterBand(i).GetColorInterpretation() == gdal.GCI_AlphaBand), None)
                x0, y0 = (src_win[0], src_win[1]) if src_win is not None else (0, 0)
                if result.GetRasterBand(1).GetMaskFlags() & gdal.GMF_ALL_VALID:
                    result.CreateMaskBand(gdal.GMF_PER_DATASET)
                mask = result.GetRasterBand(1).GetMaskBand()
                for y in range(0, result.RasterYSize, 512):
                    if callback is not None and not callback(y / result.RasterYSize, '', None):
                        raise RuntimeError('Export cancelled.')
                    for x in range(0, result.RasterXSize, 512):
                        w, h = min(512, result.RasterXSize - x), min(512, result.RasterYSize - y)
                        valid = np.ones((h, w), dtype=bool)
                        for index in [1, 2, 3]:
                            valid &= ds.GetRasterBand(index).GetMaskBand().ReadAsArray(x0 + x, y0 + y, w, h) > 0
                        if alpha is not None:
                            valid &= ds.GetRasterBand(alpha).ReadAsArray(x0 + x, y0 + y, w, h) > 0
                        mask.WriteArray(valid.astype(np.uint8) * 255, x, y)
                result.SetMetadataItem('EXPORT_JPEG_QUALITY', str(job.get('jpeg_quality', 85)))
            return result
        except Exception:
            if 'result' in locals() and result is not None:
                result.Close()
            raise
        finally:
            gdal.SetConfigOption('GDAL_TIFF_INTERNAL_MASK', previous)
            if render is not None:
                render.Close()


def is_web_mercator(ds):
    target = osr.SpatialReference()
    target.ImportFromEPSG(3857)
    return bool(ds.GetSpatialRef().IsSame(target))


def projected_resolution(ds):
    """Choose the finer projected source-pixel dimension, avoiding coarse defaults."""
    gt = ds.GetGeoTransform()
    if is_web_mercator(ds):
        return min(abs(gt[1]), abs(gt[5]))
    source = ds.GetSpatialRef().Clone()
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target = osr.SpatialReference()
    target.ImportFromEPSG(3857)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source, target)
    x, y = gt[0] + ds.RasterXSize * gt[1] / 2, gt[3] + ds.RasterYSize * gt[5] / 2
    p = transform.TransformPoint(x, y)
    px, py = transform.TransformPoint(x + gt[1], y), transform.TransformPoint(x, y + gt[5])
    resolution = min(math.hypot(px[0] - p[0], px[1] - p[1]), math.hypot(py[0] - p[0], py[1] - p[1]))
    if not math.isfinite(resolution) or resolution <= 0:
        raise ValueError('Cannot determine a valid EPSG:3857 resolution for this region.')
    return resolution


def web_mercator_view(ds, path):
    if is_web_mercator(ds):
        return ds
    resolution = projected_resolution(ds)
    ds.FlushCache()
    projected = gdal.Warp(str(path), ds, format='VRT', dstSRS=TARGET_CRS,
                          dstAlpha=ds.GetRasterBand(1).GetMaskFlags() != gdal.GMF_ALL_VALID,
                          xRes=resolution, yRes=resolution, resampleAlg='near',
                          errorThreshold=0.0, overviewLevel='NONE')
    if projected is None or not is_web_mercator(projected):
        raise RuntimeError('EPSG:3857 reprojection failed.')
    # Warp does not always retain reflectance unit metadata in its virtual bands.
    for index in range(1, min(ds.RasterCount, projected.RasterCount) + 1):
        src, dst = ds.GetRasterBand(index), projected.GetRasterBand(index)
        if src.GetScale() is not None:
            dst.SetScale(src.GetScale())
        if src.GetOffset() is not None:
            dst.SetOffset(src.GetOffset())
    return projected


def finish_output(ds, cancel_callback=None):
    levels = [level for level in [2, 4, 8, 16, 32, 64] if min(ds.RasterXSize, ds.RasterYSize) // level >= 1]
    if levels:
        algorithm = 'NEAREST' if ds.GetRasterBand(1).GetColorTable() is not None else 'AVERAGE'
        ds.BuildOverviews(algorithm, levels, callback=cancel_callback)
    if not is_web_mercator(ds):
        raise RuntimeError('Output CRS verification failed: expected EPSG:3857.')
    ds.FlushCache()


def write_rgb_style(path, ds):
    """Stop QGIS from applying another contrast stretch to already rendered RGB."""
    if ds.RasterCount < 3 or any(ds.GetRasterBand(i).DataType != gdal.GDT_Byte for i in [1, 2, 3]):
        return
    root = ET.Element('qgis', version='3.40', styleCategories='AllStyleCategories')
    pipe = ET.SubElement(root, 'pipe')
    alpha = next((i for i in range(1, ds.RasterCount + 1) if ds.GetRasterBand(i).GetColorInterpretation() == gdal.GCI_AlphaBand), -1)
    if alpha == -1 and ds.GetRasterBand(1).GetMaskFlags() & gdal.GMF_PER_DATASET:
        # QGIS's GDAL provider exposes the shared internal mask as an extra
        # virtual alpha band after the stored RGB bands.
        alpha = ds.RasterCount + 1
    renderer = ET.SubElement(pipe, 'rasterrenderer', type='multibandcolor', redBand='1', greenBand='2', blueBand='3',
                             alphaBand=str(alpha), opacity='1', nodataColor='')
    for name in ['red', 'green', 'blue']:
        ce = ET.SubElement(renderer, name + 'ContrastEnhancement')
        for tag, value in [('minValue', '0'), ('maxValue', '255'), ('algorithm', '0')]:
            ET.SubElement(ce, tag).text = value
    ET.SubElement(pipe, 'rasterresampler', maxOversampling='2')
    ET.ElementTree(root).write(str(Path(path).with_suffix('.qml')), encoding='utf-8', xml_declaration=True)
