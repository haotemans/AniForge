"""Link existing model-test images and manifests without rendering new sheets."""
import argparse
import html
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
args = parser.parse_args()
root = args.directory.resolve()
cards = []
for path in sorted(root.rglob('manifest.json')):
    data = json.loads(path.read_text(encoding='utf-8'))
    external = path.parent.parent.name == '.aniforge' and data.get('dataset_folder')
    dataset = path.parent.parent.parent/data['dataset_folder'] if external else path.parent
    entries = []
    for shot in data.get('shots', []):
        image = dataset/shot['image']
        url = html.escape(image.relative_to(root).as_posix(), quote=True)
        label = html.escape(shot.get('title', shot['group'])+' / '+shot.get('view', ''))
        entries.append(f'<figure><a href="{url}"><img src="{url}" loading="lazy"></a><figcaption>{label}</figcaption></figure>')
    name = html.escape(dataset.relative_to(root).as_posix())
    cards.append(f'<section><h2>{name}</h2><p>{html.escape(data.get("status", ""))} · {len(entries)} images</p><div>{"".join(entries)}</div></section>')
document = '<!doctype html><meta charset="utf-8"><title>AniForge render report</title><style>body{font:16px system-ui;margin:24px;background:#172033;color:white}section div{display:flex;flex-wrap:wrap}figure{width:240px;margin:12px}img{width:100%}a{color:#93c5fd}</style><h1>AniForge · A Pose 四视图</h1>'+''.join(cards)
output = root/'report.html'
output.write_text(document, encoding='utf-8')
print(output)
