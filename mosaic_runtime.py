"""Locate and launch QGIS's Python on Windows and native Linux installations."""
import os
from pathlib import Path
import subprocess
import sys
import shutil


def find_qgis():
    if sys.platform.startswith('linux'):
        roots = []
        if os.environ.get('QGIS_PREFIX_PATH'):
            roots.append(Path(os.environ['QGIS_PREFIX_PATH']))
        binary = shutil.which('qgis')
        if binary:
            roots.append(Path(binary).resolve().parent.parent)
        roots += [Path('/usr'), Path('/usr/local')]
        return next((str(p) for p in roots if (p / 'share/qgis').is_dir() and (p / 'bin/python3').is_file()), '')
    roots = list(Path(os.environ.get('ProgramFiles', 'C:/Program Files')).glob('QGIS*'))
    roots += [Path('C:/OSGeo4W'), Path('C:/OSGeo4W64')]
    return next((str(p) for p in sorted(roots, reverse=True) if (p / 'bin/python3.exe').is_file()), '')


def qgis_command(root, script, *args, platform=None):
    platform = platform or sys.platform
    root = Path(root).resolve()
    if platform.startswith('linux'):
        python = Path(os.environ.get('ESA_QGIS_PYTHON', str(root / 'bin/python3')))
        if not python.is_file() or not (root / 'share/qgis').is_dir():
            raise ValueError('Select the native QGIS prefix (usually /usr), with bin/python3 and share/qgis. Install QGIS, Python bindings, GDAL and NumPy through your Linux package manager.')
        env = dict(os.environ)
        for name in ('PYTHONHOME', 'PYTHONEXECUTABLE', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH'):
            env.pop(name, None)
        modules = root / 'share/qgis/python'
        env.update(QGIS_PREFIX_PATH=str(root), PYTHONPATH=str(modules) if modules.is_dir() else '',
                   PYTHONUTF8='1', PYTHONUNBUFFERED='1', QT_QPA_PLATFORM='offscreen')
        if getattr(sys, 'frozen', False):
            # Restore the host library path before launching system QGIS/Python.
            original = env.get('LD_LIBRARY_PATH_ORIG')
            if original is None:
                env.pop('LD_LIBRARY_PATH', None)
            else:
                env['LD_LIBRARY_PATH'] = original
            bundled = str(getattr(sys, '_MEIPASS', ''))
            if bundled:
                env['PATH'] = os.pathsep.join(p for p in env.get('PATH', '').split(os.pathsep) if not p.startswith(bundled))
        return [str(python), str(script), *map(str, args)], env
    if platform != 'win32':
        raise ValueError('This application supports Windows and Linux with a native QGIS installation.')
    python = root / 'bin/python3.exe'
    homes = sorted((root / 'apps').glob('Python3*'))
    homes = [p for p in homes if (p / 'Lib').is_dir()]
    if not python.is_file() or not homes:
        raise ValueError('Choose the QGIS installation folder containing bin/python3.exe and apps/Python3*.')
    env = dict(os.environ)
    env.update(PYTHONHOME=str(homes[-1]), PYTHONPATH='', PYTHONUTF8='1', PYTHONUNBUFFERED='1',
               GDAL_DATA=str(root / 'apps/gdal/share/gdal'),
               GDAL_DRIVER_PATH=str(root / 'apps/gdal/lib/gdalplugins'),
               PROJ_DATA=str(root / 'share/proj'),
               PATH=str(root / 'bin') + os.pathsep + os.environ.get('SystemRoot', 'C:/Windows') + '/System32')
    prefixes = [p for p in (root / 'apps/qgis', root / 'apps/qgis-ltr') if (p / 'python').is_dir()]
    if prefixes:
        prefix = prefixes[0]
        qt = root / 'apps/qt6' if (root / 'apps/qt6').exists() else root / 'apps/Qt5'
        env.update(QGIS_PREFIX_PATH=str(prefix), PYTHONPATH=str(prefix / 'python'),
                   QT_QPA_PLATFORM='offscreen', QT_PLUGIN_PATH=str(qt / 'plugins'),
                   PATH=os.pathsep.join([str(prefix / 'bin'), str(qt / 'bin'), env['PATH']]))
    env.pop('PROJ_LIB', None)
    return [str(python), str(script), *map(str, args)], env


def start_worker(root, script, *args):
    command, env = qgis_command(root, script, *args)
    # A frozen GUI must not pass its bundled DLL search directory to QGIS Python.
    frozen = sys.platform == 'win32' and getattr(sys, 'frozen', False)
    if frozen:
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        return subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace',
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    finally:
        if frozen:
            ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)
