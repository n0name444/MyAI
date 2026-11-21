/* MyAI: основная страница чата. Панель «Вычисления» живёт в calc.js и использует отсюда $, esc, HEADERS и addMessage. */
const $=s=>document.querySelector(s);
const chat=$('#chat'),form=$('#form'),input=$('#input'),send=$('#send'),routeTop=$('#routeTop'),jump=$('#jumpBtn');
const TOKEN=document.querySelector('meta[name=myai-token]').content;
const HEADERS={'Content-Type':'application/json','X-MyAI-Token':TOKEN};
const HINT_DEFAULT='Enter — отправить · Shift+Enter — новая строка · Esc — остановить ответ';
let busy=false,controller=null,stickBottom=true;

const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* Безопасный мини-Markdown: сначала экранирование, потом разметка. Ссылки — только http(s). */
function md(src){
  const blocks=[];
  let s=String(src??'').replace(/```[\w+-]*\n?([\s\S]*?)```/g,(m,code)=>{blocks.push(code.replace(/\n$/,''));return '\u0000'+(blocks.length-1)+'\u0000'});
  s=esc(s);
  s=s.replace(/`([^`\n]+)`/g,'<code>$1</code>');
  s=s.replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>');
  s=s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s=s.replace(/^[-*] /gm,'• ');
  s=s.replace(/\u0000(\d+)\u0000/g,(m,i)=>'<pre><code>'+esc(blocks[i])+'</code></pre>');
  return s;
}

/* ---------- уведомления ---------- */
let toastTimer=null;
function announce(text){$('#srStatus').textContent=text}
function toast(text,kind='ok'){
  let el=$('#toast');
  if(!el){el=document.createElement('div');el.id='toast';document.body.append(el)}
  el.textContent=text;el.className='toast'+(kind==='error'?' error':'');el.hidden=false;
  announce(text);
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>{el.hidden=true},4000);
}

/* ---------- возможности: карточки на пустом экране и в окне «Что умеет MyAI» ---------- */
const CAPABILITIES=[
  {icon:'☁️',title:'Погода',text:'Прогноз по городу на сегодня или завтра.',examples:['Какая погода в Архангельске сейчас?','Нужен ли завтра зонт в Минске?']},
  {icon:'🌐',title:'Интернет',text:'Свежие новости и факты. Ответ со ссылками на источники.',examples:['Найди в интернете новости про Python','Курс доллара сегодня']},
  {icon:'🎌',title:'Аниме',text:'Подбор по жанрам, годам и «похожему на…».',examples:['Найди аниме про машины','Посоветуй аниме как Solo Leveling']},
  {icon:'🧮',title:'Расчёты',text:'Арифметика, проценты, скидки и НДС, единицы. Больше возможностей в разделе «Вычисления».',examples:['Посчитай 125 × 48','Сколько будет 15% от 200?']},
  {icon:'🧠',title:'Память',text:'Запоминает имя и заметки между разговорами. Их можно просмотреть и удалить в разделе «Память».',examples:['Запомни: я люблю игры','Что ты помнишь?']},
  {icon:'🖥️',title:'Компьютер',text:'Открывает приложения и сайты из белого списка (только Windows).',examples:['Открой блокнот','Открой ютуб']},
];
function capsHtml(){
  return CAPABILITIES.map(c=>`<section class="cap"><h2><span aria-hidden="true">${c.icon}</span> ${esc(c.title)}</h2><p>${esc(c.text)}</p><div class="cap-ex">${c.examples.map(t=>`<button type="button" class="prompt" data-example="${esc(t)}">${esc(t)}</button>`).join('')}</div></section>`).join('');
}
function emptyHtml(){
  return `<div class="empty" id="empty"><div class="bigmark">AI</div><h1>Чем могу помочь?</h1><p>Спрашивайте обычными словами: MyAI сам поймёт, нужен ли интернет, погода, аниме, калькулятор, память или управление компьютером. Нажмите на пример, чтобы вставить его в поле ввода.</p><div class="caps">${capsHtml()}</div></div>`;
}
function removeEmpty(){$('#empty')?.remove()}
function showEmpty(){chat.innerHTML=emptyHtml();routeTop.textContent='';stickBottom=true;jump.hidden=true}
// Пример только вставляется в поле: команды вроде «Закрой …» не должны уходить без подтверждения Enter.
document.addEventListener('click',e=>{
  const b=e.target.closest('[data-example]');if(!b)return;
  input.value=b.dataset.example;resize();closeCaps(true);input.focus();
});

/* ---------- прокрутка ---------- */
const nearBottom=()=>chat.scrollHeight-chat.scrollTop-chat.clientHeight<120;
function scrollDown(force){
  if(force||stickBottom){chat.scrollTop=chat.scrollHeight;stickBottom=true;jump.hidden=true}
  else if(chat.querySelector('.message'))jump.hidden=false;
}
chat.addEventListener('scroll',()=>{stickBottom=nearBottom();jump.hidden=stickBottom||!chat.querySelector('.message')},{passive:true});
jump.onclick=()=>scrollDown(true);

/* ---------- сообщения ---------- */
function makeRow(role){
  removeEmpty();
  const row=document.createElement('div');row.className='message '+role;
  row.innerHTML=`<div class="avatar" aria-hidden="true">${role==='bot'?'AI':'Вы'}</div><div class="content"><div class="bubble"></div><div class="meta"></div><div class="sources"></div><div class="actions"></div></div>`;
  chat.appendChild(row);return row;
}
function setBubble(row,text){
  const bubble=row.querySelector('.bubble');bubble.innerHTML=md(text);
  bubble.querySelectorAll('pre').forEach(pre=>{
    const b=document.createElement('button');b.type='button';b.className='code-copy';b.textContent='Копировать';pre.prepend(b);
  });
}
function setMeta(row,d){
  row.querySelector('.meta').innerHTML=(d.route?`<span class="badge">${esc(d.route)}</span>`:'')+(d.model?`<span class="badge">${esc(d.model)}</span>`:'');
  row.querySelector('.sources').innerHTML=(d.sources||[]).filter(x=>/^https?:\/\//i.test(x.url||'')).map(x=>`<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.title||x.url)}</a>`).join('');
}
// «Копировать» работает с исходным текстом ответа, «Повторить вопрос» отправляет тот же вопрос ещё раз.
function setActions(row,raw,retryText){
  row._raw=raw||'';
  row.querySelector('.actions').innerHTML=(row._raw?'<button type="button" data-act="copy">Копировать</button>':'')+(retryText?'<button type="button" data-act="retry">Повторить вопрос</button>':'');
  if(retryText)row.dataset.retry=retryText;else delete row.dataset.retry;
}
function addMessage(text,role,meta={}){
  const row=makeRow(role);setBubble(row,text);
  if(role==='bot'){setMeta(row,meta);setActions(row,text,'')}
  scrollDown(role==='user');return row;
}
function copyText(text){
  if(navigator.clipboard&&navigator.clipboard.writeText)return navigator.clipboard.writeText(text);
  return new Promise((resolve,reject)=>{
    const ta=document.createElement('textarea');ta.value=text;ta.style.cssText='position:fixed;opacity:0';document.body.append(ta);ta.select();
    try{document.execCommand('copy')?resolve():reject(new Error('copy failed'))}catch(e){reject(e)}finally{ta.remove()}
  });
}
function flash(btn,text){
  const old=btn.dataset.label||btn.textContent;btn.dataset.label=old;btn.textContent=text;
  clearTimeout(btn._timer);btn._timer=setTimeout(()=>{btn.textContent=old},1500);
}
chat.addEventListener('click',async e=>{
  const codeBtn=e.target.closest('.code-copy');
  if(codeBtn){
    try{await copyText(codeBtn.parentElement.querySelector('code').textContent);flash(codeBtn,'Скопировано ✓')}
    catch(err){flash(codeBtn,'Не удалось')}
    return;
  }
  const act=e.target.closest('[data-act]'),row=e.target.closest('.message');
  if(!act||!row)return;
  if(act.dataset.act==='copy'){
    try{await copyText(row._raw||row.querySelector('.bubble').textContent);flash(act,'Скопировано ✓')}
    catch(err){flash(act,'Не удалось')}
  }else if(act.dataset.act==='retry'){
    if(busy){hintBusy();return}
    ask(row.dataset.retry);
  }
});

/* ---------- отправка ---------- */
function setBusy(on){
  busy=on;chat.setAttribute('aria-busy',String(on));
  send.textContent=on?'■':'↑';send.title=on?'Стоп (Esc)':'Отправить';send.setAttribute('aria-label',on?'Остановить ответ':'Отправить');send.classList.toggle('stop',on);
  $('#sys').textContent=on?'MyAI думает…':'Система готова';
  const free=!document.querySelector('.overlay:not([hidden]),.calc-overlay.open');
  if(!on&&free&&(document.activeElement===document.body||document.activeElement===send))input.focus();
}
let hintTimer=null;
function hintBusy(){
  const h=$('#hint');h.textContent='Ответ ещё идёт: дождитесь его или нажмите «Стоп».';
  clearTimeout(hintTimer);hintTimer=setTimeout(()=>{h.textContent=HINT_DEFAULT},2500);
}
async function ask(text){
  addMessage(text,'user');
  const row=makeRow('bot');
  const bubble=row.querySelector('.bubble');
  bubble.innerHTML='<div class="typing"><i></i><i></i><i></i></div><div class="status-line"></div>';
  const statusLine=bubble.querySelector('.status-line');
  let acc='',started=false,finalText='',aborted=false,failed=false;
  const show=t=>{finalText=t;setBubble(row,t)};
  controller=new AbortController();setBusy(true);
  try{
    const r=await fetch('/chat/stream',{method:'POST',headers:HEADERS,body:JSON.stringify({text}),signal:controller.signal});
    if(!r.ok)throw new Error(r.status===403?'Сессия устарела — обновите страницу (F5).':'Ошибка сервера ('+r.status+')');
    const reader=r.body.getReader(),dec=new TextDecoder();let buf='';
    const handle=ev=>{
      if(ev.type==='status'&&!started){statusLine.textContent=ev.text}
      else if(ev.type==='meta'){routeTop.textContent='· '+ev.route}
      else if(ev.type==='delta'){started=true;acc+=ev.text;show(acc);scrollDown()}
      else if(ev.type==='done'){show(ev.response||'Пустой ответ.');setMeta(row,ev);routeTop.textContent=ev.route?'· '+ev.route:'';scrollDown()}
    };
    for(;;){
      const {value,done}=await reader.read();if(done)break;
      buf+=dec.decode(value,{stream:true});
      let i;while((i=buf.indexOf('\n'))>=0){const line=buf.slice(0,i).trim();buf=buf.slice(i+1);if(line)handle(JSON.parse(line))}
    }
    if(!finalText)show('Пустой ответ.');
  }catch(e){
    if(e.name==='AbortError'){aborted=true;setBubble(row,(acc||'')+(acc?'\n\n':'')+'(остановлено)')}
    else{failed=true;row.classList.add('error');show('Ошибка: '+e.message)}
  }finally{
    controller=null;setBusy(false);
    setActions(row,aborted?acc:(failed?'':finalText),text);
    announce(failed?'Ошибка':(aborted?'Ответ остановлен':'Ответ получен'));
    status();
  }
}
form.addEventListener('submit',e=>{
  e.preventDefault();
  if(busy){controller?.abort();return}        // кнопка «Стоп»
  const t=input.value.trim();if(!t)return;
  input.value='';resize();ask(t);scrollDown(true);
});
input.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){
    e.preventDefault();
    if(busy){hintBusy();return}                // набирать можно и во время ответа, отправка ждёт
    form.requestSubmit();
  }
});
input.addEventListener('input',resize);
function resize(){input.style.height='auto';input.style.height=Math.min(input.scrollHeight,170)+'px'}

/* ---------- диалоги ---------- */
function trapTab(e,container){
  const items=[...container.querySelectorAll('button,a[href],input,select,textarea,summary')].filter(x=>!x.disabled&&!x.hidden&&x.offsetParent!==null);
  if(!items.length)return;
  const first=items[0],last=items[items.length-1];
  if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}
  else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}
}
let dialogResolve=null,dialogLastFocus=null;
function askConfirm({title,text,ok='Подтвердить'}){
  return new Promise(resolve=>{
    dialogResolve=resolve;dialogLastFocus=document.activeElement;
    $('#dialogTitle').textContent=title;$('#dialogText').textContent=text;$('#dialogOk').textContent=ok;
    $('#dialogOverlay').hidden=false;$('#dialogCancel').focus();
  });
}
function closeDialog(result){
  $('#dialogOverlay').hidden=true;
  const resolve=dialogResolve;dialogResolve=null;
  if(dialogLastFocus&&dialogLastFocus.focus)dialogLastFocus.focus();
  if(resolve)resolve(result);
}
$('#dialogOk').onclick=()=>closeDialog(true);
$('#dialogCancel').onclick=()=>closeDialog(false);
$('#dialogOverlay').addEventListener('click',e=>{if(e.target.id==='dialogOverlay')closeDialog(false)});

let capsLastFocus=null;
function openCaps(){capsLastFocus=document.activeElement;$('#capsGrid').innerHTML=capsHtml();$('#capsOverlay').hidden=false;$('#capsClose').focus()}
function closeCaps(skipFocus){
  if($('#capsOverlay').hidden)return;
  $('#capsOverlay').hidden=true;
  if(!skipFocus&&capsLastFocus&&capsLastFocus.focus)capsLastFocus.focus();
}
$('#capsBtn').onclick=openCaps;
$('#capsClose').onclick=()=>closeCaps();
$('#capsOverlay').addEventListener('click',e=>{if(e.target.id==='capsOverlay')closeCaps()});

/* ---------- боковая панель как выезжающее меню на узких экранах ---------- */
const sidebar=$('#sidebar'),backdrop=$('#drawerBackdrop'),menuBtn=$('#menuBtn');
const mq=window.matchMedia?window.matchMedia('(max-width:800px)'):null;
const sidebarOpen=()=>sidebar.classList.contains('open');
function openDrawer(){
  sidebar.classList.add('open');backdrop.hidden=false;menuBtn.setAttribute('aria-expanded','true');
  $('#newBtn').focus();
}
function closeDrawer(skipFocus){
  if(!sidebarOpen())return;
  sidebar.classList.remove('open');backdrop.hidden=true;menuBtn.setAttribute('aria-expanded','false');
  if(!skipFocus)menuBtn.focus();
}
menuBtn.onclick=()=>sidebarOpen()?closeDrawer():openDrawer();
backdrop.onclick=()=>closeDrawer();
sidebar.addEventListener('click',e=>{if(e.target.closest('.new,.side-link'))closeDrawer(true)});
if(mq&&mq.addEventListener)mq.addEventListener('change',e=>{if(!e.matches)closeDrawer(true)});

/* ---------- меню «⋯» и действия над разговором ---------- */
const topMenu=$('#topMenu');
document.addEventListener('click',e=>{if(topMenu.open&&!topMenu.contains(e.target))topMenu.open=false});
// «Новый чат» сбрасывает контекст на сервере; память и имя сохраняются.
$('#newBtn').onclick=async()=>{
  if(busy)controller?.abort();
  try{await fetch('/new',{method:'POST',headers:HEADERS})}catch(e){}
  showEmpty();input.focus();
};
$('#clearBtn').onclick=async()=>{
  topMenu.open=false;
  const yes=await askConfirm({
    title:'Стереть историю и память?',
    text:'Будут удалены вся история чата, ваше имя и все заметки из долговременной памяти. Журнал вычислений и ваши калькуляторы не затрагиваются. Это нельзя отменить.',
    ok:'Стереть'});
  if(!yes)return;
  if(busy)controller?.abort();
  try{
    const r=await fetch('/clear',{method:'POST',headers:HEADERS});
    if(!r.ok)throw new Error('server');
    showEmpty();status();toast('История и память стёрты.');
  }catch(e){toast('Не удалось стереть историю. Подробности в myai.log.','error')}
};
async function reloadModel(){
  topMenu.open=false;
  try{
    const d=await (await fetch('/reload_model',{method:'POST',headers:HEADERS})).json();
    if(d.status==='started')toast('Модель перезагружается…');else toast('Модель сейчас занята. Попробуйте позже.','error');
  }catch(e){toast('Не удалось запустить перезагрузку модели.','error')}
  status();
}
$('#reloadBtn').onclick=reloadModel;
$('#reloadMenuBtn').onclick=reloadModel;

/* ---------- клавиатура ---------- */
document.addEventListener('keydown',e=>{
  if(e.key==='Tab'){
    if(!$('#dialogOverlay').hidden)trapTab(e,$('#dialogOverlay .modal'));
    else if(!$('#capsOverlay').hidden)trapTab(e,$('#capsOverlay .modal'));
    else if(sidebarOpen())trapTab(e,sidebar);
    return;
  }
  if(e.key!=='Escape')return;
  if(!$('#dialogOverlay').hidden){closeDialog(false);e.stopImmediatePropagation();return}   // окно под диалогом остаётся открытым
  if(document.querySelector('.calc-overlay.open'))return;    // панель вычислений закрывает себя сама
  if(!$('#memoryOverlay').hidden)return;                      // окно «Память» тоже
  if(!$('#capsOverlay').hidden){closeCaps();return}
  if(sidebarOpen()){closeDrawer();return}
  if(topMenu.open){topMenu.open=false;return}
  if(busy&&controller)controller.abort();
});

/* ---------- история и состояние ---------- */
async function loadHistory(){
  try{
    const d=await (await fetch('/history')).json();
    if(!d.history?.length){showEmpty();return}
    chat.innerHTML='';let prevUser='';
    d.history.forEach(x=>{
      if(x.role==='assistant'){const row=addMessage(x.content,'bot');setActions(row,x.content,prevUser)}
      else{addMessage(x.content,'user');prevUser=x.content}
    });
    scrollDown(true);
  }catch(e){showEmpty()}
}
async function status(){
  try{
    const d=await (await fetch('/status')).json();
    $('#ver').textContent='MyAI v'+d.version;
    const st={idle:'ожидает',loading:'загружается…',ready:'готова',error:'ошибка загрузки'}[d.model_state]||d.model_state;
    $('#modelInfo').textContent='Модель: '+st+(d.loaded_model&&d.model_state==='ready'?' ('+d.loaded_model+')':'');
    const g=d.gpu||{};
    $('#gpuInfo').textContent='GPU: '+(g.available?(g.name+' · '+g.vram_gb+' GB'):'не обнаружена (CPU)');
    $('#memInfo').textContent=`Память: ${d.memory_stats?.notes||0} заметок · ${d.memory_stats?.turns||0} реплик`;
    $('#dot').style.background=d.model_state==='ready'?'#62d69b':(d.model_state==='error'?'#e55':'#e5b95b');
    $('#reloadBtn').hidden=d.model_state!=='error';
    if(!busy)$('#sys').textContent=d.model_state==='error'?'Модель не загружена':(d.model_state==='loading'?'Загрузка модели…':'Система готова');
    $('#sys').title=d.model_error||'';
  }catch(e){$('#sys').textContent='Сервер недоступен';$('#dot').style.background='#e55'}
}
$('#hint').textContent=HINT_DEFAULT;
status();setInterval(()=>{if(!document.hidden)status()},5000);
loadHistory();
if(!(mq&&mq.matches))input.focus();      // на телефоне не открываем клавиатуру сама
