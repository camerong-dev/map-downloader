"""Shared desktop styling and small, consistent layout helpers."""
from tkinter import ttk
from tkinter import font


def configure(root):
    style = ttk.Style(root)
    style.theme_use('clam')
    for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont'):
        font.nametofont(name).configure(family='Segoe UI', size=10)
    style.configure('.', background='#f4f6f9', foreground='#202e40')
    style.configure('TFrame', background='#f4f6f9')
    style.configure('TLabel', background='#f4f6f9')
    style.configure('Title.TLabel', font=('Segoe UI', 18, 'bold'))
    style.configure('Muted.TLabel', foreground='#526278')
    style.configure('TButton', padding=(10, 4))
    style.configure('Primary.TButton', background='#2164ad', foreground='white')
    style.map('Primary.TButton', background=[('disabled', '#dce3ed'), ('active', '#174c87')],
              foreground=[('disabled', '#778497')])
    style.configure('TEntry', padding=4, fieldbackground='white')
    style.configure('TNotebook', borderwidth=0, tabmargins=(12, 8, 12, 0))
    style.configure('TNotebook.Tab', padding=(16, 9), background='#e5eaf1')
    style.map('TNotebook.Tab', background=[('selected', '#f4f6f9')],
              foreground=[('selected', '#155a9e')])
    style.configure('TLabelframe', bordercolor='#d4dde8', relief='solid')
    style.configure('TLabelframe.Label', font=('Segoe UI', 10, 'bold'), foreground='#314b6b')
    style.configure('Horizontal.TProgressbar', background='#2164ad', troughcolor='#e0e7f0')
    root.option_add('*Text.background', '#ffffff')
    root.option_add('*Text.foreground', '#26364b')
    root.option_add('*Text.font', ('Consolas', 9))
    root.option_add('*Text.relief', 'flat')
    root.option_add('*Text.highlightThickness', 1)
    root.option_add('*Text.highlightBackground', '#d4dde8')
    root.option_add('*Listbox.background', '#ffffff')
    root.option_add('*Listbox.selectBackground', '#2164ad')
    root.option_add('*Listbox.selectForeground', '#ffffff')
    root.option_add('*Listbox.relief', 'flat')


def section(parent, title):
    frame = ttk.LabelFrame(parent, text=title, padding=(10, 6))
    frame.pack(fill='x', pady=(4, 6))
    return frame
