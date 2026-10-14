"""Extract owned local game portraits to web assets; never reads a remote wiki."""
import argparse,hashlib,io,json,re,struct
from pathlib import Path
from PIL import Image
from autoslay.paths import ROOT
from autoslay.knowledge_base.repository import read_json
from autoslay.knowledge_base.catalog import CATALOG_PATH

def extract(pck:Path,assembly:Path):
    catalog=read_json(CATALOG_PATH, fresh=True)
    assembly_hash=hashlib.sha256(assembly.read_bytes()).hexdigest()
    if assembly_hash!=catalog.get('game_assembly_sha256'):raise ValueError('Refresh encyclopedia for this game build first')
    asset_root=ROOT/'data/knowledge/assets'
    with pck.open('rb') as f:
        if f.read(4)!=b'GDPC':raise ValueError('Unsupported game pack')
        version,major,minor,patch,flags=struct.unpack('<5I',f.read(20))
        base,index=struct.unpack('<QQ',f.read(16))
        if version!=3 or flags&1:raise ValueError('Unsupported or encrypted pack')
        f.seek(index);count=struct.unpack('<I',f.read(4))[0];entries={}
        if count>100000:raise ValueError('Invalid pack directory')
        for _ in range(count):
            length=struct.unpack('<I',f.read(4))[0]
            if length>2000:raise ValueError('Invalid resource path')
            name=f.read(length).rstrip(b'\0').decode('utf-8');offset,size=struct.unpack('<QQ',f.read(16));digest=f.read(16);entry_flags=struct.unpack('<I',f.read(4))[0]
            entries[name]=(base+offset,size,digest,entry_flags)
        def read(name):
            offset,size,digest,entry_flags=entries[name]
            if entry_flags or size>30_000_000:raise ValueError('Unsupported resource')
            f.seek(offset);data=f.read(size)
            if len(data)!=size or hashlib.md5(data).digest()!=digest:raise ValueError('Resource checksum mismatch')
            return data
        images={};missing=[]
        for entry in catalog['entries']:
            category=entry['category']
            folder={'card':'cards','potion':'potions','relic':'relics'}[category]
            output=asset_root/folder;output.mkdir(parents=True,exist_ok=True)
            identity=entry['entity_id'];stem=identity.split('.')[-1].lower()
            pool=(entry.get('pool') or '').split('.')[-1].lower().removesuffix('_card_pool')
            # Import remaps are actual shipped resources; no generated substitute images.
            name={'card':f'images/packed/card_portraits/{pool}/{stem}.png.import',
                  'potion':f'images/potions/large/{stem}.png.import',
                  'relic':f'images/relics/{stem}.png.import'}[category]
            illustration='standard'
            if identity=='RELIC.YUMMY_COOKIE':
                # The game's canonical cookie chooses a character skin; use Ironclad in the neutral library.
                name='images/relics/yummy_cookie_ironclad.png.import'
            if name not in entries:
                beta=f'images/packed/card_portraits/{pool}/beta/{stem}.png.import' if category=='card' else f'images/relics/beta/{stem}.png.import'
                if beta in entries:name=beta;illustration='beta'
            try:
                text=read(name).decode('utf-8');resource=re.search(r'path(?:\.(?:bptc|s3tc))?="res://([^"]+)"',text).group(1);data=read(resource)
                if data[:4]!=b'GST2':raise ValueError('Unsupported texture')
                # Godot 4 encoded image payloads retain a PNG/WebP signature after the header.
                encoded=data[56:]
                if encoded.startswith(b'RIFF') and encoded[8:12]==b'WEBP' or encoded.startswith(b'\x89PNG'):
                    image=Image.open(io.BytesIO(encoded))
                elif struct.unpack_from('<I',data,48)[0] in (17,18,19,22):
                    # Shipped Godot BPTC_RGBA textures use BC7 blocks after the image header.
                    width,height=struct.unpack_from('<HH',data,40)
                    if not 0<width<=8192 or not 0<height<=8192:raise ValueError('Invalid texture size')
                    codec={17:(1,'DXT1'),18:(2,'DXT3'),19:(3,'DXT5'),22:(7,'BC7')}[struct.unpack_from('<I',data,48)[0]]
                    image=Image.frombytes('RGBA',(width,height),data[52:],'bcn',codec)
                else:raise ValueError('Unsupported texture encoding')
                with image:
                    image.thumbnail((500,380));path=output/(identity+'.webp');image.convert('RGBA').save(path,format='WEBP',quality=85)
                images[identity]=dict(file=path.name,folder=folder,source=name,texture=resource,texture_md5=entries[resource][2].hex(),illustration=illustration)
            except (KeyError,ValueError,AttributeError,OSError):missing.append(identity)
    manifest=dict(schema_version='portraits/1.0',game_assembly_sha256=assembly_hash,game_version=catalog['game_version'],entries=images,missing=missing)
    (asset_root/'portraits.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(exported=len(images),unavailable=missing),ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--game-path',type=Path,default=Path('E:/steam/steamapps/common/Slay the Spire 2'));a=p.parse_args()
    extract(a.game_path/'SlayTheSpire2.pck',a.game_path/'data_sts2_windows_x86_64/sts2.dll')
