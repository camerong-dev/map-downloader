"""Desktop smoke checks; called explicitly rather than during unit discovery."""
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from downloader import Bounds
from geofabrik import DEFAULT_SOURCE


def main():
    app = App()
    try:
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
    print('Desktop tab defaults, coordinate normalization and compression controls passed.')


if __name__ == '__main__':
    main()
