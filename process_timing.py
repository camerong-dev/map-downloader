"""Shared local-time process logs and monotonic elapsed-time displays."""
from datetime import datetime
import time
import tkinter as tk
from tkinter import ttk


def duration(seconds):
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


class OperationClock:
    def __init__(self, monotonic=None, now=None):
        self.monotonic = monotonic or time.monotonic
        self.now = now or (lambda: datetime.now().astimezone())
        self.started = self.ended = None
        self._start = self._end = None

    @property
    def running(self):
        return self._start is not None and self._end is None

    @property
    def elapsed(self):
        if self._start is None:
            return 0
        end = self.monotonic() if self._end is None else self._end
        return max(0, end - self._start)

    def start(self):
        self.started = self.now()
        self._start = self.monotonic()
        self.ended = self._end = None

    def stamp(self):
        """Safe to capture in a worker thread without calling any Tk APIs."""
        return self.monotonic(), self.now()

    def stop(self, stamp=None):
        if self.running:
            self._end, self.ended = stamp if stamp is not None else self.stamp()

    def summary(self):
        if self.started is None:
            return 'Timing: ready'
        start = self.started.strftime('%Y-%m-%d %H:%M:%S %z')
        if self.running:
            return f'Started {start} • Elapsed {duration(self.elapsed)}'
        return f'Started {start} • Ended {self.ended:%Y-%m-%d %H:%M:%S %z} • Duration {duration(self.elapsed)}'


class ProcessDisplay(ttk.Frame):
    """Observe an existing status variable; callers keep all existing status logic."""
    def __init__(self, parent, status, wraplength=760):
        super().__init__(parent)
        self.clock = OperationClock()
        self.status = status
        self.log = None
        self.operation = 'Process'
        self.status_view = tk.StringVar(value=status.get())
        self.timing = tk.StringVar(value=self.clock.summary())
        ttk.Label(self, textvariable=self.status_view, wraplength=wraplength).pack(anchor='w')
        ttk.Label(self, textvariable=self.timing, wraplength=wraplength).pack(anchor='w')
        self._trace = status.trace_add('write', self._status_changed)
        self.bind('<Destroy>', self._destroyed, add='+')

    def _destroyed(self, event):
        if event.widget is self:
            try:
                self.status.trace_remove('write', self._trace)
            except tk.TclError:
                pass

    def _status_changed(self, *_):
        message = self.status.get()
        self.status_view.set(f'[{self.clock.now():%H:%M:%S}] {message}' if message else '')
        self.append(message)

    def append(self, message, include_elapsed=False, stamp=None):
        if self.log is None or not str(message).strip():
            return
        timestamp = (stamp[1] if stamp is not None else self.clock.now()).strftime('%Y-%m-%d %H:%M:%S %z')
        elapsed = f' | +{duration(self.clock.elapsed)}' if self.clock.running or include_elapsed else ''
        lines = [f'[{timestamp}{elapsed}] {line}\n' for line in str(message).splitlines() if line.strip()]
        self.log.configure(state='normal')
        self.log.insert('end', ''.join(lines))
        self.log.see('end')
        self.log.configure(state='disabled')

    def start(self, operation):
        self.operation = operation
        self.clock.start()
        self.append(f'{operation} started.')
        self.refresh()

    def stamp(self):
        return self.clock.stamp()

    def finish(self, stamp=None):
        if self.clock.running:
            self.clock.stop(stamp)
            self.append(f'{self.operation} ended; duration {duration(self.clock.elapsed)}.', include_elapsed=True, stamp=stamp)
            self.refresh()

    def refresh(self):
        value = self.clock.summary()
        if self.timing.get() != value:
            self.timing.set(value)
