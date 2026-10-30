"""White compositing and contour crops with synthetic pixels; never render."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
import numpy as np
from mmd_lora_studio.output import white_crop, save_white_crop

rgba = np.zeros((80, 100, 4), dtype=np.float32)
rgba[20:50, 30:60] = (1, 1, 1, 1)  # Opaque white clothing must remain foreground.
rgba[60, 65] = (1, 1, 1, 1/65535)  # Keep very faint hair beyond the main body.
rgba[22, 32] = (1, 0, 0, .5)
rgba[0, 0] = (1, 1, 1, 0)  # White pixels with zero alpha are still background.
result, meta = white_crop(rgba, 3)
assert meta['crop_box_bottom_left'] == [28, 18, 68, 63], meta
assert meta['output_size'] == [40, 45]
assert np.all(result[:, :, 3] == 1)
assert np.all(result[0, 0] == 1)
assert np.allclose(result[4, 4], (1, .5, .5, 1))
associated = rgba.copy(); associated[:, :, :3] *= associated[:, :, 3:4]
premul_result, _ = white_crop(associated, 3, premultiplied=True)
assert np.allclose(premul_result, result)
_, full = white_crop(rgba, 3, enabled=False)
assert full['output_size'] == [100, 80]
try:
    white_crop(np.zeros_like(rgba))
except ValueError:
    pass
else:
    raise AssertionError('An empty training image was accepted')

scene = bpy.context.scene
scene.view_settings.view_transform = 'Standard'; scene.view_settings.look = 'None'
scene.view_settings.exposure = 0; scene.view_settings.gamma = 1
scene.view_settings.use_curve_mapping = False
source_count = len(bpy.data.images)
with tempfile.TemporaryDirectory() as directory:
    raw = Path(directory)/'rgba.png'
    output = Path(directory)/'white.png'
    image = bpy.data.images.new('Fixture', width=100, height=80, alpha=True, float_buffer=True)
    image.alpha_mode = 'STRAIGHT'
    image.pixels.foreach_set(rgba.ravel())
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'; scene.render.image_settings.color_depth = '16'
    image.save_render(str(raw), scene=scene)
    bpy.data.images.remove(image)
    scene.render.image_settings.color_mode = 'RGB'; scene.render.image_settings.color_depth = '8'
    meta = save_white_crop(scene, raw, output, 3)
    assert meta['output_size'] == [40, 45], meta
    loaded = bpy.data.images.load(str(output), check_existing=False)
    pixels = np.array(loaded.pixels[:], dtype=np.float32).reshape(45, 40, 4)
    assert np.all(pixels[0, 0] == 1), pixels[0, 0]
    # Blender exposes the saved 8-bit PNG buffer as sRGB byte values.
    assert np.allclose(pixels[4, 4], (1, 188/255, 188/255, 1), atol=1/255), pixels[4, 4]
    assert np.allclose(pixels[3, 3], (1, 1, 1, 1)), pixels[3, 3]
    bpy.data.images.remove(loaded)
    assert not (Path(directory)/'white_cropped.png').exists()
assert len(bpy.data.images) == source_count
print('OUTPUT_OK: opaque white clothing, faint hair, alpha-aware crop, linear compositing and PNG roundtrip; zero renders')
