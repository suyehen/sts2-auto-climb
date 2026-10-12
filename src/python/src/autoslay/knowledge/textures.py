"""Decode shipped Godot textures without replacing or inventing artwork."""
import io,re,struct
from PIL import Image


def read_texture(pack,path):
    name=path.removeprefix('res://')+'.import'
    metadata=pack.read(name).decode()
    match=re.search(r'path(?:\.(?:bptc|s3tc))?="res://([^"]+)"',metadata)
    if not match:raise ValueError('No texture import')
    resource=match[1];data=pack.read(resource)
    if data[:4]!=b'GST2':raise ValueError('Unsupported texture')
    encoded=data[56:]
    if encoded.startswith(b'RIFF') and encoded[8:12]==b'WEBP' or encoded.startswith(b'\x89PNG'):
        image=Image.open(io.BytesIO(encoded));image.load()
    else:
        width,height=struct.unpack_from('<HH',data,40);format_id=struct.unpack_from('<I',data,48)[0]
        codecs={17:(1,'DXT1'),18:(2,'DXT3'),19:(3,'DXT5'),22:(7,'BC7')}
        if not 0<width<=8192 or not 0<height<=8192 or format_id not in codecs:raise ValueError('Unsupported texture format')
        image=Image.frombytes('RGBA',(width,height),data[52:],'bcn',codecs[format_id])
    return image.convert('RGBA'),dict(source=name,texture=resource,texture_md5=pack.entries[resource][2].hex())
