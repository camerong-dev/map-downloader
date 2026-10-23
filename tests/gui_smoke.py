"""Desktop smoke checks; called explicitly rather than during unit discovery."""
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
import base64
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from downloader import Bounds
from geofabrik import DEFAULT_SOURCE
from place_search import Place
from downloader import Tile


@patch('region_preview.tile_image', return_value=base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'))
@patch('region_preview.search')
def main(search, tile_image):
    app = App()
    try:
        # Check the smallest supported window, including the longer final timing
        # summary, so the process logs remain usable on every tab.
        app.geometry('1100x860')
        app.update()
        notebook = app.hybrid.master
        for index, tab in enumerate((app, app.mosaic, app.splitter, app.hybrid)):
            notebook.select(index)
            tab.process_display.start('Layout check')
            tab.process_display.finish()
            app.update()
            assert tab.log.winfo_height() >= 30, (index, tab.log.winfo_height())
            assert tab.log.winfo_rooty() + tab.log.winfo_height() <= app.winfo_rooty() + app.winfo_height()
        app.withdraw()
        app.update_idletasks()
        assert app.splitter.create_overview.get()
        assert app.splitter.overview_resolution.get() == '100'
        assert app.mosaic.maximum.get() == '0.40'
        assert app.mosaic.encoding.options()['compression'] == 'DEFLATE'
        hybrid = app.hybrid
        assert hybrid.source_values() == [DEFAULT_SOURCE]
        with tempfile.TemporaryDirectory() as temporary:
            paths = [Path(temporary) / 'first country.pbf', Path(temporary) / 'second country.pbf']
            for path in paths:
                path.write_bytes(b'fixture')
            hybrid.add_source_files([*paths, paths[0]])
            assert hybrid.source_values() == [DEFAULT_SOURCE, *map(str, paths)]
        assert hybrid.encoding.options()['compression'] == 'SOURCE'
        assert hybrid.use_tiff_bounds.get() and hybrid.render_rasters.get()
        assert not hybrid.road_groups['local'].get()
        app.pasted_bounds.set('0.514984,50.966211,-0.199127,51.109558')
        app.apply_bounds()
        expected = Bounds(-0.199127, 50.966211, 0.514984, 51.109558)
        assert app.values()[0] == expected
        picker = app.region_preview
        assert picker.bounds == expected
        place = Place('Horsham, West Sussex, UK', Bounds(-0.4, 51, -0.2, 51.1))
        search.return_value = [place]
        picker.query.set('Horsham, UK')
        picker.find()
        deadline = time.monotonic() + 3
        while picker.searching and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        assert picker.results == [place]
        assert not picker.searching
        picker.result.current(0)
        picker.choose()
        assert app.values()[0] == place.bounds
        picker.buffer.set('10')
        picker.choose()
        assert app.values()[0].west < place.bounds.west
        app.select_region(place.bounds)
        tiles = [Tile('ESA_WorldCover_10m_2021_v200_N51W001_S2RGBNIR.tif', 'https://test/tile', 1024)]
        with patch('app.preview', return_value=tiles):
            app.start_preview()
            assert not picker.enabled
            deadline = time.monotonic() + 3
            while app.busy and time.monotonic() < deadline:
                app.update()
                time.sleep(0.01)
            assert not app.busy and picker.enabled
        assert picker.coverage == [Bounds(-1, 51, 0, 52)]
        assert '1 available tiles' in picker.note.get()
        assert app.values()[0] == place.bounds  # Tile coverage must not replace the requested bounds.
        with tempfile.TemporaryDirectory() as temporary, patch('app.download', return_value='Downloaded') as download:
            app.folder.set(temporary)
            app.start_download()
            deadline = time.monotonic() + 3
            while app.busy and time.monotonic() < deadline:
                app.update()
                time.sleep(0.01)
            assert not app.busy
            download.assert_called_once()
            assert list(Path(temporary).glob('region-*.json'))
        picker.set_busy(True)
        picker.result.current(0)
        picker.choose()
        assert app.values()[0] == place.bounds
        picker.set_busy(False)
        picker.render()
        deadline = time.monotonic() + 3
        while picker.tile_running and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        assert picker.images, 'Expected background tile rendering'
        picker.press(SimpleNamespace(x=50, y=50, state=1))
        picker.release(SimpleNamespace(x=180, y=150))
        assert app.values()[0] != place.bounds
        assert not picker.coverage and not app.tiles
        selected = app.values()[0]
        search.side_effect = OSError('offline')
        picker.find()
        deadline = time.monotonic() + 3
        while picker.searching and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        assert 'search unavailable' in picker.note.get()
        assert app.values()[0] == selected
        tile_image.side_effect = OSError('offline')
        picker.render()
        deadline = time.monotonic() + 3
        while picker.tile_running and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        assert 'Street map unavailable' in picker.note.get()
        assert app.values()[0] == selected
        app.product.set('Land-cover classification')
        assert app.selection is None and not app.tiles
        app.select_region(expected)
        assert hybrid.region_bounds() == expected
        hybrid.coordinates.set('0.5,51.2,-0.2,50.9')
        assert hybrid.region_bounds() == Bounds(-0.2, 50.9, 0.5, 51.2)
        assert hybrid.coordinates.get() == '-0.2,50.9,0.5,51.2'
        # All tabs share timestamped status/log behavior and independently freeze
        # elapsed time on completion, including a failed/cancelled operation.
        for tab in (app, app.mosaic, app.splitter, app.hybrid):
            display = tab.process_display
            display.clock.monotonic = Mock(return_value=100)
            display.clock.now = Mock(return_value=datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc))
            display.start('Smoke operation')
            display.clock.monotonic.return_value = 112
            tab.status.set('Processing fixture')
            display.refresh()
            assert 'Elapsed 00:00:12' in display.timing.get()
            assert display.status_view.get() == '[15:00:00] Processing fixture'
            log = tab.log.get('1.0', 'end')
            assert '[2026-10-23 15:00:00 +0000 | +00:00:12] Processing fixture' in log
            display.append('First line\nSecond line')
            assert '| +00:00:12] Second line' in tab.log.get('1.0', 'end')
            tab.status.set('Cancelled / failed fixture')
            stamp = display.stamp()
            display.clock.monotonic.return_value = 200
            display.finish(stamp)
            assert display.clock.elapsed == 12
            assert 'Duration 00:00:12' in display.timing.get()
            assert 'ended; duration 00:00:12.' in tab.log.get('1.0', 'end')
            display.start('Next operation')
            assert display.clock.elapsed == 0
            display.finish()
    finally:
        app.destroy()
    print('Desktop layouts, place search, map selection, tile preview/download, timing and export defaults passed.')


if __name__ == '__main__':
    main()
