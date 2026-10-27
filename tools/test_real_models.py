"""Blender runner for local PMX import/material/render tests; never edits source PMX files."""
import argparse
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy
import addon_utils
import mmd_lora_studio as addon
from mmd_lora_studio.core import write_json, recipe, three_view_recipe

MODELS = {
    'HSR': ('崩坏星穹铁道/龙晶石心十人公司制服/龙晶石心十人公司制服.pmx', 'longjing'),
    'ZZZ': ('绝区零/菲欧妮/菲欧妮.pmx', 'feioni'),
    'GENSHIN': ('原神/生之执政-納貝里士/納貝里士(T-Pose).pmx', 'naberius'),
    'WUWA': ('鸣潮/鸣潮_锁暝/鸣潮_锁暝1.0.pmx', 'suoming'),
    'ENDFIELD': ('终末地/【明日方舟：终末地】提弗洛斯/提弗洛斯.pmx', 'tiflos'),
    'NEVERNESS': ('异环/【异环】明音凛/明音fasion2.pmx', 'mingyin'),
}


def run(args):
    output = Path(args.output) / args.game
    output.mkdir(parents=True, exist_ok=True)
    model_rel, trigger = MODELS[args.game]
    model = Path(args.models) / model_rel
    report = {'game': args.game, 'source': str(model), 'phase': args.phase, 'status': 'running',
              'addon_version': '.'.join(map(str, addon.bl_info['version']))}
    write_json(output / ('report_' + args.phase + '.json'), report)
    addon_utils.enable('bl_ext.user_default.mmd_tools', default_set=False)
    addon.register()
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
    result = bpy.ops.mmd_tools.import_model(filepath=str(model), types={'MESH', 'ARMATURE', 'MORPHS'},
                                           scale=.08, rename_bones=True, save_log=False, log_level='WARNING')
    if result != {'FINISHED'}:
        raise RuntimeError('PMX import failed')
    settings = bpy.context.scene.ml_settings
    settings.armature = next(o for o in bpy.context.scene.objects if o.type == 'ARMATURE')
    settings.style = args.game
    settings.trigger = trigger
    settings.resolution = args.resolution
    settings.output = str(output / args.phase)
    settings.textures = str(model.parent)
    meshes = addon.model_meshes(settings)
    bpy.ops.ml.textures()
    materials = {slot.material for obj in meshes for slot in obj.material_slots if slot.material}
    report['meshes'] = [o.name for o in meshes]
    report['vertices'] = sum(len(o.data.vertices) for o in meshes)
    report['shape_keys'] = {o.name: [k.name for k in keys] for o, keys in addon.shape_blocks(meshes)}
    report['bones'] = {key: (addon.find_bone(settings.armature, key).name if addon.find_bone(settings.armature, key) else None)
                       for key in addon.BONES}
    report['arm_bones'] = {key: {'head': list(b.head), 'tail': list(b.tail), 'matrix': [list(row) for row in b.bone.matrix_local]}
                           for key in ('left_arm', 'right_arm', 'left_elbow', 'right_elbow')
                           if (b := addon.find_bone(settings.armature, key)) is not None}
    report['materials'] = []
    for material in sorted(materials, key=lambda m: m.name):
        nodes = material.node_tree.nodes if material.node_tree else []
        image = addon.diffuse_image(material)
        mmd = material.mmd_material
        report['materials'].append({'name': material.name, 'alpha': mmd.alpha,
                                    'double_sided': mmd.is_double_sided, 'diffuse': list(mmd.diffuse_color),
                                    'detected_base': image.name if image else None,
                                    'images': [{'node': n.name, 'image': n.image.name, 'path': bpy.path.abspath(n.image.filepath),
                                                'exists': bool(n.image.packed_file) or Path(bpy.path.abspath(n.image.filepath)).is_file(),
                                                'size': list(n.image.size), 'alpha_mode': n.image.alpha_mode}
                                               for n in nodes if n.type == 'TEX_IMAGE' and n.image]})
    lo, hi = addon.bounds(meshes, bpy.context.evaluated_depsgraph_get())
    report['bounds'] = [list(lo), list(hi)]
    write_json(output / ('report_' + args.phase + '.json'), report)
    if args.phase != 'audit':
        if args.phase == 'preview':
            session = addon.RenderSession(bpy.context)
            try:
                session.setup(bpy.context)
                shot = dict(group='portrait', index=5, azimuth=0, expression='neutral', pose='relaxed')
                session.render(bpy.context, shot)
                report['original_render'] = str(session.folder)
            finally:
                session.close('original_preview')
        bpy.ops.ml.materials()
        session = addon.RenderSession(bpy.context)
        try:
            session.setup(bpy.context)
            report['render_folder'] = str(session.folder)
            shots = (recipe() if args.phase == 'full' else three_view_recipe(settings.preview_group)
                     if args.phase == 'three_views' else [dict(s, expression='neutral', pose='relaxed') for s in recipe() if s['index'] == 5])
            session.manifest['recipe'] = shots
            if args.phase == 'preview':
                shots += [dict(group='half_body', index=20+i, azimuth=0, expression='neutral', pose=pose)
                          for i, pose in enumerate(('hand_hip', 'wave', 'open_arms'))]
                shots += [dict(group='portrait', index=30+i, azimuth=0, expression=expression, pose='relaxed')
                          for i, expression in enumerate(('smile', 'blink', 'surprised'))]
            for shot in shots:
                session.render(bpy.context, shot)
        except Exception:
            session.close('failed', traceback.format_exc())
            raise
        else:
            session.close('complete' if args.phase == 'full' else 'preview')
        # Save an inspectable local project containing the tested character and its original materials.
        bpy.ops.wm.save_as_mainfile(filepath=str(output / ('tested_' + args.phase + '.blend')))
    report['status'] = 'complete'
    write_json(output / ('report_' + args.phase + '.json'), report)
    print('REAL_MODEL_TEST_OK', args.game, args.phase, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--game', choices=MODELS, required=True)
    parser.add_argument('--models', default='D:/mmd')
    parser.add_argument('--output', required=True)
    parser.add_argument('--phase', choices=('audit', 'three_views', 'preview', 'full'), default='three_views')
    parser.add_argument('--resolution', type=int, default=512)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    try:
        run(args)
    except Exception:
        error = traceback.format_exc()
        target = Path(args.output) / args.game / ('error_' + args.phase + '.txt')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(error, encoding='utf-8')
        raise
