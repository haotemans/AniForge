"""Load the checkout even when another AniForge version is enabled locally."""
import importlib
import sys
from pathlib import Path


def load_addon():
    import addon_utils
    root = Path(__file__).resolve().parents[1]
    addon_utils.disable('mmd_lora_studio', default_set=False)
    for name in list(sys.modules):
        if name == 'mmd_lora_studio' or name.startswith('mmd_lora_studio.'):
            del sys.modules[name]
    sys.path.insert(0, str(root))
    addon = importlib.import_module('mmd_lora_studio')
    assert Path(addon.__file__).resolve().parent == root / 'mmd_lora_studio'
    print('ANIFORGE_SOURCE', addon.__file__, flush=True)
    return addon
