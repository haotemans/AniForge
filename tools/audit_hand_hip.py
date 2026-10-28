"""Check both hand-on-hip mirrors on one loaded local model, without images."""
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import intersect_ray_tri
from bpy_extras.object_utils import world_to_camera_view
from tools.blender_source import load_addon
addon=load_addon()
from mmd_lora_studio.performance import finger_bones, bone_side
from mmd_lora_studio.core import write_json
from mmd_lora_studio.hand_pose import fit_report
p=argparse.ArgumentParser(); p.add_argument('--game',required=True); p.add_argument('--output',type=Path,required=True)
args=p.parse_args(sys.argv[sys.argv.index('--')+1:]); args.output.mkdir(parents=True,exist_ok=True)
addon.register(); scene=bpy.context.scene; s=scene.ml_settings
rig=s.armature=next(o for o in scene.objects if o.type=='ARMATURE'); meshes=addon.model_meshes(s)
before={b.name:b.matrix_basis.copy() for b in rig.pose.bones}; frame=scene.frame_current
s.output=str(args.output/'sessions'); s.resolution=1024
current=next(t for t in addon.dataset_plan(s) if t['group']=='half_body' and t['index']==5)
report={'game':args.game,'renders':0,'current_halfbody05':current['pose'],'checks':[]}
fingers=list(finger_bones(rig))
trunk={b.name for k in ('upper_body','upper_body2','lower_body') if (b:=addon.find_bone(rig,k,s))}
blocked={b.name for k in ('head','left_arm','right_arm','left_shoulder','right_shoulder') if (b:=addon.find_bone(rig,k,s))}
body_names={b.name for b in rig.data.bones if ({b.name}|{p.name for p in b.parent_recursive})&trunk and not ({b.name}|{p.name for p in b.parent_recursive})&blocked}
hand_names={side:{b.name for b,_,_ in fingers if bone_side(b)==side}|{addon.find_bone(rig,side+'_wrist',s).name} for side in ('left','right')}

def geometry():
    points=[]; body=[]; hands={'left':[],'right':[]}
    depsgraph=bpy.context.evaluated_depsgraph_get()
    for obj in meshes:
        ev=obj.evaluated_get(depsgraph)
        if obj.hide_render or len(ev.data.vertices)!=len(obj.data.vertices): continue
        groups={g.index for g in obj.vertex_groups if g.name in body_names}
        hv={side:{g.index for g in obj.vertex_groups if g.name in names} for side,names in hand_names.items()}
        hi={side:{v.index for v in obj.data.vertices if sum(g.weight for g in v.groups if g.group in names)>=.55} for side,names in hv.items()}
        bi={v.index for v in obj.data.vertices if sum(g.weight for g in v.groups if g.group in groups)>=.35}-(hi['left']|hi['right'])
        offset=len(points); points += [ev.matrix_world@v.co for v in ev.data.vertices]
        ev.data.calc_loop_triangles()
        for triangle in ev.data.loop_triangles:
            ids=set(triangle.vertices); tri=tuple(i+offset for i in triangle.vertices)
            if ids<=bi: body.append(tri)
            for side,indices in hi.items():
                if ids<=indices: hands[side].append(tri)
    return points,body,hands

def crossing(a,b):
    for src,dst in ((a,b),(b,a)):
        for i in range(3):
            start,end=src[i],src[(i+1)%3]; delta=end-start
            if delta.length<1e-7: continue
            hit=intersect_ray_tri(dst[0],dst[1],dst[2],delta.normalized(),start,True)
            if hit is not None and 1e-6 < (hit-start).dot(delta.normalized()) < delta.length-1e-6: return True
    return False

shots=[]
for i,(hand,angle) in enumerate((('LEFT',30),('RIGHT',-30)),1):
    shots.append(dict(group='half_body',index=i,pose='hand_hip',expression='smile',azimuth=angle,elevation=0,support='floor',focus='UPPER',include_hands=['left_wrist','right_wrist'],
        performance={'head_angles':[0,-12 if angle<0 else 12,0],'pose_strength':1,'expression_strength':.4,'hand_pose':'AUTO','hip_hand':hand},
        camera={'camera_projection':'PERSP','camera_lens':75,'camera_aspect':'PORTRAIT','camera_azimuth':0,'camera_roll':0,'camera_shift_x':0,'camera_shift_y':0,'camera_target_offset':[0,0,0],'camera_zoom':1,'margin':1.12}))
session=addon.RenderSession(bpy.context,shots); session.setup(bpy.context)
try:
    for shot in shots:
        _,shapes,mapped=session.prepare_shot(bpy.context,shot)
        points,body,hands=geometry(); tree=BVHTree.FromPolygons(points,body,all_triangles=True)
        result={'azimuth':shot['azimuth'],'hip_hand':shot['performance']['hip_hand'],'hand_fit':fit_report(rig),'mapped_expression_keys':shapes,'mapped_pose_bones':len(mapped),'hands':{},'projected_outside':[]}
        for side,triangles in hands.items():
            ht=BVHTree.FromPolygons(points,triangles,all_triangles=True)
            candidates=tree.overlap(ht)
            count=sum(crossing([points[v] for v in body[a]],[points[v] for v in triangles[b]]) for a,b in candidates)
            wrist=addon.find_bone(rig,side+'_wrist',s); w=rig.matrix_world@wrist.head
            reach=max(p.x for p in points)-min(p.x for p in points)+1
            left=tree.ray_cast(w-Vector((reach,0,0)),Vector((1,0,0)),reach*2)[0]
            right=tree.ray_cast(w+Vector((reach,0,0)),Vector((-1,0,0)),reach*2)[0]
            inside=bool(left is not None and right is not None and left.x+1e-4<w.x<right.x-1e-4)
            result['hands'][side]={'surface_crossings':count,'bvh_candidates':len(candidates),'wrist':list(w),'wrist_inside_torso_x_envelope':inside,'triangles':len(triangles)}
        key_bones=[(k,addon.find_bone(rig,k,s)) for k in ('head','left_arm','right_arm','left_elbow','right_elbow','left_wrist','right_wrist')]
        key_bones += [(b.name,b) for b,_,_ in fingers]
        for name,bone in key_bones:
            for endpoint in (bone.head,bone.tail):
                v=world_to_camera_view(scene,session.camera,rig.matrix_world@endpoint)
                if not (0<=v.x<=1 and 0<=v.y<=1 and v.z>0): result['projected_outside'].append(name); break
        report['checks'].append(result)
finally: session.close('pose_compatibility_diagnostics_no_render')
assert frame==scene.frame_current
assert all(abs(b.matrix_basis[i][j]-before[b.name][i][j])<1e-4 for b in rig.pose.bones for i in range(4) for j in range(4))
report['status']='complete'; report['source_state_restored']=True
write_json(args.output/(args.game+'.json'),report)
print('HALFBODY05_CHECK',args.game,[(c['azimuth'],{side:h['surface_crossings'] for side,h in c['hands'].items()},c['projected_outside']) for c in report['checks']],flush=True)
