"""Alpha-based trimming and white compositing, using Blender's bundled NumPy."""
from pathlib import Path

import bpy
import numpy as np

from .core import padded_crop


def crop_bounds(rgba, padding_percent=3):
    # Keep every nonzero 16-bit alpha value, including antialiased hair and lace.
    mask = rgba[:, :, 3] > 0
    rows = np.flatnonzero(mask.any(axis=1))
    columns = np.flatnonzero(mask.any(axis=0))
    if not len(rows) or not len(columns):
        raise ValueError('相机中没有可见角色，未保存空白训练图片。')
    height, width = rgba.shape[:2]
    return padded_crop(width, height, (int(columns[0]), int(rows[0]), int(columns[-1])+1, int(rows[-1])+1), padding_percent)


def white_crop(rgba, padding_percent=3, enabled=True, premultiplied=False):
    if rgba.ndim != 3 or rgba.shape[2] != 4 or not np.isfinite(rgba).all():
        raise ValueError('渲染图像的颜色或透明通道无效。')
    height, width = rgba.shape[:2]
    bounds = crop_bounds(rgba, padding_percent)
    if not enabled:
        bounds = (0, 0, width, height)
    left, bottom, right, top = bounds
    result = rgba[bottom:top, left:right].copy()
    alpha = np.clip(result[:, :, 3:4], 0, 1)
    # Blender's float image buffer exposes associated scene-linear RGB, even
    # when the PNG's alpha_mode is STRAIGHT. Multiplying alpha twice creates
    # dark outlines on hair/lace; byte buffers instead expose straight sRGB.
    if premultiplied:
        result[:, :, :3] = np.where(alpha > 0, result[:, :, :3], 0) + (1-alpha)
    else:
        result[:, :, :3] = result[:, :, :3]*alpha + (1-alpha)
    result[:, :, 3] = 1
    return result, {'source_size': [width, height], 'crop_box_bottom_left': list(bounds),
                    'output_size': [right-left, top-bottom], 'padding_percent': padding_percent,
                    'auto_crop': enabled, 'background': 'white', 'mask': 'nonzero_alpha'}


def save_white_crop(scene, source_path, destination, padding_percent=3, enabled=True):
    source = output = None
    try:
        source = bpy.data.images.load(str(source_path), check_existing=False)
        source.alpha_mode = 'STRAIGHT'
        width, height = source.size
        pixels = np.empty(width*height*4, dtype=np.float32)
        source.pixels.foreach_get(pixels)
        rgba = pixels.reshape(height, width, 4)
        if not source.is_float:
            rgb = rgba[:, :, :3]
            rgba[:, :, :3] = np.where(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)**2.4)
        result, metadata = white_crop(rgba, padding_percent, enabled, premultiplied=source.is_float)
        out_width, out_height = metadata['output_size']
        output = bpy.data.images.new('AniForge White Crop', width=out_width, height=out_height,
                                     alpha=True, float_buffer=True)
        output.alpha_mode = 'STRAIGHT'
        output.pixels.foreach_set(np.ascontiguousarray(result).ravel())
        destination = Path(destination)
        temporary = Path(source_path).with_name('white_cropped.png')
        output.save_render(str(temporary), scene=scene)
        temporary.replace(destination)
        return metadata
    finally:
        for image in (output, source):
            if image is not None:
                bpy.data.images.remove(image)


def show_output(context, path):
    """Show the finished white crop instead of the intermediate transparent pass."""
    if bpy.app.background:
        return
    areas = [area for window in context.window_manager.windows for area in window.screen.areas
             if area.type == 'IMAGE_EDITOR' and area.spaces.active.image is not None
             and (area.spaces.active.image.type == 'RENDER_RESULT' or area.spaces.active.image.get('aniforge_output_preview'))]
    if not areas:
        return
    preview = next((image for image in bpy.data.images if image.get('aniforge_output_preview')), None)
    if preview is None:
        preview = bpy.data.images.load(str(path), check_existing=False)
        preview.name = 'AniForge Render Preview'
        preview['aniforge_output_preview'] = True
    else:
        preview.filepath = str(path)
        preview.reload()
    preview.use_view_as_render = False
    for area in areas:
        area.spaces.active.image = preview
        area.tag_redraw()
