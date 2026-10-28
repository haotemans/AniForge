"""Explicit AnimChar capture/generation for selected photo slots; never renders.

Blender --background character.blend --python tools/capture_animchar_plan.py --
  --slots neutral half_body:5 --output D:/local-output --capture
Uses the Key configured in the installed AnimChar add-on preferences.
"""
import argparse
import heapq
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import bpy
    from mathutils import Vector
    import tempfile
    from tools.blender_source import load_addon
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slots', nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--capture', action='store_true', help='Explicitly submit selected jobs to AnimChar using account credits')
    parser.add_argument('--general', action='store_true', help='Use the native general image capture model')
    parser.add_argument('--optimize', action='store_true', help='Enable the native pose optimization option')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    if not args.capture or not bpy.app.background:
        raise ValueError('此工具只在后台运行；实际调用 AnimChar 需显式 --capture。')
    addon = load_addon(); addon.register()
    from mmd_lora_studio import animchar, workflow
    settings = bpy.context.scene.ml_settings
    rigs = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE' and not o.name.startswith('AniForge')]
    if len(rigs) != 1:
        raise ValueError('需要只有一个角色骨架的测试场景。')
    settings.armature = rigs[0]
    native, _ = animchar.modules()
    bpy.context.scene.animchartools_props.use_general_model = args.general
    bpy.context.scene.animchartools_props.use_pose_optimization = args.optimize
    previous_refresh = native.refresh
    native.refresh = lambda: None  # Native UI redraw is unavailable in -b.
    source_path = bpy.data.filepath
    destination_blend = (args.output/'character_animchar.blend').resolve()
    if source_path and destination_blend == Path(source_path).resolve():
        destination_blend = (args.output/'character_animchar_updated.blend').resolve()
    baseline = {b.name: b.matrix_basis.copy() for b in settings.armature.pose.bones}
    queue = []; serial = 0
    original_register = bpy.app.timers.register
    def schedule(callback, first_interval=0, persistent=False):
        nonlocal serial
        serial += 1
        heapq.heappush(queue, (time.monotonic()+first_interval, serial, callback))
    bpy.app.timers.register = schedule
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'renders': 0, 'provider': 'AnimCharTools', 'source_blend': source_path, 'result_blend': str(destination_blend), 'slots': []}
    try:
        plan = addon.recipe()
        for slot in args.slots:
            shot = next((s for s in plan if s.get('asset_slot') == slot), None)
            if not shot:
                raise ValueError('未知 AnimChar 镜头：'+slot)
            started = time.monotonic()
            print('ANIMCHAR_START', slot, flush=True)
            prompt = workflow.PORTRAIT_PROMPTS.get(shot['index'], '') if shot['group'] == 'portrait' and shot['index'] > 4 else ''
            animchar.start_capture(bpy.context, settings, shot, workflow.reference_path(settings, shot), text_prompt=prompt)
            previous = ''
            while animchar.busy():
                while queue and queue[0][0] <= time.monotonic():
                    _, _, callback = heapq.heappop(queue)
                    interval = callback()
                    if interval is not None:
                        schedule(callback, interval)
                message = native.message_to_show['message_pose_capture_image']
                if message != previous:
                    print('ANIMCHAR_PROGRESS', slot, message[:250], flush=True)
                    previous = message
                time.sleep(.1)
            result = {'slot': slot, 'status': settings.animchar_status, 'seconds': round(time.monotonic()-started, 2)}
            report['slots'].append(result)
            library = addon.custom_presets(settings)
            asset = library['shot_assets'].get(animchar.rig_key(settings.armature)+'|'+slot)
            if asset:
                expected = animchar.capture_report(asset['pose']).get('expected_joints', {})
                if expected:
                    old_output = settings.output
                    with tempfile.TemporaryDirectory(prefix='aniforge_pose_audit_') as temp:
                        settings.output = temp
                        selected = next(s for s in addon.dataset_plan(settings) if s.get('asset_slot') == slot)
                        session = addon.RenderSession(bpy.context, [selected]); session.setup(bpy.context)
                        try:
                            session.prepare_shot(bpy.context, selected)
                            errors = {}
                            for name, (head, tail) in expected.items():
                                b = settings.armature.pose.bones[name]
                                errors[name] = max((settings.armature.matrix_world @ b.head-Vector(head)).length,
                                                   (settings.armature.matrix_world @ b.tail-Vector(tail)).length)
                            result['max_joint_replay_error'] = max(errors.values())
                            result['joints_over_1mm'] = {k: v for k, v in errors.items() if v > .001}
                        finally:
                            session.close('pose_replay_audit_no_render')
                    settings.output = old_output
            (args.output/'poses.json').write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding='utf8')
            (args.output/'capture_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
            bpy.ops.wm.save_as_mainfile(filepath=str(destination_blend), copy=True)
            print('ANIMCHAR_FINISHED', json.dumps(result, ensure_ascii=False), flush=True)
        assert bpy.data.filepath == source_path
        assert all(abs(b.matrix_basis[i][j]-baseline[b.name][i][j]) < 1e-5
                   for b in settings.armature.pose.bones for i in range(4) for j in range(4))
        report['source_pose_restored'] = True
        report['bound_slots'] = len(addon.custom_presets(settings)['shot_assets'])
        (args.output/'capture_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    finally:
        bpy.app.timers.register = original_register
        native.refresh = previous_refresh
        from AnimCharTools.addons.AnimCharTools.bridges import stop_server
        from AnimCharTools.addons.AnimCharTools.network_tools import stop_region_speedtest_timer
        stop_server(); stop_region_speedtest_timer()


if __name__ == '__main__':
    main()
