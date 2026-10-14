"""Export exact atlas regions from the installed game; no replacement artwork."""
import hashlib
import json
import re
from pathlib import Path

from PIL import Image
from autoslay.paths import ROOT
from autoslay.knowledge_base.game_pack import GamePack
from autoslay.knowledge_base.textures import read_texture


def export(game):
    pack = GamePack(game / 'SlayTheSpire2.pck')
    output = ROOT / 'data/knowledge/assets/map'
    output.mkdir(parents=True, exist_ok=True)
    images = {}
    for name in pack.entries:
        if name.startswith('images/map/placeholder/') and name.endswith('.png.import'):
            image, metadata = read_texture(pack, name.removesuffix('.import'))
            identity = 'MAP.' + Path(name.removesuffix('.import')).stem.upper()
            image.save(output/(identity+'.webp'), format='WEBP', lossless=True)
            images[identity] = dict(metadata, resource=name, file=identity+'.webp', size=list(image.size))
            continue
        if not name.startswith('images/atlases/ui_atlas.sprites/map/') or not name.endswith('.tres'):
            continue
        if '/icons/' not in name and '/ancients/' not in name and '/placeholder/' not in name:
            continue
        text = pack.read(name).decode('utf-8')
        atlas = re.search(r'path="res://([^"]+\.png)"', text)
        region = re.search(r'region = Rect2\(([^)]+)\)', text)
        if not atlas or not region:
            continue
        image, metadata = read_texture(pack, atlas[1])
        x, y, w, h = [int(float(v)) for v in region[1].split(',')]
        image = image.crop((x, y, x+w, y+h))
        margin = re.search(r'margin = Rect2\(([^)]+)\)', text)
        if margin:
            mx, my, mw, mh = [int(float(v)) for v in margin[1].split(',')]
            canvas = Image.new('RGBA', (w+mw, h+mh))
            canvas.paste(image, (mx, my))
            image = canvas
        identity = 'MAP.' + Path(name).stem.upper()
        image.save(output / (identity+'.webp'), format='WEBP', lossless=True)
        images[identity] = dict(metadata, resource=name, resource_md5=pack.entries[name][2].hex(),
                               file=identity+'.webp', size=list(image.size))
    manifest = dict(game_assembly_sha256=hashlib.sha256((game/'data_sts2_windows_x86_64/sts2.dll').read_bytes()).hexdigest(),
                    source='installed_game_pack', images=images)
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Exported {len(images)} native map textures')


if __name__ == '__main__':
    installation = json.loads((ROOT/'.runtime/local/installation.json').read_text(encoding='utf-8-sig'))
    export(Path(installation['game_path']))
