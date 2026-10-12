import {playView,launchErrors} from './play-state.js';
const O=window.Observer,E=O.el,host=document.getElementById('play-controls');
if(host){
 let data=null,busy=false,polling=false,editedProfile=false,pending=null,localError='',profileOptions='';
 const title=E('div',undefined,'play-intro');title.append(E('span','AUTONOMOUS RUN','eyebrow'),E('h2','从这里开始爬塔'),E('p','选好决策后端与新局设置，其余交给 Agent。游戏菜单会自动操作。','muted'));
 const form=E('div',undefined,'play-form');
 function field(label,input){const wrap=E('label',label);input.setAttribute('aria-label',label);wrap.append(input);form.append(wrap);return input}
 const provider=field('决策后端',E('select'));provider.onchange=()=>{editedProfile=true;render()};
 const character=field('新局职业',E('select'));for(const[id,name]of [['IRONCLAD','铁甲战士'],['SILENT','静默猎手'],['DEFECT','故障机器人'],['NECROBINDER','亡灵契约师'],['REGENT','储君']]){const option=E('option',name);option.value=id;character.append(option)}
 const difficulty=field('新局进阶',E('select'));for(const[id,name]of [['highest','该职业已解锁的最高进阶'],['custom','指定进阶']]){const option=E('option',name);option.value=id;difficulty.append(option)}
 const level=field('指定进阶等级',E('input'));level.type='number';level.min='0';level.step='1';level.value='0';const levelWrap=level.parentElement;levelWrap.hidden=true;difficulty.onchange=()=>{levelWrap.hidden=difficulty.value!=='custom'};
 const save=E('div',undefined,'play-save'),replaceLabel=E('label',undefined,'check-label'),replace=E('input');replace.type='checkbox';replaceLabel.append(replace,document.createTextNode('我确认放弃现有存档并开始新局（无法撤销）'));replace.onchange=render;
 const actions=E('div',undefined,'play-actions'),newButton=E('button','开始新局并自动游玩','primary'),continueButton=E('button','继续存档并自动游玩'),manage=E('a','配置模型 →','text-button');manage.href='#agent';actions.append(continueButton,newButton,manage);
 const reason=E('div',undefined,'play-reasons'),progress=E('div',undefined,'play-progress'),progressTitle=E('strong','准备开始'),progressDetail=E('p',undefined,'muted'),cancel=E('button','暂停 / 取消启动');progress.setAttribute('role','status');progress.append(progressTitle,progressDetail,cancel);
 host.append(title,form,save,replaceLabel,actions,reason,progress);
 function render(){
  const previous=provider.value,profiles=data?.agent?.profiles||[],signature=JSON.stringify(profiles.map(p=>[p.id,p.label,p.ready]));if(data&&signature!==profileOptions){provider.replaceChildren();for(const p of profiles){const option=E('option',p.label+(p.ready?'':' · 待配置'));option.value=p.id;provider.append(option)}profileOptions=signature}if(data)provider.value=editedProfile&&profiles.some(p=>p.id===previous)?previous:data.agent.active||profiles[0]?.id||'';
  const v=playView(data,{profileId:provider.value,replaceSave:replace.checked,busy:busy||!!pending});
  save.textContent=v.current?'游戏已进入对局，可直接接管当前局。':v.saved?'发现一份未完成存档，推荐继续游玩。':data?.game?.connected?'游戏已连接，准备开始新局。':'启动时会自动打开游戏，并等待模组连接。';
  replaceLabel.hidden=!v.saved||v.current;newButton.disabled=!!v.newReason;continueButton.disabled=!!v.continueReason;continueButton.textContent=v.continueLabel;
  const preferNew=!v.current&&(!v.saved||replace.checked);newButton.classList.toggle('primary',preferNew);continueButton.classList.toggle('primary',!preferNew);
  reason.replaceChildren();for(const text of new Set([v.newReason,v.continueReason].filter(Boolean)))reason.append(E('p',text,'hint'));
  progressTitle.textContent=v.progress;progressDetail.textContent=localError||v.error||(data?.agent?.running?'Agent 正在自动游玩。实时观察可查看每次决策。':data?.agent?.launch_active?'正在执行启动流程，可以暂停。':'Agent 当前不会提交新的游戏操作。');
  cancel.disabled=!v.active||busy;for(const input of [provider,character,difficulty,level,replace])input.disabled=v.active||busy||!!pending;
 }
 function clearPending(){pending=null;try{sessionStorage.removeItem('autoslay-launch-pending')}catch{}}
 async function sendPending(){try{const response=await O.api('/api/play/start',pending);data={...data,...response};clearPending()}catch(e){if(e.message in launchErrors||['INVALID_REQUEST','REQUEST_ID_CONFLICT','UNKNOWN_PROFILE'].includes(e.message)){localError=launchErrors[e.message]||'启动参数未通过校验，请检查设置。';clearPending()}else localError='启动请求结果尚未确认，恢复连接后会使用同一请求编号查询。'}}
 async function refresh(){if(polling)return;polling=true;try{data=await O.api('/api/play/state'+(pending?'?request_id='+encodeURIComponent(pending.request_id):''));if(pending){if(data.job?.request?.request_id===pending.request_id){clearPending();localError=''}else if(!busy)await sendPending()}render();window.dispatchEvent(new CustomEvent('autoslay:play',{detail:data}));if(data.job?.status==='completed'&&data.agent.running&&location.hash.startsWith('#play'))location.hash='live'}catch{data=null;render();progressTitle.textContent='本地服务未连接';progressDetail.textContent=pending?'启动请求结果尚未确认，正在等待服务恢复；不会重复提交游戏操作。':'请检查启动脚本。'}finally{polling=false}}
 async function start(mode){if(busy||pending)return;if(difficulty.value==='custom'&&(!Number.isInteger(Number(level.value))||Number(level.value)<0||level.value==='')){progressDetail.textContent='进阶等级需要是非负整数。';return}
  busy=true;localError='';pending={request_id:'play-'+crypto.randomUUID(),mode,profile_id:provider.value,character:character.value,ascension:difficulty.value==='highest'?null:Number(level.value),replace_save:replace.checked,expected_epoch:data?.game?.epoch??null,expected_state_version:data?.game?.state_version??null};try{sessionStorage.setItem('autoslay-launch-pending',JSON.stringify(pending))}catch{}render();
  try{await sendPending()}finally{busy=false;await refresh()}
 }
 newButton.onclick=()=>start('new');continueButton.onclick=()=>start(playView(data).current?'current':'continue');cancel.onclick=async()=>{busy=true;render();try{await O.api('/api/agent/pause',{});localError=''}catch{localError='暂停请求未确认，请检查服务与游戏状态。'}finally{busy=false;await refresh();window.AgentPanel?.refresh()}};
 try{pending=JSON.parse(sessionStorage.getItem('autoslay-launch-pending')||'null')}catch{}
 render();refresh();setInterval(refresh,2000);window.addEventListener('autoslay:agent',()=>{if(!polling)refresh()});
}
