"""Headless material diagnostics; no renders and no saved asset changes."""
import json
import sys
import bpy

rows = []
for mat in bpy.data.materials:
    if mat.get('mmd_lora_material') or not mat.node_tree:
        continue
    shader = mat.node_tree.nodes.get('mmd_shader')
    if not shader:
        continue
    rows.append({'name': mat.name, 'display': list(mat.diffuse_color),
                 'shader': {s.name: (list(s.default_value) if hasattr(s.default_value, '__len__') else s.default_value)
                            for s in shader.inputs if hasattr(s, 'default_value')},
                 'images': [{'node': n.name, 'name': n.image.name, 'path': bpy.path.abspath(n.image.filepath),
                             'depth': n.image.depth, 'alpha': n.image.alpha_mode, 'channels': n.image.channels}
                            for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image]})
path = sys.argv[sys.argv.index('--') + 1]
with open(path, 'w', encoding='utf-8') as file:
    json.dump(rows, file, ensure_ascii=False, indent=2)
print('MATERIAL_DIAGNOSTICS_SAVED', path)
