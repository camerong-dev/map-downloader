# ESA Region Downloader

A Windows and Linux desktop GUI using Python and Tkinter. Downloads ESA WorldCover
2021 v200 or 2020 v100 satellite composites or land-cover GeoTIFFs from public S3 over anonymous HTTPS.
No AWS account, API keys, boto3, or runtime pip packages are needed.
Processing tabs require a native local QGIS installation with Python bindings,
NumPy and GDAL 3.8 or newer. QGIS 3 LTR and QGIS 4 APIs are supported.

## Run

Install Python 3.11 or newer from python.org, including Tcl/Tk and the PATH option.
Open PowerShell in this folder:

```powershell
python app.py
```

On Linux (native Debian/Ubuntu installation; Ubuntu 24.04 or newer is a suitable base):

```bash
sudo apt update
sudo apt install qgis python3-qgis python3-gdal python3-numpy python3-tk python3-venv
/usr/bin/python3 app.py
```

Use packages appropriate to your distribution; see the [official QGIS installation
guide](https://qgis.org/resources/installation-guide/). The processing prefix is
automatically detected, usually `/usr`; select that prefix in the QGIS folder field.
Linux uses the native prefix's `bin/python3`, not a Windows executable. For a custom
native installation, set `QGIS_PREFIX_PATH` and, if necessary, `ESA_QGIS_PYTHON` to
the compatible interpreter. Flatpak/Snap-only QGIS installations are not supported
by this external Python launcher; install native QGIS Python packages instead.
The desktop GUI needs a graphical desktop session. Only workers run offscreen.

Paste a comma-separated bounding box in **west, south, east, north** order and click
**Apply**, or edit the four labelled edges individually.
Reversed west/east or south/north edges are automatically sorted in this tab too;
the applied coordinates and individual fields show the normalized bounds.
For example, `-61.528931,-52.484453,-57.615051,-50.934200` sets west to
`-61.528931`, south to `-52.484453`, east to `-57.615051`, and north to `-50.934200`.
The supplied example is preloaded on startup.
Choose the product, year and output
folder, click **Preview region**, then **Download tiles**. Negative longitudes
are west of Greenwich; negative latitudes are south of the equator.
Split a region crossing longitude ±180 into two downloads.
Bounds must have nonzero width and height. Preview is limited to 400 candidate
tiles to prevent accidental worldwide requests.

The preview checks availability and reports total transfer size before downloading.
Ocean-only tiles may not exist. Network errors are reported; retry the operation
after restoring connectivity. Cancellation can take up to the current network
timeout (30 seconds). Completed files are kept; files of the expected size are
skipped on retry. This is a size check, not cryptographic integrity verification.
An interrupted tile is removed and restarts from the beginning on retry.
Existing files with conflicting sizes are not overwritten. Use a separate output
folder per running instance. Successful batches include a JSON region manifest
with source URLs, requested bounds, tile sizes, attribution, and license.

## Place search & area preview

In the Download tab, enter a country, county, city or town and press **Search**
(or Enter). Choose the correct match from the results, which include the wider
region and country. The selected place's bounding rectangle fills the coordinate
fields. If the service only returns a centre point, the result explicitly says
**5 km around centre**; it is a suggested area, not the administrative boundary.
Countries and counties without usable extents are not replaced with a small point
area. Date-line regions still need separate downloads.

Set **Buffer (km)** and click **Apply to place** to add surrounding countryside.
Drag the street map to pan, use the wheel or +/− to zoom, and **Shift + drag** to
draw a replacement rectangle. Pasted coordinates update the map when you click
**Apply**. **Fit area** restores the selected area and checked tile coverage.

The blue outline is the requested rectangle. Click **Preview region** to check
ESA availability and total transfer size; orange outlines show the full available
tiles, and the view fits both outlines. A small town can require a much larger
tile. Downloads remain full original ESA GeoTIFFs, not street-map tiles or files
clipped to a town boundary. Large countries may exceed the existing 400-tile
limit; draw a smaller area or split the request.

Search uses the [Photon public service](https://github.com/komoot/photon), with
submitted searches only, cached results and serialized requests. Street preview
tiles use [OpenStreetMap](https://operations.osmfoundation.org/policies/tiles/),
with visible attribution, an identifying User-Agent, a minimum seven-day local
tile cache and on-demand viewport loading. No bulk or offline street-map tile
download is offered. Cache files are under `%LOCALAPPDATA%/ESA-Maps/preview-cache`
on Windows or `~/.cache/ESA-Maps/preview-cache` on Linux. The preview starts without
network access until a search, coordinate application or ESA preview is requested.
If either preview service is unavailable, coordinate entry and ESA checks still
work. These public services provide no availability guarantee.

For another Photon-compatible service, set `ESA_MAPS_SEARCH_URL` to its HTTPS API
endpoint. `ESA_MAPS_TILE_URL` accepts an HTTPS street-tile template with `{z}`, `{x}`
and `{y}`. Any replacement tile provider must permit this usage and retain the
displayed OpenStreetMap attribution. ESA downloads and Geofabrik hybrid processing
use their existing services independently.

## Build a single Windows executable

On Windows with Python installed:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The build creates an isolated `.venv`, installs PyInstaller, and outputs
`dist\ESARegionDownloader.exe`. Distribute that executable; users do not need
Python. Internet is needed for data downloads. Build on the same operating system
and architecture you intend to distribute to. Unsigned executables may trigger
Windows SmartScreen; code signing is a separate distribution step.

## Build a Linux executable

On the target Linux system, with native QGIS and the dependencies above installed:

```bash
bash build.sh
./dist/ESARegionDownloader
```

`build.sh` creates `.venv-build` for PyInstaller. The portable `build.py` selects
the correct resource separator for the build OS. Build Windows executables on
Windows and Linux executables on Linux; there is no single binary for both systems.
The Linux executable still requires native QGIS/Python/GDAL on the destination
machine. Build on the oldest Linux distribution you intend to support because
system library compatibility matters.

External workers use their host QGIS environment rather than bundled GUI libraries.
In frozen Linux builds, `LD_LIBRARY_PATH` is restored to its original host value,
as described in [PyInstaller's external-program guidance](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html).
`.github/workflows/linux.yml` supplies Ubuntu raster tests and a GUI smoke test.
Local verification so far is on Windows; Linux launcher behaviour is unit tested
and the Linux workflow has been added, but a native Linux run has not been completed here.

## Products and QGIS

The default **Satellite imagery (RGB + near infrared)** product downloads annual
Sentinel-2 RGBNIR composites (`S2RGBNIR`) from `esa-worldcover-s2/rgbnir`.
These use a **1° × 1°** grid, matching S2RGBNIR results in the ESA viewer.
They are annual composites, rather than individual dated satellite scenes.
Load the four-band GeoTIFFs into QGIS and use multiband colour, with bands
1/2/3 for red/green/blue; band 4 is near infrared. Adjust the display stretch
as needed. Select **Land-cover classification** for the categorical map instead.

## Colour balance and merge

Open the **Colour balance & merge** tab in the same application:

1. Check the detected QGIS installation folder (for example `C:\Program Files\QGIS 4.2.3`).
2. Add TIFF files, or add a folder containing the downloaded imagery.
3. Leave **Your QGIS colours — fixed RGB stretch** selected to reproduce the supplied
   QGIS script's method: RGB bands 1/2/3, `StretchToMinimumMaximum`, minimum **0.005**,
   with your updated desert-friendly maximum **0.40** for each channel. Both limits
   are editable (the original script used 0.20). This uses the actual
   `QgsMultiBandColorRenderer` and `QgsContrastEnhancement`, then exports the rendered
   pixels through `QgsRasterFileWriter` before merging. QGIS honours band scale/offset
   metadata (the downloaded WorldCover RGBNIR files use a 0.0001 scale), so the limits
   are applied to reflectance values rather than raw integer storage values.
4. The earlier **Shared colour stretch** remains available for a sampled 2nd–98th
   percentile range per channel across all tiles. For tiles with different
   brightness/colour distributions, choose **Match colours
   to reference tile** and select a representative tile with **Use selected as reference**.
   This applies sampled, per-channel quantile matching before the shared reference
   stretch. It can reduce seams but may change genuine differences in terrain or water;
   it is not overlap-based radiometric calibration and cannot guarantee invisible seams.
5. Choose a **new** output `.tif` and click **Balance colours & merge**.

The app runs PyQGIS and the GDAL/NumPy tools bundled with QGIS in a separate background process;
QGIS Desktop does not need to be open. No extra GIS pip packages are installed.
The fixed mode exports each tile with QGIS's raster renderer; the two statistical
modes balance full-resolution tiles in 512-pixel blocks. It merges the results and builds viewing
overviews. The result is a compressed, tiled, georeferenced **8-bit RGB + alpha GeoTIFF**,
in **EPSG:3857**, with BigTIFF support for large outputs. RGB bands default to 1/2/3. Near infrared and
original scientific reflectance values are not included in this display product.
Source files remain unchanged. In the two statistical modes, colour statistics are
approximate (up to 512 × 512 sample pixels per tile, excluding nodata and masked pixels).
The fixed mode uses the specified limits, without percentile matching. Its output
has the desired appearance baked into byte RGB values: view RGB bands 1/2/3 with
**No enhancement** in QGIS rather than applying the reflectance limits again.

Use RGB imagery, not the categorical WorldCover land-cover map. Inputs must have
the same CRS and pixel size and be north-up. The worker reports incompatible inputs
instead of silently skipping them. Where valid imagery overlaps, later files take
priority. Alpha/nodata transparency is honoured. Geographic gaps remain transparent.
The output covers all selected tiles, without clipping to the downloader's region.
Reprojection uses the base-resolution image, with output pixel spacing chosen from
the finer projected source-pixel dimension. Native downloaded ESA source files stay
in their original CRS; all merged outputs and split outputs are EPSG:3857. The
download bounds remain WGS84 longitude/latitude degrees.

Each rendered byte RGB output includes a matching `.qml` sidecar for QGIS with RGB
bands 1/2/3, alpha transparency and no additional contrast enhancement. Keep it beside
the TIFF and add the new file as a fresh QGIS layer. Viewing overviews are built for
both mosaics and split areas. Set the QGIS **project** CRS to EPSG:3857 as well if
you want the map canvas to use it; a project's CRS is separate from a raster's CRS.

Intermediate balanced tiles and the mosaic are written beside the output and removed
after completion or cancellation. Allow ample free space for both the intermediates
and final mosaic, potentially several times the input size. Cancellation is cooperative
and can wait for the current GDAL operation. Existing output files are never overwritten.
The packaged GUI still requires QGIS on the destination computer; it does not bundle QGIS.

## OSM hybrid overlay

The **OSM hybrid overlay** tab uses regional [Geofabrik OSM extracts](https://download.geofabrik.de/)
and processes roads and settlement names locally. The road styling, labels,
transparency and EPSG:3857 outputs use the same renderer as before. There are no
Overpass queries in this workflow.

It extracts data for the full selected satellite TIFF by default. Without a TIFF, or with **Use full
satellite TIFF bounds** unchecked, it uses coordinates pasted directly into the tab,
or the WGS84 edges entered on **Download region** when the local coordinate box is blank. Reversed longitude or
latitude edges are automatically sorted, and the box shows the normalized bounds.
For example, `0.514984,50.966211,-0.199127,51.109558` becomes
`-0.199127,50.966211,0.514984,51.109558`. Regions crossing the date line must still
be split into two downloads. It creates an offline hybrid style with outlined roads, white labels with
dark halos, curved road names, and scale-dependent city/town/village labels.
This is an independent OSM-based hybrid map style; coverage and appearance differ
from Google Maps. It does not download Google imagery or Google map labels.

1. Paste **west,south,east,north** in the OSM tab, or leave it blank to use **Download region**.
2. Open **OSM hybrid overlay** and check the QGIS folder.
   The **Geofabrik datasets** box defaults to West Sussex. Enter **one Geofabrik
   `.osm.pbf` URL or local PBF path per line**. Use **Add PBF files…** to select
   several already-downloaded files at once; you can mix URLs and local files.
   Remove the default entry if it is not relevant. Local `.osm` XML files are also
   supported. To find extracts,
   use the [Geofabrik download catalogue](https://download.geofabrik.de/).
   **Choose an extract covering all the land in your requested area.** For a map
   crossing country or county boundaries, add each relevant extract or use a larger
   region. Selecting a TIFF changes the extraction bounds, not the coverage of the
   chosen datasets.
3. Optionally select a **rendered 8-bit RGB satellite TIFF** created by **Colour
   balance & merge**. Raw reflectance and categorical inputs are rejected. Leave
   this field empty to create the transparent overlay only. **Use full satellite
   TIFF bounds** is enabled by default: selecting a merged or split TIFF extracts
   OSM for that file's entire rectangular extent within the source extract, regardless of the coordinate box
   or original download region. Uncheck it to deliberately use manual coordinates.
4. Choose a **new output folder** and export spacing in EPSG:3857 metres/pixel.
   The default is **10**; use **5** for smaller areas where you want local road
   names. The full-detail vector project is always created. Enabled raster exports
   write a single GeoTIFF per output using bounded render blocks, with BigTIFF for
   large files. The former 32-million-pixel / 16384-pixel-edge skip limits no longer
   apply. Requested pixel spacing is preserved. Large regions need more processing
   time and disk space; choose coarser spacing explicitly for a smaller file, or
   disable GeoTIFFs to create the vector project only.
   **Export compression** defaults to **Match satellite TIFF**: a JPEG satellite
   TIFF produces a JPEG hybrid at its recorded quality (85 if unspecified); other
   inputs produce a lossless DEFLATE hybrid. You can explicitly choose lossless or
   JPEG and set its quality. The separate road overlay always uses lossless RGBA
   DEFLATE. JPEG hybrids store three RGB bands plus a lossless internal binary mask,
   keeping transparent gaps without an external mask file. JPEG is lossy, including
   the baked labels, so use the lossless overlay or vector project for exact text
   edges. The satellite image is re-encoded when labels are baked in; files are
   not concatenated and the hybrid size is not the sum of its two source file sizes.
5. Click **Create hybrid from Geofabrik**. The app downloads the regional PBF once,
   verifies it against Geofabrik's MD5 checksum, and imports only roads and settlement
   nodes into a spatially indexed local GeoPackage. Later runs reuse the import and
   select data for each merged or split TIFF locally. You do not need to manage the
   GeoPackage manually.

For a map spanning two countries, list both country files/URLs and select the
complete merged satellite TIFF. The app imports each dataset, selects matching
roads and settlements from both, and removes duplicate OSM IDs at shared borders.
Node and way IDs are treated separately. If overlapping sources contain different
versions of an object, the **first source listed wins**; use extracts from similar
dates for a consistent snapshot. The manifest records all sources and duplicate
counts. Each import is cached independently. A failed source stops the job instead
of silently publishing a combined overlay with missing data.

The result is still one `overlay.tif`, one `hybrid.tif` (when imagery is supplied),
and one `hybrid.qgz`, using the same road filters, label style and compression
options. You can split the completed hybrid in **Split maps visually**. Labels in
those cuts remain fixed at the original export scale. For more detailed subregion
labels, select a split satellite TIFF and reuse the same source list to create a
new hybrid; matching regional imports are reused locally.

**Roads and names to show** controls both the QGIS project and the exported
GeoTIFFs. Motorways/M roads, A roads, B roads and other main roads are enabled by
default. Minor/residential/service roads are off. Other main roads means OSM
`tertiary` and its links; unnumbered country lanes (`unclassified`) are included
with the optional minor roads group. Numbered M/A/B `ref` tags take priority over
the OSM road class, with motorway/trunk/primary/secondary classes as fallbacks.
This follows the [UK OSM road mapping conventions](https://wiki.openstreetmap.org/wiki/Roads_in_the_United_Kingdom);
outside the UK the class-based fallbacks still apply.

Road labels can show **Numbers and names**, **Numbers only**, **Names only**, or
be **Off**. City/town names are on by default; village names and hamlets/suburbs
are optional. Scale limits and collision avoidance still control which names fit
on the map. Raw GeoJSON keeps all downloaded road and settlement data; these
settings control what is displayed. To create another styled export, select the
options and choose a new output folder in the same parent folder; matching cached
downloads and imports are reused.

Outputs:

- `roads.geojson` and `places.geojson`: reusable WGS84 vectors. Includes drivable
  road classes and settlement **nodes** (cities, towns, villages, hamlets, suburbs).
  Footpaths, buildings, boundaries and other OSM features are outside this download's scope.
  Full intersecting road geometries may extend beyond the requested bounds.
  This is a filtered regional extract, not a complete OSM database.
- `hybrid.qgz`: QGIS project with styled offline vectors. Labels adapt to scale
  and collisions as you zoom. Keep the GeoJSON files beside the project. If satellite
  imagery was selected, keep it at its original location relative to the project.
- `overlay.tif`: transparent EPSG:3857 RGBA overlay and matching QGIS `.qml` style.
- `hybrid.tif`: satellite image with roads and labels baked in, when a satellite
  TIFF was supplied, plus matching `.qml`. Its pixel spacing is the selected export
  spacing, which may be coarser than the source satellite image. It includes only
  the requested region, rounded outward by less than one pixel east/south.
- `osm-manifest.json` and `ATTRIBUTION.txt`: all sources, bounds, counts, date and credit.

GeoTIFF labels are fixed at the export scale; they cannot change when zooming. For
different label detail, export another resolution or use the vector QGIS project.
OSM vector data itself has no raster pixel resolution. In QGIS, open `hybrid.qgz`
to keep roads and text sharp at every zoom; loading only `hybrid.tif` displays the
fixed-resolution raster and will become blurry when enlarged. Uncheck **Also export
fixed-resolution GeoTIFFs** if only the vector project is needed.
Label collision handling deliberately omits some names. Imagery coverage gaps remain
transparent, although roads and labels may still appear over those gaps. Hybrid
GeoTIFFs can be loaded into **Split maps visually**; baked labels then remain fixed.
These exports do not add MBTiles or ATAK packaging.

Large rasters are rendered in blocks of up to 4096 × 4096 output pixels, with
surrounding context for road-label placement, then written to exact pixel windows
in one output TIFF. No split files need assembling. The log reports block progress
and overview creation. Label placement is calculated per padded block, so some
labels near block boundaries may differ from an interactive QGIS view; pixel
coverage and satellite alignment remain continuous. For example, a 55660 × 35385
RGBA map has about 7.3 GiB of pixel data before compression and overviews **per
output**; actual file sizes depend on compression and image content.
JPEG satellite files can grow substantially when exported using lossless DEFLATE,
because lossless encoding preserves their decoded pixels rather than their original
JPEG bitstream. Match-source compression avoids that unintended change of codec.
Existing outputs are never overwritten. Cancellation removes the current incomplete TIFF
and keeps completed files and the vector project.

Regional downloads and indexed imports are saved in `.geofabrik-cache` beside the
output folder. They have no automatic expiry: the log and manifest record the
download dates. Check **Refresh cached Geofabrik downloads** to fetch fresh snapshots
for the URL entries, or replace local PBFs with newer downloads. Existing outputs are kept. The old
`.esa-osm-cache` is unused by the hybrid tab and may be removed when no longer needed.
Changing output parents uses a separate cache; to work fully offline, reuse the
same parent or browse directly to your existing PBF. A local PBF is never downloaded
or refreshed by the app.

Large country extracts need time and extra disk space for their first import.
The worker scans the regional PBF for roads and settlements, then subsequent bbox
queries use the local spatial indexes. No Overpass request-count or feature-count
limits apply. A region outside the selected source can have missing roads; an
entirely empty result is reported explicitly. Regions crossing ±180° must be split,
and hybrid output supports latitudes from -85° to 85°.
Network operations use a 90-second socket timeout and up to three attempts for
temporary connection errors, HTTP 408/500/502/503/504 and incomplete transfers,
waiting 5 and then 15 seconds between attempts. The log names the file and stage
(checksum or PBF transfer). Permanent HTTP errors are reported without repeated
requests. If the service remains unavailable, download the regional PBF files in
a browser and add those local files; no checksum-service request is needed for
local inputs.

Interrupted PBF transfers are kept in checksum-specific `.part` files in the cache.
Matching snapshots resume using HTTP Range after a retry or a later run. A server
that ignores Range causes a safe restart. The completed file must pass its MD5
check before import; invalid partials are removed. If the provider publishes a
new snapshot, a different partial filename is used rather than mixing versions.

Cancellation waits for the current network read (90-second socket timeout), GDAL
import operation, or QGIS render. Completed downloads, imports and outputs remain;
choose a new output folder in the same parent for another run to reuse downloads.
Interrupted imports restart on retry; completed imports are reused. No online
tiles are needed to view downloaded data.

OSM data is licensed under the [ODbL](https://opendatacommons.org/licenses/odbl/1-0/).
Exports show **© OpenStreetMap contributors** with the copyright URL; preserve this
credit and the accompanying license information when sharing. Satellite source
attribution must also be preserved; the original ESA download manifest contains its
year-specific credit. See [OpenStreetMap attribution requirements](https://www.openstreetmap.org/copyright).

`tests/test_geofabrik.py` tests source validation, checksum verification, offline
cache reuse and refresh. `tests/geofabrik_integration.py` checks local import,
spatial selection, road attributes, cache reuse, rendering and cancellation.
`tests/hybrid_integration.py` runs under QGIS Python and
checks rendering, georeferencing, transparency, project reload and output protection
using synthetic offline data.

## Split maps visually

The **Split maps visually** tab splits one merged TIFF or a group of compatible
TIFFs into smaller geographic areas:

1. Click **Load TIFFs & preview** and select your merged file, or multiple TIFFs.
2. Adjust **Rows** and **Columns** (for example 2 × 2 makes four areas). The yellow
   grid updates immediately over the map preview. Labels are row,column, with north
   at the top. Nothing is selected initially. Checkerboard areas indicate masked/no-data coverage in the preview.
   Use the mouse wheel or **+ / −** buttons to zoom (25–800% of the preview image).
   Wheel zoom centres on the cursor. Drag with the left or middle mouse button to
   pan; a plain left click selects/deselects an area. **Fit map** recentres
   the whole map. The split grid and hover bounds stay aligned while zooming/panning.
   Zoom enlarges the preview image; it does not load additional source detail or
   change exported extents, resolution, selected areas or pixel values.
3. Click the areas you want to export. Selected areas have a yellow outline and
   check mark; click again to deselect. **Select all** and **Clear selection** handle
   the whole grid. Changing the rows/columns clears the selection. Hover to see exact
   pixel dimensions and bounds in EPSG:3857 metres.
   Alternatively, select **Draw polygon** and click successive points along the
   border you want to follow. Drag to pan and use the wheel to zoom between points.
   Choose **Finish outline** (or Enter) to close it. **Undo point** (or Backspace)
   reopens the outline and removes the last point; **Clear selection** starts again.
   Export creates `area_polygon.tif`, cropped to the outline's pixel-aligned bounds,
   with everything outside the polygon masked out. Existing nodata/alpha validity
   is preserved, as are source resolution and pixel values in lossless exports.
   Outlines must not cross themselves. Grid and polygon selections are retained
   separately; the active mode determines what is exported.
4. Choose an output location. The app proposes a new subfolder.
5. Leave **Create whole-region overview** enabled and choose **50, 100, 250 or 500**
   EPSG:3857 metres per pixel (default **100**). Uncheck it to export detailed areas only.
6. Leave **Export grid as one TIFF** checked (the default) to write the selected
   grid cells directly into `area_selected.tif`. The file is cropped to the selected
   cells' combined bounds; unselected cells, including gaps between separate
   selections, are masked out. Source pixel alignment, resolution and existing
   nodata/alpha validity are preserved, avoiding a later merge step. Uncheck this
   option to export individual files named `area_row01_col01.tif`, etc. Polygon
   mode always writes one `area_polygon.tif` regardless of this checkbox.
   Click **Export selected areas** (enabled once an area is selected).

The separate `region_overview_100m.tif` (or selected spacing) covers the **entire loaded
region**, including grid areas excluded from detailed export. It averages the rendered
pixel colours down to a coarser grid; nodata and alpha are respected. It does not
change the merged file or the detailed split pixels. The overview uses the same
EPSG:3857 CRS, with a QGIS RGB style and viewing overviews. Its bounds may extend
by less than one overview pixel at the east/south edges to cover the complete region.
An overview must have spacing at least as coarse as the detailed image.
Categorical rasters use mode resampling rather than averaging class IDs.

For matching colours, load your **colour-balanced merged TIFF** before exporting.
The overview does not apply a new colour stretch. In QGIS, put it below the detailed
areas; optional scale visibility can control which layers show as you zoom.
EPSG:3857 spacing is in projected map metres, not constant ground metres at every latitude.

The preview is reduced in size to keep the GUI responsive; exports use a full-detail
EPSG:3857 grid. Existing EPSG:3857 rasters are cut directly without reprojection.
Other CRSs are reprojected with nearest-neighbour sampling first, using a virtual
grid rather than a full intermediate TIFF. Data type, bands and scale metadata are kept.
Already rendered byte RGB maps are shown without a further stretch. Scaled imagery
is shown with the 0.005–0.40 reflectance stretch for preview only. Other non-byte
images use an approximate preview stretch. No colour processing is applied to exports.

Grid cuts follow exact EPSG:3857 pixel boundaries; all areas together cover that grid without
missing or duplicating pixels. Each is a compressed tiled GeoTIFF. The size estimate
is pixel data before compression, not an exact output-file size or a hard limit.
Areas containing only no-data are still exported if selected. A JSON manifest lists
the exported files, pixel windows and geographic bounds. Completed areas remain if
cancelled; an incomplete area is removed. Existing output folders are never overwritten.

Multiple inputs use a virtual mosaic, avoiding another large intermediate merged TIFF.
Inputs must match in CRS, pixel size, band layout, types, scale/offset and nodata.
They must be north-up. Slightly offset source grids use the virtual mosaic's nearest
pixel mapping; EPSG:3857 inputs are split without another reprojection. Later input files
take priority in overlaps; the virtual mosaic does not alpha-blend overlapping images.
For predictable overlapping imagery, merge it first using the processing tab.
The temporary preview is stored in the operating system's temporary folder and
removed when reloading or closing the application. Source folders may be read-only.

`tests/split_integration.py` verifies full pixel coverage, exported values and scale,
selected areas, virtual-mosaic previews, overview averaging and transparency,
unchanged full-detail areas, cancellation and existing-folder protection.

## Export compression

Both **Colour balance & merge** and **Split maps visually** include an
**Export compression** selector:

- **Lossless (DEFLATE)** preserves the exported pixel values exactly and remains
  the default. It supports source data types, classifications and extra bands.
- **JPEG (rendered RGB)** writes a tiled GeoTIFF with `COMPRESS=JPEG`,
  `JPEG_QUALITY=85` by default, `PHOTOMETRIC=YCBCR` and pixel interleaving.
  Change quality from 1–100 if desired. It keeps EPSG:3857 and full pixel dimensions
  but is lossy, so decoded pixel colours may change slightly.

The split tab applies the selected compression to both detailed areas and the
optional whole-region overview. The merge tab applies it to the final mosaic,
after colour rendering and reprojection; intermediates remain lossless.

JPEG output contains **three rendered 8-bit RGB bands**. Already rendered byte
maps are encoded without another colour stretch. Raw multi-band reflectance inputs
in the split tab are rendered as RGB bands 1/2/3 with the preview's fixed
0.005–0.40 stretch first. Near infrared and original scientific values are omitted.
Classified or single-band rasters require the lossless option.

For JPEG, transparency is stored as a **lossless internal binary mask**, instead
of compressing an alpha band with JPEG. Masked pixels stay transparent; partially
transparent pixels become valid/invalid mask pixels. No external `.msk` is needed.
Keep the supplied `.qml` next to the TIFF: it tells QGIS to use the internal mask's
virtual alpha band and avoids applying a second colour stretch. Viewing overviews
are retained for fast navigation.

For the best master-copy workflow, keep the merged TIFF lossless and select JPEG
85 for the final split maps and overview. Selecting JPEG in both tabs works, but
splitting a JPEG master recompresses its already lossy colours.

`tests/compression_integration.py` checks actual JPEG encoding, size reduction on a
synthetic map, quality, raw reflectance rendering, internal-mask transparency in
QGIS, merged/split outputs, and regional overviews.

## Data and output scope

The **land-cover classification** product is a categorical dataset, not natural-colour satellite
photography, terrain elevation, or a street map. It has 11 land-cover classes and
10 m nominal resolution. Downloads contain entire intersecting 3° × 3° source
tiles, including data outside the requested rectangle. Imagery instead uses 1° tiles.
Files retain their original
georeferencing and class values in the download tab. The processing tab creates
a separate display mosaic reprojected to EPSG:3857. No region clipping, MBTiles,
or ATAK package conversion is performed. View/style these files in GIS software;
ATAK compatibility depends on the target app's supported raster formats and styling.

The app supports these two WorldCover products. Other ESA products need their own
discovery/download adapter. The preview count depends on the selected product;
classification and imagery use different grids and are not interchangeable.

Official sources:

- https://esa-worldcover.org/en/data-access
- https://github.com/ESA-WorldCover/esa-worldcover-datasets
- https://registry.opendata.aws/esa-worldcover-vito/

Data license: CC BY 4.0. Preserve acknowledgement when sharing maps or derived data:

> © ESA WorldCover project [year] / Contains modified Copernicus Sentinel data ([year]) processed by ESA WorldCover consortium

## Process timing

The desktop workspace uses four numbered tabs: Download, Colour & merge, Split
maps, and OSM hybrid. Blue buttons mark the main action on each tab. Hybrid
settings group map coverage and OSM sources on the left, with output and road
labels on the right. The process history stays below the settings.

All four tabs show timestamped status messages and process logs in local time,
including the timezone offset in the logs. Each operation shows its start time
and a live elapsed counter. When it finishes, fails, or is cancelled, its end time
and total duration remain visible. Each tab keeps its own timing and log history
for the current session. Elapsed time is unaffected by system clock adjustments.

## Checks

```powershell
python -m unittest discover -s tests -v
```

Run the complete local check set (unit tests, all six QGIS integration suites,
and the desktop smoke test) with:

```powershell
python check.py --gui
```

QGIS is detected automatically; use `--qgis "C:\Program Files\QGIS 4.2.3"` to
select another installation. On headless systems omit `--gui`, or run with a
virtual display. Timestamped logs and a summary are saved under
`.verification/checks/`. Checks use temporary synthetic data and mocked downloads;
they do not modify existing map files or require live download services.

Tests cover coordinate/tile boundaries, cancellation, interrupted transfers and
existing-file protection using mocked HTTP responses (no large downloads).

`tests/mosaic_integration.py` runs separately under QGIS Python and verifies
synthetic mosaic dimensions, transparency, known colour-offset correction,
cancellation and output-file protection.
