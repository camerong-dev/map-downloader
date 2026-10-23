from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

from process_timing import OperationClock, duration


class TimingTests(unittest.TestCase):
    def test_duration_does_not_wrap_at_a_day(self):
        for seconds, expected in [(0, '00:00:00'), (61.9, '00:01:01'),
                                  (3601, '01:00:01'), (90000, '25:00:00'), (-1, '00:00:00')]:
            with self.subTest(seconds=seconds):
                self.assertEqual(duration(seconds), expected)

    def test_live_elapsed_final_duration_and_restart(self):
        monotonic = Mock(return_value=0)
        now = Mock(return_value=datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc))
        clock = OperationClock(monotonic, now)
        self.assertEqual(clock.summary(), 'Timing: ready')
        clock.start()
        monotonic.return_value = 65
        self.assertTrue(clock.running)
        self.assertIn('Elapsed 00:01:05', clock.summary())
        now.return_value += timedelta(seconds=65)
        clock.stop()
        monotonic.return_value = 500
        self.assertFalse(clock.running)
        self.assertEqual(clock.elapsed, 65)
        self.assertIn('Duration 00:01:05', clock.summary())
        clock.start()
        self.assertEqual(clock.elapsed, 0)
        self.assertTrue(clock.running)

    def test_wall_clock_changes_do_not_change_elapsed(self):
        monotonic = Mock(return_value=100)
        now = Mock(return_value=datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc))
        clock = OperationClock(monotonic, now)
        clock.start()
        now.return_value -= timedelta(hours=1)
        monotonic.return_value = 110
        self.assertEqual(clock.elapsed, 10)
        clock.stop()
        self.assertEqual(clock.elapsed, 10)

    def test_worker_completion_ignores_delayed_gui_updates(self):
        monotonic = Mock(return_value=100)
        now = Mock(return_value=datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc))
        clock = OperationClock(monotonic, now)
        clock.start()
        monotonic.return_value = 112
        now.return_value += timedelta(seconds=12)
        stamp = clock.stamp()
        monotonic.return_value = 300
        now.return_value += timedelta(seconds=188)
        clock.stop(stamp)
        self.assertEqual(clock.elapsed, 12)
        self.assertEqual(clock.ended, stamp[1])
