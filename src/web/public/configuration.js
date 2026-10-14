/* Configuration portability only. No run archives or telemetry are exported. */
const host=document.getElementById('config-controls');
if(host){
 const O=window.Observer,E=O.el,api=O.api;
 let packageData=null,previewReady=false,busy=false;
 const heading=E('div',undefined,'surface-heading');heading.append(E('h2','配置备份与迁移'),E('span','格式 v2','badge'));
 const note=E('p','只包含业务配置，不包含对局记录、决策快照或实战校准数据。','muted');
 const grid=E('div',undefined,'config-transfer-grid'),exportBox=E('div'),importBox=E('div');
 exportBox.append(E('h3','导出当前配置'));
 const modeLabel=E('label','导出范围'),mode=E('select');mode.setAttribute('aria-label','配置导出范围');
 for(const [value,label]of [['standard','标准配置 · 不含密钥'],['complete','完整配置 · 含密钥，密码加密']]){const o=E('option',label);o.value=value;mode.append(o)}modeLabel.append(mode);
 const exportPasswordLabel=E('label','备份密码（至少 8 个字符）'),exportPassword=E('input');exportPassword.type='password';exportPassword.autocomplete='new-password';exportPassword.setAttribute('aria-label','配置备份密码');exportPasswordLabel.append(exportPassword);exportPasswordLabel.hidden=true;
 const exportButton=E('button','导出配置','primary');exportBox.append(modeLabel,exportPasswordLabel,exportButton);
 exportBox.append(E('p','标准配置可用于共享。完整配置使用 AES-256-GCM 加密，导入时需要备份密码。','hint'));
 mode.onchange=()=>{exportPasswordLabel.hidden=mode.value!=='complete';exportPassword.value=''};
 importBox.append(E('h3','导入已有配置'));
 const fileLabel=E('label','选择配置文件'),file=E('input');file.type='file';file.accept='.json,application/json';file.setAttribute('aria-label','选择配置文件');fileLabel.append(file);
 const importPasswordLabel=E('label','备份密码'),importPassword=E('input');importPassword.type='password';importPassword.autocomplete='off';importPassword.setAttribute('aria-label','导入配置密码');importPasswordLabel.append(importPassword);importPasswordLabel.hidden=true;
 const previewButton=E('button','读取并预览'),applyButton=E('button','应用配置并暂停 Agent','primary');previewButton.disabled=applyButton.disabled=true;
 const buttons=E('div',undefined,'button-row');buttons.append(previewButton,applyButton);importBox.append(fileLabel,importPasswordLabel,buttons,E('p','支持旧版配置迁移。应用前自动备份当前配置；现有密钥只在后端身份未变化时保留。','hint'));
 grid.append(exportBox,importBox);
 const preview=E('div',undefined,'config-preview'),message=E('p',undefined,'agent-notice');message.setAttribute('role','status');
 const backups=E('details');backups.append(E('summary','导入与升级前的本地备份'));const backupList=E('div'),backupButton=E('button','查看备份');backups.append(backupButton,backupList);
 host.append(heading,note,grid,preview,message,backups);
 const errors={CONFIG_PASSWORD_REQUIRED:'请输入至少 8 个字符的备份密码。',CONFIG_PASSWORD_OR_FILE_INVALID:'密码不正确，或文件已经损坏。',CONFIG_VERSION_UNSUPPORTED:'配置版本暂不支持；原配置未被覆盖。',CONFIG_CONTAINS_CREDENTIALS:'标准配置不接受明文密钥，请使用完整加密配置。',CONFIG_UNKNOWN_FIELDS:'配置含有无法识别的字段，请检查版本。',CONFIG_PROOF_REQUIREMENTS_FIXED:'此配置试图修改自动执行的固定校验条件，未应用。'};
 async function act(fn){if(busy)return;busy=true;exportButton.disabled=previewButton.disabled=applyButton.disabled=backupButton.disabled=true;try{await fn()}catch(e){message.textContent=errors[e.message]||'操作未完成，原配置保持不变。'}finally{busy=false;exportButton.disabled=backupButton.disabled=false;previewButton.disabled=!packageData;applyButton.disabled=!previewReady}}
 function showPreview(p){preview.replaceChildren(E('h3','即将应用的配置'));const selected=p.profiles.find(v=>v.id===p.active);preview.append(E('p','默认后端：'+(selected?.label||p.active)),E('p','文件格式 v'+p.migration.from_version+' → 当前格式 v'+p.migration.to_version));for(const row of p.profiles)preview.append(E('p',row.label+' · '+({codex:'本机 Codex',api:'云端 API',local:'本地模型'})[row.kind]+(row.base_url?' · '+row.base_url:'')+(row.model?' · '+row.model:''),'config-profile-preview'));preview.append(E('p','变更的决策参数：'+(p.changed_policies.join('、')||'无')),E('p',p.credentials_included?'将恢复 '+p.credential_count+' 组密钥（密钥不显示）。':'不包含密钥。'));if(p.migration.reset_executable_profiles.length)preview.append(E('p','本机 Codex 程序路径重置为默认值，避免沿用其他电脑的路径。'));}
 file.onchange=async()=>{previewReady=false;packageData=null;preview.replaceChildren();importPassword.value='';applyButton.disabled=true;try{const selected=file.files[0];if(!selected)return;if(selected.size>240000)throw Error('TOO_LARGE');packageData=JSON.parse(await selected.text());importPasswordLabel.hidden=packageData.format!=='autoslay-config-encrypted';previewButton.disabled=false;message.textContent='文件已读取，预览后可应用。'}catch{previewButton.disabled=true;message.textContent='文件不是有效配置，或超过大小限制。'}};
 importPassword.oninput=()=>{previewReady=false;applyButton.disabled=true};
 previewButton.onclick=()=>act(async()=>{previewReady=false;const p=await api('/api/config/preview',{package:packageData,password:importPassword.value});showPreview(p);previewReady=true;message.textContent='预览完成，尚未修改配置。'});
 exportButton.onclick=()=>act(async()=>{try{const p=await api('/api/config/export',{mode:mode.value,password:exportPassword.value,ui:{display_mode:'human'}});const blob=new Blob([JSON.stringify(p,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=E('a');a.href=url;a.download='autoslay-config.'+(mode.value==='complete'?'encrypted':'standard')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message.textContent='配置已导出，不包含对局数据。'}finally{exportPassword.value=''}});
 function applyUI(value){localStorage.setItem('autoslay-display-mode','human');window.dispatchEvent(new CustomEvent('autoslay:configuration',{detail:{...value,display_mode:'human'}}));}
 applyButton.onclick=()=>act(async()=>{try{const r=await api('/api/config/import',{package:packageData,password:importPassword.value});applyUI(r.ui);await window.AgentPanel?.refresh();preview.replaceChildren();message.textContent='配置已应用，Agent 已暂停，旧配置已备份。'+(r.credentials_need_reentry.length?'部分后端需重新填写密钥。':'');}finally{importPassword.value='';previewReady=false}});
 backupButton.onclick=()=>act(async()=>{const r=await api('/api/config/backups',{});backupList.replaceChildren();if(!r.backups.length)backupList.append(E('p','尚无本地备份。','hint'));for(const item of r.backups){const row=E('div',undefined,'config-backup-row'),restore=E('button','恢复并暂停');row.append(E('span',item.id),restore);restore.onclick=()=>act(async()=>{const r=await api('/api/config/restore',{backup_id:item.id});applyUI(r.ui);await window.AgentPanel?.refresh();message.textContent='已恢复备份，Agent 保持暂停；恢复前的配置也已备份。'});backupList.append(row)}});
}
