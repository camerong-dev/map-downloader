"""Shared export compression controls (no GIS imports in the GUI process)."""
import tkinter as tk
from tkinter import ttk


class CompressionControls(ttk.Frame):
    def __init__(self, parent, allow_source=False):
        super().__init__(parent)
        self.compression = tk.StringVar(value='Match satellite TIFF' if allow_source else 'Lossless (DEFLATE)')
        self.quality = tk.StringVar(value='85')
        self.busy = False
        ttk.Label(self, text='Export compression').pack(side='left', padx=(0, 8))
        self.combo = ttk.Combobox(self, textvariable=self.compression, state='readonly', width=24,
                                  values=(['Match satellite TIFF'] if allow_source else []) + ['Lossless (DEFLATE)', 'JPEG (rendered RGB)'])
        self.combo.pack(side='left')
        self.combo.bind('<<ComboboxSelected>>', lambda _: self.update_state())
        ttk.Label(self, text='JPEG quality').pack(side='left', padx=(12, 5))
        self.spin = ttk.Spinbox(self, from_=1, to=100, textvariable=self.quality, width=4)
        self.spin.pack(side='left')
        self.update_state()

    def update_state(self):
        self.combo.configure(state='disabled' if self.busy else 'readonly')
        self.spin.configure(state='normal' if not self.busy and self.compression.get().startswith('JPEG') else 'disabled')

    def set_busy(self, busy):
        self.busy = busy
        self.update_state()

    def options(self):
        if self.compression.get() == 'Match satellite TIFF':
            return {'compression': 'SOURCE', 'jpeg_quality': 85}
        compression = 'JPEG' if self.compression.get().startswith('JPEG') else 'DEFLATE'
        quality = 85
        if compression == 'JPEG':
            try:
                quality = int(self.quality.get())
            except ValueError:
                raise ValueError('JPEG quality must be a whole number from 1 to 100.')
            if not 1 <= quality <= 100:
                raise ValueError('JPEG quality must be between 1 and 100.')
        return {'compression': compression, 'jpeg_quality': quality}
