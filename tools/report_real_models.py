"""Create local visual QA sheets and a linked HTML report from real-model tests."""
import argparse
import html
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
args = parser.parse_args()
root = args.directory.resolve()
font_path = 'C:/Windows/Fonts/msyh.ttc'
font = ImageFont.truetype(font_path, 22)
small = ImageFont.truetype(font_path, 16)
games = [('HSR', '崩坏：星穹铁道 / 龙晶'), ('ZZZ', '绝区零 / 菲欧妮'), ('GENSHIN', '原神 / 納貝里士'),
         ('WUWA', '鸣潮 / 锁暝'), ('ENDFIELD', '终末地 / 提弗洛斯'), ('NEVERNESS', '异环 / 明音凛')]
groups = [('portrait', '肖像'), ('half_body', '半身'), ('full_body', '全身'), ('outfit_detail', '服装细节')]
portraits = Image.new('RGB', (960, 724), '#171d29')
all_groups = Image.new('RGB', (1024, 6 * 304), '#171d29')
compare = Image.new('RGB', (1024, 6 * 304), '#171d29')
rows = []
summary = []


def relative(path):
    return Path(path).relative_to(root).as_posix()


def shot_image(folder, group, index=5):
    return next((Path(folder) / group).glob(f'*_{index:02d}.png'))


for i, (game, label) in enumerate(games):
    preview = json.loads((root / game / 'report_preview.json').read_text(encoding='utf-8'))
    if i == 0:
        addon_version = preview.get('addon_version', '0.2.0')
    full_path = root / game / 'report_full.json'
    full = json.loads(full_path.read_text(encoding='utf-8')) if full_path.exists() else {}
    folder = Path(preview['render_folder'])
    materials = preview['materials']
    image_refs = [image for mat in materials for image in mat['images']]
    missing = [image for image in image_refs if not image['exists']]
    missing_base = [image for image in missing if image['node'] == 'mmd_base_tex']
    base_count = sum(bool(m['detected_base']) for m in materials)
    manifests = []
    if full.get('status') == 'complete':
        manifest_path = Path(full['render_folder']) / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifests = manifest['shots']
    row = {'game': game, 'label': label, 'material_count': len(materials), 'base_textures_detected': base_count,
           'missing_base_textures': missing_base, 'missing_auxiliary_textures': missing,
           'full_batch_status': full.get('status', 'not_run'), 'full_batch_images': len(manifests),
           'expressions_without_mapping': sorted({s['expression'] for s in manifests
                                                  if s['expression'] != 'neutral' and not s['mapped_shape_keys']}),
           'source': preview['source']}
    summary.append(row)
    tile = Image.open(shot_image(folder, 'portrait')).convert('RGB').resize((320, 320))
    x, y = (i % 3) * 320, (i // 3) * 362
    portraits.paste(tile, (x, y))
    ImageDraw.Draw(portraits).text((x + 10, y + 329), label, font=small, fill='white')
    draw = ImageDraw.Draw(all_groups)
    draw.text((12, i * 304 + 6), label, font=font, fill='white')
    cards = []
    for j, (group, name) in enumerate(groups):
        path = shot_image(folder, group)
        tile = Image.open(path).convert('RGB').resize((256, 256))
        all_groups.paste(tile, (j * 256, i * 304 + 40))
        cards.append(f'<figure><a href="{relative(path)}"><img src="{relative(path)}"></a><figcaption>{name}</figcaption></figure>')
    # Same neutral expression and relaxed pose for original-vs-toon comparison.
    original = shot_image(preview['original_render'], 'portrait')
    neutral = shot_image(folder, 'portrait')
    pose = shot_image(folder, 'half_body', 21)
    blink = shot_image(folder, 'portrait', 31)
    ImageDraw.Draw(compare).text((12, i * 304 + 6), label + '：原 MMD / 三渲二 / 招手 / 闭眼', font=small, fill='white')
    for j, path in enumerate([original, neutral, pose, blink]):
        compare.paste(Image.open(path).convert('RGB').resize((256, 256)), (j * 256, i * 304 + 40))
    detail = f'{len(materials)} 个材质；{base_count} 个底色绑定；底色缺失 {len(missing_base)}；完整批次 {len(manifests)}/36 张。'
    if missing:
        detail += f' 原材质有 {len(missing)} 个辅助 Toon 引用缺失，底色完整；该辅助图的原效果尚不能还原。'
    if game == 'ZZZ':
        detail += ' 模型 RM.txt 明确禁止 AI 训练/生成式 AI 数据集；本次只作本机兼容性和画面测试，不能据此当作已获训练许可。'
    if game == 'WUWA':
        detail += ' 头发高光较强，尚未按该模型专用的高光遮罩校准。'
    project = root / game / ('tested_full.blend' if full.get('status') == 'complete' else 'tested_preview.blend')
    link = f'<a href="{relative(project)}">本机测试工程</a>'
    if full.get('render_folder'):
        link += f' · <a href="{relative(Path(full["render_folder"]) / "manifest.json")}">36 张图片清单</a>'
    rows.append(f'<section><h2>{html.escape(label)}</h2><p>{html.escape(detail)}</p><p>{link}</p><div class="grid">{"".join(cards)}</div></section>')

portraits.save(root / 'six_games_portraits.jpg', quality=94)
all_groups.save(root / 'six_games_four_groups.jpg', quality=94)
compare.save(root / 'six_games_comparison.jpg', quality=94)
(root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
css = 'body{background:#111827;color:#e5e7eb;font:16px/1.7 system-ui;margin:32px auto;max-width:1200px;padding:0 20px}a{color:#93c5fd}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}figure{margin:0}img{width:100%;border-radius:8px}section{margin:32px 0;padding:20px;background:#1f2937;border-radius:12px}h1,h2{line-height:1.3}p{color:#cbd5e1}'
body = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>六游戏 MMD 渲染测试</title><style>{css}</style>
<h1>六游戏 MMD 贴图与渲染实测</h1><p>Blender 5.2.2 · MMD Tools 4.5.14 · MMD LoRA Studio {addon_version} · 512 × 512</p>
<p>每款游戏选取一个角色，测试 PMX 导入、底色绑定、透明度、四类构图、表情、姿势及完整 36 张批量流程。仅在输出目录写入测试图片与工程，源 PMX/贴图不作修改。</p>
<p>画面仍使用通用三渲二近似。宽袖、披风、长发与招手等动作尚未进行物理稳定和逐张穿模修复；部分特殊高光保留模型原始贴图效果。图片属于功能测试结果，未作训练集质量验收。已关闭模型刚体模拟以确保静态测试可重复。</p>
<p><a href="six_games_four_groups.jpg">四类构图总览</a> · <a href="six_games_comparison.jpg">原材质 / 三渲二 / 招手 / 闭眼对照</a> · <a href="summary.json">机器可读汇总</a></p>
{''.join(rows)}</html>'''
(root / 'report.html').write_text(body, encoding='utf-8')
print(root / 'report.html')
print(json.dumps(summary, ensure_ascii=False, indent=2))
