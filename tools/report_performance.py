"""Report numeric rig audits and arrange the existing single-model three views."""
import argparse
import html
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
args = parser.parse_args()
root = args.directory.resolve()
audits = [json.loads(path.read_text(encoding='utf-8')) for path in sorted((root / 'audit').glob('*.json'))]
preview = json.loads((root / 'preview/WUWA/three_views.json').read_text(encoding='utf-8'))
folder = Path(preview['render_folder'])
manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
images = [folder / shot['image'] for shot in manifest['shots']]
assert len(images) == 3
version = '.'.join(map(str, preview['version']))
first = manifest['shots'][0]
description = f'{first.get("title", first["pose"])} / {first["expression"]} / {first["camera"]["lens"]:g} mm / {first["group"]}'
sheet = Image.new('RGB', (900, 442), '#172033')
font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 20)
cards = []
for col, (label, path) in enumerate(zip(('正面', '侧面', '背面'), images)):
    sheet.paste(Image.open(path).convert('RGB').resize((300, 400)), (col*300, 38))
    ImageDraw.Draw(sheet).text((col*300+12, 6), label, font=font, fill='white')
    relative = path.relative_to(root).as_posix()
    cards.append(f'<figure><a href="{relative}"><img src="{relative}"></a><figcaption>{label}</figcaption></figure>')
sheet.save(root / 'pose_expression_camera.jpg', quality=94)
rows = []
for audit in audits:
    missing = [name for name, value in audit['mapping']['bones'].items() if not value]
    expressions = sum(bool(value) for name, value in audit['expressions'].items() if name != 'neutral')
    rows.append(f'<tr><td>{audit["game"]}</td><td>{len(audit["poses"])}</td><td>{expressions}/7</td><td>{audit["camera_fits"]} + {len(audit.get("storyboard", []))}</td><td>{html.escape(", ".join(missing) or "无")}</td></tr>')
summary = {'version': version, 'rendered_model': 'WUWA', 'final_views': 3, 'pose': first['pose'],
           'expression': first['expression'], 'projection': first['camera']['projection'], 'aspect': '3:4', 'resolution': first['camera']['resolution'],
           'audited_models': len(audits), 'real_camera_fits': sum(a['camera_fits'] for a in audits),
           'linked_shot_fits': sum(len(a.get('storyboard', [])) for a in audits),
           'synthetic_camera_fits': 72, 'vpd_static_import': any(a.get('vpd_static_import') for a in audits)}
(root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
css = 'body{max-width:1300px;margin:32px auto;padding:0 20px;background:#111827;color:#e5e7eb;font:16px/1.7 system-ui}a{color:#93c5fd}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}img{width:100%}figure{margin:0}td,th{padding:8px 16px;border-bottom:1px solid #334155}table{border-collapse:collapse}p{color:#cbd5e1}'
document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>AniForge {version} 姿势表情镜头</title><style>{css}</style>
<h1>AniForge {version} · 姿势、表情与镜头</h1>
<p>本页展示锁暝最终一次三视图：{html.escape(description)}，{first['camera']['resolution'][0]} × {first['camera']['resolution'][1]}。排版复用这三张图。</p>
<div class="grid">{''.join(cards)}</div>
<p>六个模型的骨骼/表情/相机检查均不渲染。每模型另验证 36 个联动编排镜头，共 {summary['linked_shot_fits']} 个取景。另有 72 组合成相机边界测试、预设 JSON 往返、强度与重复应用、取消后的状态恢复检查。VPD 使用测试夹具验证坐标转换与静态导入，不创建 Action。没有运行 36 张完整出图。</p>
<table><tr><th>游戏</th><th>姿势模板</th><th>非中性表情</th><th>镜头组合</th><th>缺失常用骨骼</th></tr>{''.join(rows)}</table>
<p>头肩/脸部特写、半身、全身、服装细节均按当前模型重新构图；侧面和背面不会看见正面表情。手型模板、复杂约束、宽袖与披风仍需人工检查，未进行碰撞修复或物理稳定。</p>
<p><a href="summary.json">测试汇总</a> · <a href="preview/WUWA/optimized.blend">本机测试工程</a> · <a href="{(folder / 'manifest.json').relative_to(root).as_posix()}">镜头与映射参数清单</a></p></html>'''
(root / 'report.html').write_text(document, encoding='utf-8')
print(root / 'report.html')
