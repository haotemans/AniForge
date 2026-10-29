"""Audit saved local models in memory, without rendering or saving the sources."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
from tools.blender_source import load_addon

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True, help='GAME/tested_full.blend files')
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--games', nargs='+', required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
addon = load_addon(); addon.register()
report = {'renders': 0, 'models': {}}
for game in args.games:
    source = args.source / game / 'tested_full.blend'
    bpy.ops.wm.open_mainfile(filepath=str(source.resolve()))
    settings = bpy.context.scene.ml_settings
    settings.armature = next(o for o in bpy.context.scene.objects if o.type == 'ARMATURE')
    meshes = addon.model_meshes(settings)
    settings.style = game; settings.front = 0
    settings.camera_azimuth = settings.camera_elevation = settings.camera_roll = 0
    settings.camera_target_offset = (0, 0, 0)
    settings.camera_shift_x = settings.camera_shift_y = 0
    settings.camera_zoom = 1
    before = [(b, b.matrix_basis.copy()) for b in settings.armature.pose.bones]
    rest = [(b, b.matrix_local.copy()) for b in settings.armature.data.bones]
    with tempfile.TemporaryDirectory() as folder:
        settings.output = folder
        session = addon.RenderSession(bpy.context, addon.recipe())
        try:
            session.setup(bpy.context)
            fits = []
            for shot in addon.recipe():
                session.prepare_shot(bpy.context, shot)
                for point in session.camera_meta['roi']:
                    p = world_to_camera_view(bpy.context.scene, session.camera, Vector(point))
                    assert -.001 <= p.x <= 1.001 and -.001 <= p.y <= 1.001 and p.z > 0, (game, shot, list(p))
                fits.append({'group': shot['group'], 'view': shot['view'], 'target': session.camera_meta['target']})
            arms = {}
            for side, sign in [('left', 1), ('right', -1)]:
                for part in ('arm', 'elbow', 'wrist'):
                    bone = addon.find_bone(settings.armature, side+'_'+part)
                    assert bone is not None, (game, side, part, [b.name for b in settings.armature.pose.bones][:90])
                    direction = (settings.armature.matrix_world.to_3x3() @ (bone.tail-bone.head)).normalized()
                    assert direction.dot(Vector((sign, 0, -1)).normalized()) > .999, (game, bone.name, list(direction))
                    arms[bone.name] = list(direction)
            report['models'][game] = {'source': str(source), 'fits': fits, 'arm_directions': arms, 'status': 'PASS'}
        finally:
            session.close('checked_without_rendering')
        assert not list(Path(folder).rglob('*.png'))
    assert all(max(abs(b.matrix_basis[i][j]-m[i][j]) for i in range(4) for j in range(4)) < 1e-5 for b, m in before)
    assert all(b.matrix_local == m for b, m in rest)
    print('APOSE_MODEL_OK', game, '16 camera fits; source unchanged; zero renders', flush=True)
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
addon.unregister()
print('APOSE_MODELS_OK', len(report['models']), flush=True)
