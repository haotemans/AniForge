"""Build a legacy Blender add-on ZIP, excluding caches and test artifacts."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import ast

root = Path(__file__).resolve().parents[1]
module = ast.parse((root / 'mmd_lora_studio' / '__init__.py').read_text(encoding='utf-8'))
info = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'bl_info' for target in node.targets))
version = '.'.join(map(str, info['version']))
output = root / 'dist' / f'mmd_lora_studio-{version}.zip'
output.parent.mkdir(exist_ok=True)
with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
    for file in sorted((root / 'mmd_lora_studio').glob('*.py')):
        archive.write(file, file.relative_to(root))
    archive.write(root / 'README.md', 'mmd_lora_studio/README.md')
    for file in sorted((root / 'docs').rglob('*')):
        if file.is_file() and file.suffix.lower() in {'.md'}:
            archive.write(file, 'mmd_lora_studio/' + file.relative_to(root).as_posix())
print(output)
