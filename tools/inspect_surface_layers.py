"""Trace eye/collar surface layers and sampled base RGBA without rendering."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bpy
from mathutils import Vector
from mathutils.geometry import barycentric_transform
import mmd_lora_studio as addon

addon.register()
s = bpy.context.scene.ml_settings
s.armature = next(o for o in bpy.context.scene.objects if o.type == 'ARMATURE')
s.output = str(Path(__file__).resolve().parents[1] / 'test_output/surface_diagnostics')
session = addon.RenderSession(bpy.context)
session.setup(bpy.context)
session.prepare_shot(bpy.context, dict(group='portrait', index=1, azimuth=0, expression='neutral', pose='relaxed'))
cam = session.camera
right = cam.matrix_world.to_quaternion() @ Vector((1, 0, 0))
up = cam.matrix_world.to_quaternion() @ Vector((0, 1, 0))
direction = cam.matrix_world.to_quaternion() @ Vector((0, 0, -1))
dg = bpy.context.evaluated_depsgraph_get()
game = sys.argv[sys.argv.index('--')+1]
samples = [(x, y) for x in (.38, .41, .59, .62) for y in (.455, .47, .49)] if game == 'WUWA' else [(.5, y) for y in (.58, .62, .65, .68, .71)]
rows = []
for x, y in samples:
    origin = cam.location + right * (x-.5) * cam.data.ortho_scale + up * (.5-y) * cam.data.ortho_scale
    layers = []
    for _ in range(14):
        hit, pos, normal, index, obj, matrix = bpy.context.scene.ray_cast(dg, origin, direction)
        if not hit:
            break
        evaluated = obj.evaluated_get(dg)
        poly = evaluated.data.polygons[index]
        material = obj.material_slots[poly.material_index].material
        source = bpy.data.materials.get(material.get('mmd_lora_source', '')) or material
        image = addon.diffuse_image(source)
        rgba = None
        if image and len(poly.vertices) == 3:
            coords = [matrix @ evaluated.data.vertices[v].co for v in poly.vertices]
            uvs = [Vector((*evaluated.data.uv_layers.active.data[i].uv, 0)) for i in poly.loop_indices]
            uv = barycentric_transform(pos, *coords, *uvs)
            w, h = image.size
            if w and h:
                u, v = min(int((uv.x % 1) * w), w-1), min(int((uv.y % 1) * h), h-1)
                offset = (v*w+u)*4
                rgba = list(image.pixels[offset:offset+4])
        layers.append({'material': source.name, 'rgba': rgba, 'facing': normal.dot(direction), 'alpha_mode': image.alpha_mode if image else None})
        origin = pos + direction * .00002
    rows.append({'pixel': [x, y], 'layers': layers})
session.close('diagnostics')
out = Path(__file__).resolve().parents[1] / 'test_output' / (game + '_surface_layers.json')
out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
print(out)
