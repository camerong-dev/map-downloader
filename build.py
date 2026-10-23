"""Build on the target OS: python build.py (after installing requirements-build.txt)."""
import os
from pathlib import Path
import sys


def main():
    if sys.platform != 'win32' and not sys.platform.startswith('linux'):
        raise SystemExit('Build on Windows or Linux for that operating system.')
    import PyInstaller.__main__
    root = Path(__file__).resolve().parent
    args = ['--noconfirm', '--clean', '--onefile', '--windowed', '--name', 'ESARegionDownloader',
            '--distpath', str(root / 'dist'), '--workpath', str(root / 'build'), '--specpath', str(root)]
    for name in ['mosaic_worker.py', 'split_worker.py', 'hybrid_worker.py', 'hybrid_style.py', 'geofabrik_worker.py', 'raster_output.py']:
        args += ['--add-data', str(root / name) + os.pathsep + '.']
    PyInstaller.__main__.run(args + [str(root / 'app.py')])


if __name__ == '__main__':
    main()
