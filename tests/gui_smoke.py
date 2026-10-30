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
        app.geometry('1100x700')
        app.update()
        notebook = app.tabs
        for index, tab in enumerate((app, app.mosaic, app.splitter, app.hybrid, app.osm)):
            notebook.select(index)
            tab.process_display.start('Layout check')
            tab.process_display.finish()
            app.update()
            page = app.pages[index]
            assert tab.log.winfo_height() >= 160, (index, tab.log.winfo_height())
            assert page.content.winfo_height() > page.viewport.winfo_height()
            page.show_progress()
            app.update()
            assert page.viewport.yview()[0] > 0
            assert tab.log.winfo_rooty() >= page.viewport.winfo_rooty()

            assert tab.log.winfo_rooty() + tab.log.winfo_height() <= app.winfo_rooty() + app.winfo_height()
            page.viewport.yview_moveto(0)
            app.update()
            first, last = page.viewport.yview()
            assert first == 0
            child = tab.process_display.winfo_children()[0]
            page._wheel(SimpleNamespace(widget=child, delta=-120, num=None))
            assert page.viewport.yview()[0] > first
            before = page.viewport.yview()
            page._wheel(SimpleNamespace(widget=tab.log, delta=-120, num=None))
            assert page.viewport.yview() == before, 'Page stole log scrolling'
        assert app.region_preview.canvas.winfo_height() >= 440
        assert app.region_preview.canvas.winfo_width() >= 1000
        assert app.splitter.canvas.winfo_height() >= 440
        app.withdraw()
        app.update_idletasks()
        import tkinter as tk
        split = app.splitter
        split.info = {'width': 100, 'height': 100, 'bytes_per_pixel': 3}
        split.photo = tk.PhotoImage(master=app, width=100, height=100)
        split.selection_mode.set('Draw polygon')
        split.draw()
        left, top, iw, ih = split.image_box
        for u, v in [(0.1, 0.1), (0.9, 0.1), (0.1, 0.9)]:
            split.click(SimpleNamespace(x=left+iw*u, y=top+ih*v))
        split.finish_polygon()
        assert split.polygon_closed
        original = list(split.polygon)
        split.change_zoom(1)
        assert split.polygon == original
        split.undo_point()
        assert not split.polygon_closed and len(split.polygon) == 2
        split.clear_selection()
        assert not split.polygon
        split.info = split.photo = None
        split.selection_mode.set('Grid')
        split.draw()
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
        app.osm.coordinates.set('')
        try:
            app.osm.region_bounds()
            raise AssertionError('Pure OSM silently inherited an unselected area')
        except ValueError as error:
            assert 'Choose an export area first' in str(error)
        assert 'No export area chosen' in app.osm.area_summary.get()
        app.osm.copy_area()
        assert app.osm.region_bounds() == hybrid.region_bounds()
        assert app.osm.resolution.get() == '2'
        assert app.osm.pure_osm and app.osm.prefer_english.get()
        assert app.osm.map_style.get() == 'Light'
        assert app.osm.style_selector is not None and app.hybrid.style_selector is None
        app.osm.map_style.set('Dark')
        assert app.osm.encoding.options()['compression'] == 'DEFLATE'
        assert all(variable.get() for variable in app.osm.road_groups.values())
        assert not app.osm.imagery.get()
        with patch('hybrid_gui.start_worker') as worker:
            worker.return_value.communicate.return_value = ('{"bounds": {"west": 1, "south": 50, "east": 2, "north": 51}}', None)
            worker.return_value.returncode = 0
            hybrid.imagery.set('fixture.tif')
            app.osm.copy_area()
            assert app.osm.region_bounds() == Bounds(1, 50, 2, 51)
            hybrid.imagery.set('')
            with patch('hybrid_gui.filedialog.askopenfilename', return_value='fixture.tif'):
                app.osm.copy_tiff_area()
                assert app.osm.region_bounds() == Bounds(1, 50, 2, 51)
        # Exercise the GUI-to-worker contract without any network or GIS work.
        import json
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            saved = root / 'osm-manifest.json'
            saved.write_text(json.dumps({'bounds': {'west': 34, 'south': 31, 'east': 35, 'north': 32}}))
            with patch('hybrid_gui.filedialog.askopenfilename', return_value=str(saved)):
                app.osm.copy_saved_area()
            assert app.osm.region_bounds() == Bounds(34, 31, 35, 32)
            assert 'west 34.00000' in app.osm.area_summary.get()
            source = root / 'fixture.pbf'
            source.write_bytes(b'fixture')
            calls = []
            def fake_worker(qgis, script, job_path, report):
                job = json.loads(Path(job_path).read_text())
                calls.append(job)
                assert job['map_mode'] == 'osm'
                if Path(script).name == 'geofabrik_worker.py':
                    info = {'counts': {'roads': 1, 'places': 1, 'map_areas': 1, 'map_lines': 1},
                            'duplicates_removed': {'roads': 0, 'places': 0, 'map_areas': 0, 'map_lines': 0}}
                    (Path(job['folder']) / 'extract-info.json').write_text(json.dumps(info))
                else:
                    assert job['imagery'] == '' and job['resolution'] == 2
                    assert job['compression'] == 'DEFLATE' and job['prefer_english']
                    assert job['osm_style'] == 'Dark'
            app.osm.output.set(str(root / 'map'))
            with patch('hybrid_gui.obtain', return_value=(source, {'kind': 'fixture'})), patch.object(app.osm, 'run_worker', side_effect=fake_worker):
                app.osm.start()
                deadline = time.monotonic() + 5
                while app.osm.busy and time.monotonic() < deadline:
                    app.update()
                    time.sleep(.01)
                assert not app.osm.busy and len(calls) == 2
                assert json.loads((root / 'map' / 'osm-manifest.json').read_text())['display']['map_mode'] == 'osm'

        # All tabs share timestamped status/log behavior and independently freeze
        # elapsed time on completion, including a failed/cancelled operation.
        for tab in (app, app.mosaic, app.splitter, app.hybrid, app.osm):
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
