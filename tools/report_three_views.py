"""Arrange the existing three-view images; does not trigger any renders."""
import argparse
import html
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
args = parser.parse_args()
root = args.directory.resolve()
reports = [(game, json.loads((root / game / 'three_views.json').read_text(encoding='utf-8')))
           for game in ('WUWA', 'ZZZ')]
sheet = Image.new('RGB', (1200, 2 * 446), '#172033')
fronts = Image.new('RGB', (960, 526), '#172033')
font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 22)
sections = []
for row, (game, data) in enumerate(reports):
    title = '鸣潮 · 锁暝：眼睛' if game == 'WUWA' else '绝区零 · 菲欧妮：颈圈'
    folder = Path(data['render_folder'])
    assert data['shots'] == 3
    images = sorted(folder.glob('*_portrait_*.png')) or sorted((folder / 'portrait').glob('*.png'))
    assert len(images) == 3
    ImageDraw.Draw(sheet).text((12, row * 446 + 8), title, font=font, fill='white')
    cards = []
    for col, (label, file) in enumerate(zip(('正面', '侧面', '背面'), images)):
        im = Image.open(file).convert('RGB')
        sheet.paste(im.resize((400, 400)), (col * 400, row * 446 + 40))
        if col == 0:
            fronts.paste(im.resize((480, 480)), (row * 480, 0))
            ImageDraw.Draw(fronts).text((row * 480 + 12, 490), title, font=font, fill='white')
        relative = file.relative_to(root).as_posix()
        cards.append(f'<figure><a href="{relative}"><img src="{relative}"></a><figcaption>{label}</figcaption></figure>')
    sections.append(f'<section><h2>{title}</h2><div class="grid">{"".join(cards)}</div><p><a href="{game}/optimized.blend">本机工程</a></p></section>')
sheet.save(root / 'three_views.jpg', quality=94)
fronts.save(root / 'fixed_eyes_collar.jpg', quality=94)
css = 'body{background:#111827;color:#e5e7eb;font:16px/1.7 system-ui;max-width:1400px;margin:32px auto;padding:0 20px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}figure{margin:0}img{width:100%}a{color:#93c5fd}section{margin:24px 0}'
document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>AniForge 0.4.0 三视图测试</title><style>{css}</style>
<h1>AniForge 0.4.0 · 眼睛与颈圈修复</h1><p>本页只汇总每个角色最终一次的正面、侧面、背面三张，1024 × 1024。图片排版复用已有渲染，不额外生成场景渲染。</p>
<p>锁暝：眼部改用独立颜色通路与 BLENDED 透明方式，显示原始红色虹膜与瞳孔纹理。菲欧妮：皮肤贴花使用独立颜色/Alpha 通路，显示原始白色领口细节。</p>
<p>菲欧妮的模型说明禁止 AI 训练，本页为本机材质测试。源模型、原始贴图与这些渲染图片均不上传到代码仓库。</p>
{''.join(sections)}</html>'''
(root / 'report.html').write_text(document, encoding='utf-8')
print(root / 'report.html')
