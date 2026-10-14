"""Render native local monster visuals with the installed game's Spine renderer."""
import argparse,hashlib,json,re,subprocess
from pathlib import Path
from PIL import Image
from autoslay.paths import ROOT
from autoslay.knowledge_base.game_pack import GamePack
from autoslay.knowledge_base.behavior import load
from autoslay.knowledge_base.textures import read_texture
from autoslay.knowledge_base.source_audit import body


def export(game):
    data=load();digest=hashlib.sha256((game/'data_sts2_windows_x86_64/sts2.dll').read_bytes()).hexdigest()
    if digest!=data['game_assembly_sha256']:raise ValueError('Refresh behavior catalog for this game build first')
    pack=GamePack(game/'SlayTheSpire2.pck');project=ROOT/'.runtime/cache/monster-render';project.mkdir(parents=True,exist_ok=True)
    (project/'project.godot').write_text('config_version=5\n[application]\nconfig/name="AutoSlay artwork export"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n',encoding='utf-8')
    copied=set()
    # Only the installed Spine extension is needed. No FMOD, Sentry, C# game
    # scripts, autoloads or user data are included in the isolated resource pack.
    native=game/'libspine_godot.windows.template_release.x86_64.dll'
    extension='addons/spine/spine_godot_extension.gdextension'
    (project/extension).parent.mkdir(parents=True,exist_ok=True)
    (project/extension).write_text('[configuration]\nentry_symbol="spine_godot_library_init"\ncompatibility_minimum="4.1"\n[libraries]\nwindows.release.x86_64="'+native.as_posix()+'"\n',encoding='utf-8')
    (project/'.godot').mkdir(exist_ok=True)
    (project/'.godot/extension_list.cfg').write_text('res://'+extension+'\n',encoding='utf-8')
    (project/'.godot/global_script_class_cache.cfg').write_text('list=Array[Dictionary]([])\n',encoding='utf-8')
    copied.update([extension,'.godot/extension_list.cfg','.godot/global_script_class_cache.cfg'])
    def copy_resource(name):
        name=name.removeprefix('res://')
        if name in copied:return
        copied.add(name)
        if name not in pack.entries:
            if name+'.import' in pack.entries:copy_resource(name+'.import');return
            raise ValueError('Missing asset '+name)
        raw=pack.read(name);target=project/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        if name.endswith(('.import','.tres')):
            for dependency in re.findall(r'"res://([^"]+)"',raw.decode()):copy_resource(dependency)
        elif name.endswith('.spatlas'):
            atlas=json.loads(raw);base=Path(atlas['source_path'].removeprefix('res://')).parent
            for line in atlas['atlas_data'].replace('\\n','\n').splitlines():
                if line.endswith('.png'):copy_resource((base/line).as_posix())
    jobs=[];missing=[];sources={};static_images={};output=ROOT/'data/knowledge/assets/monsters';output.mkdir(parents=True,exist_ok=True)
    for entry in data['entries']:
        if entry['category']!='monster':continue
        identity=entry['entity_id'];stem=identity.split('.')[1].lower()
        scene='scenes/creature_visuals/'+stem+'.tscn'
        # The model deliberately reuses the Defect visuals for the debug dummy.
        if identity=='MONSTER.BIG_DUMMY':scene='scenes/creature_visuals/defect.tscn'
        if scene not in pack.entries:missing.append(identity);continue
        text=pack.read(scene).decode();resources={}
        for line in text.splitlines():
            if line.startswith('[ext_resource'):
                attrs=dict(re.findall(r'(\w+)="([^"]+)"',line))
                if attrs.get('type')=='SpineSkeletonDataResource':resources[attrs['id']]=attrs['path']
        node=re.search(r'\[node name="Visuals" type="SpineSprite"[^\]]*\]([^\[]+)',text)
        if not node:
            sprite=re.search(r'\[node name="Visuals" type="Sprite2D"[^\]]*\]([^\[]+)',text)
            texture_id=re.search(r'texture = ExtResource\("([^"]+)"\)',sprite[1]) if sprite else None
            texture=re.search(r'\[ext_resource type="Texture2D"[^\]]*path="([^"]+)"[^\]]*id="'+re.escape(texture_id[1])+'"',text) if texture_id else None
            if texture:
                image,meta=read_texture(pack,texture[1]);image.thumbnail((384,384));image.save(output/(identity+'.webp'),format='WEBP',quality=90)
                static_images[identity]=dict(meta,file=identity+'.webp',folder='monsters',kind='game_placeholder' if 'placeholder' in texture[1] else 'native_texture',scene=scene)
            else:missing.append(identity)
            continue
        block=node[1];skeleton=re.search(r'skeleton_data_res = ExtResource\("([^"]+)"\)',block)
        if not skeleton or skeleton[1] not in resources:missing.append(identity);continue
        resource=resources[skeleton[1]]
        try:copy_resource(resource)
        except ValueError:missing.append(identity);continue
        skin=re.search(r'preview_skin = "([^"]+)"',block);animation=re.search(r'preview_animation = "([^"]+)"',block)
        model=ROOT/'.runtime/cache'/('behavior-source-'+digest[:16])/entry['provenance']['source_file']
        model_text=model.read_text(encoding='utf-8-sig') if model.exists() else ''
        setup=body(model_text,'SetupSkins') or ''
        skins=re.findall(r'FindSkin\("([^\"]+)"\)',setup)
        if 'if (' in setup:skins=skins[:1] # One valid visual variant, not mutually exclusive skins together.
        for option in re.findall(r'NextItem\((\w+)\)',setup):
            choices=re.search(r'\b'+re.escape(option)+r'\s*=\s*new string\[[^\]]*\]\s*\{([^}]+)',model_text)
            if choices:
                first=re.search(r'"([^\"]+)"',choices[1])
                if first:skins.append(first[1])
        if re.search(r'HasPhobiaSpineSkin\s*=>\s*true',model_text):skins=['normal']
        tracks=[dict(animation=m[1],track=int(m[2])) for m in re.finditer(r'SetAnimation\("([^\"]+)",\s*loop:\s*true,\s*(\d+)\)',setup)]
        jobs.append(dict(entity_id=identity,resource=resource,skins=skins,skin='' if skins else (skin[1] if skin and skin[1]!='-- Empty --' else ''),tracks=tracks,animation=animation[1] if animation and animation[1]!='-- Empty --' else '',output=str(project/(identity+'.png'))))
        sources[identity]=dict(scene=scene,resource=resource,skins=skins,tracks=tracks,scene_md5=pack.entries[scene][2].hex(),kind='native_spine_preview')
    job_path=project/'jobs.json';job_path.write_text(json.dumps(jobs),encoding='utf-8')
    flags=0x08000000 if hasattr(subprocess,'CREATE_NO_WINDOW') else 0
    script=str(ROOT/'src/godot/tools/export_monster_portraits.gd');isolated=project/'artwork.pck'
    files=[dict(resource='res://'+name,source=str(project/name)) for name in sorted(copied) if (project/name).is_file()]
    files.append(dict(resource='res://project.godot',source=str(project/'project.godot')))
    file_list=project/'pack-files.json';file_list.write_text(json.dumps(files),encoding='utf-8')
    packed=subprocess.run([str(game/'SlayTheSpire2.exe'),'--headless','--script',script,'--','--pack',str(isolated),str(file_list)],capture_output=True,timeout=120,creationflags=flags)
    if packed.returncode:raise ValueError('Could not build isolated artwork pack')
    for job in jobs:Path(job['output']).unlink(missing_ok=True)
    result=subprocess.run([str(game/'SlayTheSpire2.exe'),'--main-pack',str(isolated),'--script',script,
        '--rendering-method','gl_compatibility','--position','-32000,-32000','--',str(job_path)],capture_output=True,timeout=240,creationflags=flags)
    (ROOT/'.runtime/logs/monster-render.out.log').write_bytes(result.stdout)
    (ROOT/'.runtime/logs/monster-render.err.log').write_bytes(result.stderr)
    if result.returncode:raise ValueError('Native portrait renderer failed; see .runtime/logs/monster-render.err.log')
    images=dict(static_images)
    for job in jobs:
        path=Path(job['output'])
        if not path.exists():missing.append(job['entity_id']);continue
        with Image.open(path) as image:
            if not image.convert('RGBA').getchannel('A').getbbox():missing.append(job['entity_id']);continue
            image.convert('RGBA').save(output/(job['entity_id']+'.webp'),format='WEBP',quality=90)
        images[job['entity_id']]=dict(sources[job['entity_id']],file=job['entity_id']+'.webp',folder='monsters')
    manifest=dict(schema_version='monster-portraits/1.0',game_assembly_sha256=digest,game_version=data['game_version'],entries=images,missing=sorted(set(missing)))
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(exported=len(images),missing=manifest['missing']),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--game-path',type=Path,default=Path('E:/steam/steamapps/common/Slay the Spire 2'))
    export(parser.parse_args().game_path)
