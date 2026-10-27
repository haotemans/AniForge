"""Build A/B contact sheets and a linked local report for the four shader fixes."""
import argparse
import html
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
args = parser.parse_args()
root = args.directory.resolve()
games = [('WUWA', '鸣潮 · 锁暝', '恢复高光 PNG 的透明通道，保留红色发丝，降低覆盖层强度。'),
         ('ZZZ', '绝区零 · 菲欧妮', '降低覆盖在底色上的高光层，恢复原贴图的浅金发色。'),
         ('ENDFIELD', '终末地 · 提弗洛斯', '脸部使用低对比柔和明暗，额外发影与目影强度减至原值的 0.3。'),
         ('GENSHIN', '原神 · 納貝里士', '减轻帽檐和发丝造成的硬阴影，保留肤色与五官纹理。')]
font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 24)
small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 18)
before_after = Image.new('RGB', (960, 4 * 526), '#172033')
after_grid = Image.new('RGB', (960, 2 * 526), '#172033')
sections = []; summary = []


def shot(folder, group, index):
    return next((Path(folder) / group).glob(f'*_{index:02d}.png'))


def rel(path):
    return Path(path).relative_to(root).as_posix()


for row, (game, title, detail) in enumerate(games):
    data = json.loads((root / game / 'comparison.json').read_text(encoding='utf-8'))
    old = shot(data['before'], 'portrait', 2)
    new = shot(data['after'], 'portrait', 2)
    old_im = Image.open(old).convert('RGB')
    new_im = Image.open(new).convert('RGB')
    assert old_im.size == new_im.size == (data['resolution'], data['resolution'])
    assert ImageChops.difference(old_im, new_im).getbbox(), game + ' did not change'
    draw = ImageDraw.Draw(before_after)
    draw.text((12, row * 526 + 8), title + '    优化前                                      优化后', font=small, fill='white')
    before_after.paste(old_im.resize((480, 480)), (0, row * 526 + 40))
    before_after.paste(new_im.resize((480, 480)), (480, row * 526 + 40))
    x, y = (row % 2) * 480, (row // 2) * 526
    after_grid.paste(new_im.resize((480, 480)), (x, y))
    ImageDraw.Draw(after_grid).text((x + 12, y + 490), title, font=font, fill='white')
    full_path = root / game / 'report_full.json'
    full = json.loads(full_path.read_text(encoding='utf-8')) if full_path.exists() else {}
    manifest_path = Path(full['render_folder']) / 'manifest.json' if full.get('render_folder') else None
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path and manifest_path.exists() else {}
    full_count = len(manifest.get('shots', []))
    summary.append({'game': game, 'title': title, 'status': data['status'],
                    'resolution': data['resolution'], 'comparison_images': len(list((root / game / 'before').rglob('*.png'))) + len(list((root / game / 'after').rglob('*.png'))),
                    'full_images': full_count, 'full_status': manifest.get('status'),
                    'alpha_recovered': data['alpha_recovered'], 'change': detail})
    figures = []
    for label, path in [('优化前', old), ('优化后', new), ('左侧 40°', shot(data['after'], 'portrait', 1)),
                        ('右侧 40°', shot(data['after'], 'portrait', 3)), ('闭眼', shot(data['after'], 'portrait', 4)),
                        ('微笑', shot(data['after'], 'portrait', 5)), ('全身', shot(data['after'], 'full_body', 6))]:
        figures.append(f'<figure><a href="{rel(path)}"><img loading="lazy" src="{rel(path)}"></a><figcaption>{label}</figcaption></figure>')
    links = f'<a href="{game}/optimized.blend">优化后的本机工程</a>'
    if manifest_path:
        links += f' · <a href="{rel(manifest_path)}">完整 36 张清单（{full_count}/36）</a>'
    note = '该模型 RM.txt 明确禁止 AI 训练或生成式 AI 数据集，本页仅为本机材质与渲染测试。' if game == 'ZZZ' else ''
    sections.append(f'<section><h2>{title}</h2><p>{html.escape(detail)}</p><p>{links}</p><p>{note}</p><div class="grid">{"".join(figures)}</div></section>')

before_after.save(root / 'before_after.jpg', quality=95)
after_grid.save(root / 'optimized_four_models.jpg', quality=95)
(root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
style = 'body{background:#101827;color:#e5e7eb;font:16px/1.7 system-ui;max-width:1300px;margin:36px auto;padding:0 20px}section{background:#1e293b;padding:24px;border-radius:14px;margin:24px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}img{width:100%;border-radius:8px}figure{margin:0}a{color:#93c5fd}p{color:#cbd5e1}'
document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>头发与脸部材质优化</title><style>{style}</style>
<h1>头发与脸部材质优化 · 0.3.0</h1><p>Blender 5.2.2 · 1024 × 1024 · 四个角色 · 同一相机、光照、构图与姿势的前后对照。</p>
<p>每个角色对比三个角度、闭眼、微笑与全身，共 48 张对照图；另运行每角色 36 张完整批次。原 PMX 和图片文件不修改，Alpha 修复使用复制的 Blender 图像数据块。</p>
<p>参数：头发高光层强度 0.22，脸部柔和补光 0.92，脸部阴影层强度 0.3。脸部为没有专用 SDF 遮罩时的柔和明暗近似，未实现游戏原版脸部 SDF。袖子、披风和刚体物理不在本次材质修复范围内。</p>
<p><a href="before_after.jpg">前后对照总览</a> · <a href="optimized_four_models.jpg">四角色优化结果</a> · <a href="summary.json">测试汇总</a></p>
{''.join(sections)}</html>'''
(root / 'report.html').write_text(document, encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
print(root / 'report.html')
