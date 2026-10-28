"""Read MMD Tools' local pose format without starting a simulation."""
import importlib
import sys

import bpy


def mmd_api():
    package = next((name for name in bpy.context.preferences.addons.keys() if name == 'mmd_tools' or name.endswith('.mmd_tools')), None)
    if not package and hasattr(bpy.types.Object, 'mmd_type'):
        package = next((name for name, module in sys.modules.copy().items()
                        if (name == 'mmd_tools' or name.endswith('.mmd_tools')) and hasattr(module, 'register')), None)
    if not package:
        raise ValueError('请先启用 MMD Tools，以读取 VPD 姿势。')
    return importlib.import_module(package+'.core.model')
