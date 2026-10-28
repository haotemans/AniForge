"""Present existing sitting renders and no-render audits; never calls Blender."""
import argparse
import html
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
args = parser.parse_args()
root = args.directory.resolve()
preview = json.loads((root / 'preview_release/physics.json').read_text(encoding='utf-8'))
folder = Path(preview['render_folder'])
manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
images = [folder / shot['image'] for shot in manifest['shots']]
assert len(images) == 3 and all(path.is_file() for path in images)
audits = [json.loads(path.read_text(encoding='utf-8')) for path in sorted((root / 'rig_audit_final').glob('*.json'))]
physics = json.loads((root / 'physics_audit_final/physics.json').read_text(encoding='utf-8'))
summary = {'version': '0.7.0', 'production_total': 36, 'per_group': 9, 'seated_photos': 3,
           'rig_audits': len(audits), 'linked_camera_checks': sum(len(a['storyboard']) for a in audits),
           'physics_states': {name: item['physics'] for name, item in physics['poses'].items()},
           'restored_after_physics': physics.get('state_restored', False), 'development_views': len(images),
           'final_manifest': str(folder / 'manifest.json')}
(root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
cards = ''.join(f'<figure><img src="{path.relative_to(root).as_posix()}"><figcaption>{label}</figcaption></figure>'
                for path, label in zip(images, ('开发抽查：正面', '侧面', '背面')))
rows = ''.join(f'<tr><td>{html.escape(name)}</td><td>{item["physics"]["status"]}</td><td>{item["physics"]["frames"]}</td><td>{item["physics"].get("reused_physics", False)}</td></tr>' for name, item in physics['poses'].items())
document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>AniForge 0.7.0</title>
<style>body{{max-width:1200px;margin:30px auto;padding:0 20px;background:#151923;color:#e5e7eb;font:16px/1.7 system-ui}}a{{color:#93c5fd}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}figure{{margin:0}}img{{width:100%}}td,th{{padding:8px 20px;border-bottom:1px solid #475569}}</style>
<h1>AniForge 0.7.0 · 坐姿、衣服物理、36 张角色信息</h1>
<p>正式输出固定 9 肖像 + 9 半身 + 9 全身 + 9 服装细节；每个位置一张，不逐 Pose 扩展三视图。包含 2 张全身坐姿和 1 张半身坐姿，服装覆盖鞋靴与背部。</p>
<p>以下只复用一次坐姿开发检查的三张图片。头发与披风已垂落；物理采用源模型骨链/关节构建的位置约束代理，独立后台计算后将网格带回主工程。代理不具备高精度布料自碰撞，局部衣服穿模和遮挡仍需检查。</p>
<div class="grid">{cards}</div>
<h2>连续切换与缓存检查（无出图）</h2><table><tr><th>姿势</th><th>收敛</th><th>帧数</th><th>结果复用</th></tr>{rows}</table>
<p>骨架、约束、时间轴、文件路径和对象数恢复检查：{summary['restored_after_physics']}。另有 {len(audits)} 个模型的 {summary['linked_camera_checks']} 个编排镜头边界检查，无额外渲染。</p>
<p><a href="summary.json">测试汇总</a> · <a href="{(folder / 'manifest.json').relative_to(root).as_posix()}">开发图片清单</a></p></html>'''
(root / 'report.html').write_text(document, encoding='utf-8')
print(root / 'report.html')
