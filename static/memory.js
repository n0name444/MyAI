/* ===== Окно «Память»: заметки, имя, добавление и точечное удаление ===== */
const memoryOverlay=$('#memoryOverlay');
let memoryLastFocus=null;

async function memApi(url,body){
  const options=body===undefined?{}:{method:'POST',headers:HEADERS,body:JSON.stringify(body)};
  const response=await fetch(url,options);
  let data={};
  try{data=await response.json()}catch(e){}
  if(!response.ok)throw new Error(data.error||'Ошибка запроса ('+response.status+')');
  return data;
}
function memDate(value){
  try{const d=new Date(String(value).replace(' ','T')+'Z');return isNaN(d)?'':d.toLocaleDateString('ru-RU')}catch(e){return ''}
}
function memError(message=''){const el=$('#memoryError');el.hidden=!message;el.textContent=message}
const memShort=text=>text.length>60?text.slice(0,57)+'…':text;

function renderMemory(d){
  const notes=d.notes||[];
  const rows=[];
  if(d.name)rows.push(`<li class="mem-item mem-name"><span class="mem-text">Вас зовут <b>${esc(d.name)}</b></span><button type="button" data-forget-name aria-label="Забыть имя ${esc(d.name)}">Забыть имя</button></li>`);
  notes.forEach(n=>rows.push(`<li class="mem-item"><span class="mem-text">${esc(n.text)}</span><span class="mem-date">${esc(memDate(n.created_at))}</span><button type="button" data-forget="${Number(n.id)}" aria-label="Забыть заметку: ${esc(memShort(n.text))}">Забыть</button></li>`));
  $('#memoryList').innerHTML=rows.join('');
  $('#memoryEmpty').hidden=rows.length>0;
  const shown=notes.length+(d.name?1:0);
  $('#memoryCount').textContent=d.total>d.limit?`Показаны последние ${d.limit} из ${d.total} заметок.`:(rows.length?`Заметок: ${d.total}`:'');
  return shown;
}
async function loadMemory(){
  try{memError();renderMemory(await memApi('/memory'))}
  catch(e){memError(e.message)}
}
function openMemory(){
  memoryLastFocus=document.activeElement;
  memoryOverlay.hidden=false;loadMemory();$('#memoryInput').focus();
}
function closeMemory(){
  if(memoryOverlay.hidden)return;
  memoryOverlay.hidden=true;
  if(memoryLastFocus&&memoryLastFocus.focus)memoryLastFocus.focus();
}
async function addMemoryNote(){
  const field=$('#memoryInput'),text=field.value.trim();
  if(!text){memError('Введите текст заметки.');field.focus();return}
  try{
    memError();
    const d=await memApi('/memory/add',{text});
    if(d.added){field.value='';toast('Запомнил.')}
    else toast('Такая заметка уже есть.','error');
    await loadMemory();status();
  }catch(e){memError(e.message)}
}
$('#memoryBtn').onclick=openMemory;
$('#memoryClose').onclick=closeMemory;
$('#memoryAddBtn').onclick=addMemoryNote;
$('#memoryInput').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing){e.preventDefault();addMemoryNote()}});
memoryOverlay.addEventListener('click',e=>{if(e.target===memoryOverlay)closeMemory()});
$('#memoryList').addEventListener('click',async e=>{
  const note=e.target.closest('[data-forget]'),name=e.target.closest('[data-forget-name]');
  if(!note&&!name)return;
  try{
    memError();
    if(name){
      await memApi('/memory/forget_name',{});
      toast('Забыл ваше имя.');
    }else{
      const text=note.closest('.mem-item').querySelector('.mem-text').textContent;
      await memApi('/memory/forget',{id:Number(note.dataset.forget)});
      toast('Забыл: «'+memShort(text)+'»');
    }
  }catch(err){
    // заметку могли удалить из чата командой «Забудь…»: просто обновляем список
    if(/уже нет/.test(err.message))toast(err.message,'error');else memError(err.message);
  }
  await loadMemory();status();
});
document.addEventListener('keydown',e=>{
  if(memoryOverlay.hidden)return;
  if(e.key==='Escape'){closeMemory();return}
  if(e.key==='Tab')trapTab(e,$('#memoryOverlay .modal'));
});
