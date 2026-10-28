"""Install user-supplied AnimChar ZIPs into Blender's local add-on directory.

Run with the normal Python interpreter. Vendor files remain outside the repo.
Enable AnimChar Tools in Blender after installation; no key is handled here.
"""
import argparse
from pathlib import Path
import shutil
from datetime import datetime
from zipfile import ZipFile


def install(archive, destination, package):
    archive, destination = Path(archive), Path(destination).resolve()
    target = destination/package
    with ZipFile(archive) as z:
        members = [n for n in z.namelist() if n.startswith(package+'/')]
        if package+'/__init__.py' not in members:
            raise ValueError('ZIP 中不存在 '+package)
        for name in members:
            if not (destination/name).resolve().is_relative_to(target):
                raise ValueError('ZIP 路径越界。')
        destination.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = destination.parent/'aniforge_addon_backups'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')/package
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(target, backup)
        for name in members:
            z.extract(name, destination)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--updater', required=True)
    parser.add_argument('--addon', required=True)
    parser.add_argument('--destination', required=True)
    args = parser.parse_args()
    for archive, package in ((args.updater, 'AnimCharToolsUpdater'), (args.addon, 'AnimCharTools')):
        print(install(archive, args.destination, package))
