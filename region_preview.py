"""Interactive Tk map: on-demand street tiles, place lookup and region selection."""
import base64
import math
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

from downloader import Bounds, candidates, parse_region
from place_search import WebCache, buffered, project, search, tile_bounds, tile_image, unproject


class RegionPreview(ttk.LabelFrame):
    def __init__(self, parent, on_region):
        super().__init__(parent, text='Find a place & preview the area', padding=8)
        self.on_region = on_region
        self.cache = WebCache()
        self.events = queue.Queue()
        self.results = []
        self.bounds = None
        self.coverage = []
        self.zoom = 4
        self.centre = project(0, 30, self.zoom)
        self.images = {}
        self.enabled = True
        self.online = False
        self.searching = False
        self.generation = 0
        self.tile_running = False
        self.pending_view = None
        self.render_after = None
        self.drag = None
        self.destroyed = False
        row = ttk.Frame(self)
        row.pack(fill='x')
        self.query = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.query)
        self.entry.pack(side='left', fill='x', expand=True)
        self.entry.bind('<Return>', lambda _: self.find())
        self.search_button = ttk.Button(row, text='Search', command=self.find)
        self.search_button.pack(side='left', padx=(6, 0))
        self.result = ttk.Combobox(self, state='readonly', values=[], width=36)
        self.result.pack(fill='x', pady=5)
        self.result.bind('<<ComboboxSelected>>', self.choose)
        tools = ttk.Frame(self)
        tools.pack(fill='x')
        ttk.Label(tools, text='Buffer (km)').pack(side='left')
        self.buffer = tk.StringVar(value='0')
        self.buffer_entry = ttk.Spinbox(tools, from_=0, to=1000, textvariable=self.buffer, width=5)
        self.buffer_entry.pack(side='left', padx=5)
        self.buffer_button = ttk.Button(tools, text='Apply to place', command=self.choose)
        self.buffer_button.pack(side='left')
        ttk.Button(tools, text='−', width=2, command=lambda: self.change_zoom(-1)).pack(side='right')
        ttk.Button(tools, text='+', width=2, command=lambda: self.change_zoom(1)).pack(side='right', padx=4)
        ttk.Button(tools, text='Fit area', command=self.fit).pack(side='right')
        self.canvas = tk.Canvas(self, height=160, background='#e4eaf0', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, pady=(6, 4))
        self.canvas.bind('<Configure>', self.schedule)
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.motion)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.canvas.bind('<MouseWheel>', lambda e: self.change_zoom(1 if e.delta > 0 else -1))
        self.canvas.bind('<Button-4>', lambda _: self.change_zoom(1))
        self.canvas.bind('<Button-5>', lambda _: self.change_zoom(-1))
        ttk.Label(self, text='Drag to pan • Wheel to zoom • Shift + drag to draw an area',
                  style='Muted.TLabel', wraplength=510).pack(anchor='w')
        ttk.Label(self, text='Blue: requested rectangle   Orange: available ESA tiles',
                  style='Muted.TLabel', wraplength=510).pack(anchor='w')
        self.note = tk.StringVar(value='Search for a country, county or town, or apply coordinates.')
        ttk.Label(self, textvariable=self.note, wraplength=510).pack(anchor='w', pady=(3, 0))
        credit = ttk.Label(self, text='© OpenStreetMap contributors • ODbL | Search: Photon',
                           cursor='hand2', style='Muted.TLabel')
        credit.pack(anchor='w', pady=(3, 0))
        credit.bind('<Button-1>', lambda _: webbrowser.open('https://www.openstreetmap.org/copyright'))
        self.bind('<Destroy>', self.closed, add='+')
        self.poll_after = self.after(100, self.poll)

    def closed(self, event):
        if event.widget is self:
            self.destroyed = True
            self.after_cancel(self.poll_after)
            if self.render_after:
                self.after_cancel(self.render_after)

    def set_busy(self, busy):
        self.enabled = not busy
        for widget in (self.entry, self.buffer_entry, self.buffer_button):
            widget.configure(state='disabled' if busy else 'normal')
        self.search_button.configure(state='disabled' if busy or self.searching else 'normal')
        self.result.configure(state='disabled' if busy else 'readonly')

    def find(self):
        if not self.enabled or self.searching:
            return
        query = self.query.get().strip()
        if len(query) < 2:
            self.note.set('Enter at least two characters, for example Horsham, UK.')
            return
        self.searching = True
        self.search_button.configure(state='disabled')
        self.note.set('Searching places…')
        def work():
            try:
                self.events.put(('places', search(query, self.cache)))
            except Exception as error:
                self.events.put(('search_error', str(error)))
        threading.Thread(target=work, daemon=True).start()

    def choose(self, _=None):
        if not self.enabled:
            return
        index = self.result.current()
        if not 0 <= index < len(self.results):
            self.note.set('Search, then choose a matching place first.')
            return
        try:
            place = self.results[index]
            bounds = buffered(place.bounds, self.buffer.get())
            self.on_region(bounds)
        except ValueError as error:
            messagebox.showerror('Cannot select area', str(error))
            return
        self.note.set(('Suggested 5 km area around the place centre. ' if place.approximate else
                       'Place bounding rectangle selected. ') + 'Click Preview region to check available tiles and size.')

    def set_region(self, bounds, product, online=True):
        self.bounds = bounds
        self.coverage = []
        self.online = online
        try:
            count = len(candidates(bounds, 2021, product))
            self.note.set(f'Requested rectangle • {count} possible ESA tiles. Preview region checks availability and size.')
        except ValueError as error:
            self.note.set(str(error))
        self.fit()

    def set_tiles(self, tiles):
        self.coverage = [tile_bounds(tile.name) for tile in tiles]
        self.note.set(f'{len(tiles)} available tiles • {sum(t.size for t in tiles) / 1024**2:,.1f} MiB. Downloads include full tiles.')
        self.fit()

    def fit(self):
        if self.bounds is None:
            return
        w, h = max(250, self.canvas.winfo_width() - 40), max(150, self.canvas.winfo_height() - 40)
        boxes = [self.bounds, *self.coverage]
        b = Bounds(min(b.west for b in boxes), min(b.south for b in boxes),
                   max(b.east for b in boxes), max(b.north for b in boxes))
        for zoom in range(15, -1, -1):
            x1, y1 = project(b.west, b.north, zoom)
            x2, y2 = project(b.east, b.south, zoom)
            if x2 - x1 <= w and y2 - y1 <= h:
                break
        self.zoom = zoom
        self.centre = ((x1 + x2) / 2, (y1 + y2) / 2)
        self.schedule()

    def change_zoom(self, amount):
        zoom = max(0, min(15, self.zoom + amount))
        factor = 2 ** (zoom - self.zoom)
        self.centre = tuple(v * factor for v in self.centre)
        self.zoom = zoom
        self.schedule()

    def origin(self):
        return self.centre[0] - self.canvas.winfo_width() / 2, self.centre[1] - self.canvas.winfo_height() / 2

    def press(self, event):
        self.drag = (event.x, event.y, self.centre, bool(event.state & 1) and self.enabled)

    def motion(self, event):
        if self.drag is None:
            return
        x, y, centre, drawing = self.drag
        if drawing:
            self.canvas.delete('selection')
            self.canvas.create_rectangle(x, y, event.x, event.y, outline='#2164ad', width=3, tags='selection')
        else:
            self.centre = (centre[0] + x - event.x, centre[1] + y - event.y)
            self.schedule()

    def release(self, event):
        if self.drag is None:
            return
        x, y, _, drawing = self.drag
        self.drag = None
        self.canvas.delete('selection')
        if drawing and self.enabled and abs(x - event.x) > 5 and abs(y - event.y) > 5:
            ox, oy = self.origin()
            a = unproject(ox + x, oy + y, self.zoom)
            b = unproject(ox + event.x, oy + event.y, self.zoom)
            try:
                self.on_region(parse_region(f'{a[0]},{a[1]},{b[0]},{b[1]}'))
            except ValueError as error:
                self.note.set(str(error))
        self.schedule()

    def schedule(self, _=None):
        if self.render_after:
            self.after_cancel(self.render_after)
        self.render_after = self.after(250, self.render)

    def render(self):
        self.render_after = None
        self.generation += 1
        generation = self.generation
        self.canvas.delete('all')
        self.images.clear()
        ox, oy = self.origin()
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        size = 2 ** self.zoom
        needed = []
        for x in range(max(0, math.floor(ox / 256)), min(size, math.ceil((ox + w) / 256))):
            for y in range(max(0, math.floor(oy / 256)), min(size, math.ceil((oy + h) / 256))):
                px, py = x * 256 - ox, y * 256 - oy
                self.canvas.create_rectangle(px, py, px + 256, py + 256, outline='#d0d9e3')
                needed.append((self.zoom, x, y, px, py))
        if not self.online:
            self.canvas.create_text(w / 2, h / 2, text='Search or apply coordinates to load the street map.', width=max(100, w - 30))
        self.draw_bounds()
        # One worker, no prefetch. Superseded views stop after the current tile.
        self.pending_view = (generation, needed) if self.online else None
        self.start_tiles()

    def draw_bounds(self):
        self.canvas.delete('bounds')
        ox, oy = self.origin()
        for b, colour, width in [*( (b, '#d77b00', 2) for b in self.coverage),
                                *([(self.bounds, '#2164ad', 3)] if self.bounds else [])]:
            x1, y1 = project(b.west, b.north, self.zoom)
            x2, y2 = project(b.east, b.south, self.zoom)
            self.canvas.create_rectangle(x1 - ox, y1 - oy, x2 - ox, y2 - oy,
                                         outline=colour, width=width, tags='bounds')

    def start_tiles(self):
        if self.tile_running or not self.pending_view:
            return
        generation, needed = self.pending_view
        self.pending_view = None
        self.tile_running = True
        def work():
            try:
                for z, x, y, px, py in needed:
                    if self.destroyed or generation != self.generation:
                        break
                    data = tile_image(z, x, y, self.cache)
                    self.events.put(('tile', (generation, x, y, px, py, data)))
            except Exception as error:
                self.events.put(('map_error', (generation, str(error))))
            finally:
                self.events.put(('tiles_done', None))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        for _ in range(40):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind in ('places', 'search_error'):
                self.searching = False
                self.set_busy(not self.enabled)
                if kind == 'places':
                    self.results = value
                    self.result.configure(values=[p.label for p in value])
                    self.result.set('Choose a matching place' if value else '')
                    self.note.set(f'{len(value)} matches. Choose the correct place below the search box.' if value else
                                  'No suitable areas found. Try adding a country, or use coordinates.')
                else:
                    self.note.set(f'Place search unavailable: {value}. You can still use coordinates.')
            elif kind == 'tile':
                generation, x, y, px, py, data = value
                if generation == self.generation:
                    try:
                        photo = tk.PhotoImage(data=base64.b64encode(data))
                        self.images[x, y] = photo
                        self.canvas.create_image(px, py, image=photo, anchor='nw', tags='map')
                        self.canvas.tag_lower('map')
                        self.canvas.tag_raise('bounds')
                    except tk.TclError:
                        self.note.set('Street map image unavailable. Area selection still works.')
            elif kind == 'map_error' and value[0] == self.generation:
                self.note.set('Street map unavailable. Coordinates and ESA tile checks still work.')
            elif kind == 'tiles_done':
                self.tile_running = False
                self.start_tiles()
        self.poll_after = self.after(100, self.poll)
