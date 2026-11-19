/* ===== Панель «Вычисления»: Журнал / Калькуляторы / Обмен ===== */
const calcOverlay=$('#calcOverlay'),calcPanel=$('.calc-panel'),calcList=$('#calcList'),calcError=$('#calcError');
const FORMULA_NAME=/^[A-Za-z][A-Za-z0-9]{0,19}$/;      // имя переменной в формуле (требование сервера)
const SCENARIO_ID=/^[A-Za-z][A-Za-z0-9_]{0,29}$/;       // ключ параметра и id шага (требование сервера)
const CALC_TABS=['journal','calculators','exchange'];
let calcLastFocus=null,calcNoticeTimer=null;

/* --- общие помощники --- */
async function calcApi(url,body){
  const options=body===undefined?{}:{method:'POST',headers:HEADERS,body:JSON.stringify(body)};
  const response=await fetch(url,options);
  let data={};
  try{data=await response.json()}catch(e){}
  if(!response.ok)throw new Error(data.error||'Ошибка запроса ('+response.status+')');
  return data;
}
function calcNotify(text,kind='ok'){
  const el=$('#calcNotice');clearTimeout(calcNoticeTimer);
  if(!text){el.hidden=true;return}
  el.textContent=text;el.className='calc-notice'+(kind==='error'?' error':'');el.hidden=false;
  if(kind!=='error')calcNoticeTimer=setTimeout(()=>{el.hidden=true},6000);
}
function showLocalError(el,message=''){el.hidden=!message;el.textContent=message}
function localStamp(value){try{return new Date(value).toLocaleString('ru-RU')}catch(e){return value||''}}
function closeMenu(el){const menu=el&&el.closest?el.closest('.calc-menu'):null;if(menu)menu.open=false}

/* --- открытие, вкладки, клавиатура --- */
function openCalcPanel(tab){
  calcLastFocus=document.activeElement;
  calcOverlay.classList.add('open');
  calcShowTab(tab||'journal');
  calcPanel.focus();
}
function closeCalcPanel(){
  calcOverlay.classList.remove('open');
  if(calcLastFocus&&calcLastFocus.focus)calcLastFocus.focus();
}
function calcShowTab(name){
  if(!CALC_TABS.includes(name))name='journal';
  document.querySelectorAll('#calcTabs [role=tab]').forEach(b=>{
    const on=b.dataset.tab===name;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;
  });
  document.querySelectorAll('.calc-pane').forEach(p=>{p.hidden=p.dataset.pane!==name});
  calcNotify('');
  if(name==='journal'){loadCalcStats();loadCalcHistory()}
  else if(name==='calculators'){loadCalcFormulas();loadCalcScenarios();loadScenarioLibrary()}
  else loadScenarioCommunity();
}
$('#calcTabs').addEventListener('click',e=>{const b=e.target.closest('[data-tab]');if(b)calcShowTab(b.dataset.tab)});
$('#calcTabs').addEventListener('keydown',e=>{
  if(e.key!=='ArrowRight'&&e.key!=='ArrowLeft')return;
  const tabs=[...document.querySelectorAll('#calcTabs [role=tab]')],at=tabs.findIndex(b=>b.getAttribute('aria-selected')==='true');
  const next=tabs[(at+(e.key==='ArrowRight'?1:tabs.length-1))%tabs.length];
  calcShowTab(next.dataset.tab);next.focus();
});
$('#calcHistoryBtn').onclick=()=>openCalcPanel('journal');
$('#calcCloseBtn').onclick=closeCalcPanel;
calcOverlay.addEventListener('click',e=>{if(e.target===calcOverlay)closeCalcPanel()});
document.addEventListener('click',e=>{
  calcOverlay.querySelectorAll('.calc-menu[open]').forEach(m=>{if(!m.contains(e.target))m.open=false});
});
document.addEventListener('keydown',e=>{
  if(!calcOverlay.classList.contains('open'))return;
  if(e.key==='Escape'){
    const menu=calcOverlay.querySelector('.calc-menu[open]');
    if(menu){menu.open=false;return}
    closeCalcPanel();return;
  }
  if(e.key!=='Tab')return;
  const items=[...calcPanel.querySelectorAll('button,a[href],input,select,textarea,summary')]
    .filter(x=>!x.disabled&&!x.hidden&&x.tabIndex>=0&&x.offsetParent!==null);
  if(!items.length)return;
  const first=items[0],last=items[items.length-1];
  if(e.shiftKey&&(document.activeElement===first||document.activeElement===calcPanel)){e.preventDefault();last.focus()}
  else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}
});

/* =====================  ЖУРНАЛ  ===================== */
function calcFilterParams(){
  const p=new URLSearchParams();
  const q=$('#calcQuery').value.trim(),from=$('#calcDateFrom').value,to=$('#calcDateTo').value;
  if(q)p.set('q',q);if(from)p.set('date_from',from);if(to)p.set('date_to',to);
  return p;
}
async function loadCalcStats(){
  const days=Number($('#calcStatsDays').value||14);
  try{
    const d=await calcApi('/calculator/stats?days='+days);
    $('#calcStatTotal').textContent=d.total;$('#calcStatActive').textContent=d.active_days;
    $('#calcStatTop').textContent=d.top_operation||'—';
    $('#calcStatLine').textContent=d.total?`Всего ${d.total} · активных дней ${d.active_days} · чаще всего: ${d.top_operation||'—'}`:'Журнал пока пуст';
    const max=Math.max(1,...d.daily.map(x=>x.count));
    $('#calcDailyChart').innerHTML=d.daily.map(x=>{
      const h=x.count?Math.max(5,Math.round(x.count/max*100)):2;
      return `<div class="calc-bar-wrap" title="${esc(x.date)}: ${x.count}"><div class="calc-bar" style="height:${h}%"></div><span class="calc-bar-label">${esc(x.date.slice(8))}</span></div>`;
    }).join('');
    $('#calcOperationStats').innerHTML=d.operations.length?d.operations.map(x=>`<span class="calc-op">${esc(x.name)} <b>${x.count}</b></span>`).join(''):'<span class="calc-hint">Пока нет данных для разбивки операций</span>';
  }catch(e){
    $('#calcStatLine').textContent='Статистика недоступна';
    $('#calcStatTotal').textContent='—';$('#calcStatActive').textContent='—';$('#calcStatTop').textContent='—';
    $('#calcDailyChart').textContent='';$('#calcOperationStats').textContent=e.message;
  }
}
$('#calcStatsDays').addEventListener('change',loadCalcStats);

async function loadCalcHistory(){
  showLocalError(calcError);
  const params=calcFilterParams(),suffix=params.toString()?'?'+params.toString():'';
  $('#calcExportBtn').href='/calculator/export.csv'+suffix;
  $('#calcResetBtn').hidden=!suffix;
  try{
    const d=await calcApi('/calculator/search'+suffix);
    $('#calcCount').textContent=suffix?`Найдено записей: ${d.count}`:`Записей: ${d.count}`;
    if(!d.history?.length){
      calcList.innerHTML=`<div class="calc-empty">${suffix?'По заданным условиям ничего не найдено.':'Журнал пуст. Посчитайте что-нибудь в чате или на вкладке «Калькуляторы».'}</div>`;
      return;
    }
    calcList.innerHTML=d.history.map(x=>`<article class="calc-item"><div class="calc-item-main"><div class="calc-expression">${esc(x.expression)}</div><div class="calc-answer">${esc(x.answer)}</div><div class="calc-item-meta">#${esc(x.id)} · ${esc(localStamp(x.created_at))}</div></div><button type="button" data-replay="${esc(x.id)}">↻ Повторить</button></article>`).join('');
  }catch(e){
    calcList.innerHTML='<div class="calc-empty">Не удалось загрузить журнал.</div>';
    showLocalError(calcError,e.message);
  }
}
let calcQueryTimer=null;
$('#calcQuery').addEventListener('input',()=>{clearTimeout(calcQueryTimer);calcQueryTimer=setTimeout(loadCalcHistory,300)});
$('#calcQuery').addEventListener('keydown',e=>{if(e.key==='Enter'){clearTimeout(calcQueryTimer);loadCalcHistory()}});
$('#calcDateFrom').addEventListener('change',loadCalcHistory);
$('#calcDateTo').addEventListener('change',loadCalcHistory);
$('#calcResetBtn').onclick=()=>{$('#calcQuery').value='';$('#calcDateFrom').value='';$('#calcDateTo').value='';loadCalcHistory()};
calcList.addEventListener('click',async e=>{
  const btn=e.target.closest('[data-replay]');if(!btn)return;
  btn.disabled=true;btn.textContent='Считаю…';showLocalError(calcError);
  try{
    const d=await calcApi('/calculator/replay',{id:Number(btn.dataset.replay)});
    closeCalcPanel();
    addMessage(`Повтор расчёта #${d.id}:\n${d.response}`,'bot',{route:'CALCULATOR'});
  }catch(err){showLocalError(calcError,err.message);btn.disabled=false;btn.textContent='↻ Повторить'}
});
$('#calcClearBtn').onclick=async e=>{
  closeMenu(e.target);
  if(!confirm('Удалить все записи истории вычислений? История чата и память MyAI останутся без изменений.'))return;
  try{await calcApi('/calculator/clear',{});calcNotify('Журнал очищен.');await loadCalcHistory();await loadCalcStats()}
  catch(err){calcNotify(err.message,'error')}
};

/* =====================  БЫСТРАЯ ФОРМУЛА  ===================== */
const formulaExpression=$('#formulaExpression'),formulaName=$('#formulaName');
let calcSavedFormulas=[],calcFormulaLabels={};
// Имена переменных в формуле. Числа вида 2e5 и дробные части не считаются именами.
function formulaIdents(expression){
  const out=[];
  for(const m of String(expression||'').matchAll(/(?<![0-9A-Za-z_.])[A-Za-z_][A-Za-z0-9_]*/g))if(!out.includes(m[0]))out.push(m[0]);
  return out;
}
function renderFormulaFields(){
  const box=$('#formulaFields'),old={};
  box.querySelectorAll('[data-formula-var]').forEach(el=>{old[el.dataset.formulaVar]=el.value});
  const names=formulaIdents(formulaExpression.value);
  box.innerHTML=names.map(n=>{
    const label=calcFormulaLabels[n];
    return `<label>${esc(label||n)}${label?` <code>${esc(n)}</code>`:''}<input type="number" step="any" data-formula-var="${esc(n)}" placeholder="0"></label>`;
  }).join('');
  box.querySelectorAll('[data-formula-var]').forEach(el=>{if(old[el.dataset.formulaVar]!==undefined)el.value=old[el.dataset.formulaVar]});
  box.hidden=!names.length;
}
function formulaValues(requireAll){
  const names=formulaIdents(formulaExpression.value);
  if(!names.length)throw new Error('В формуле нет переменных. Для простых выражений вроде 2 + 2 напишите их в чате.');
  const bad=names.find(n=>!FORMULA_NAME.test(n));
  if(bad)throw new Error(`Имя «${bad}» нельзя использовать: только латинские буквы и цифры, до 20 знаков.`);
  const values={},missing=[];
  $('#formulaFields').querySelectorAll('[data-formula-var]').forEach(el=>{
    if(el.value.trim()===''){missing.push(el.dataset.formulaVar);values[el.dataset.formulaVar]=0}
    else values[el.dataset.formulaVar]=el.value;
  });
  if(requireAll&&missing.length)throw new Error('Заполните значения: '+missing.join(', '));
  return values;
}
function useFormula(name,expression,labels){
  formulaName.value=name||'';formulaExpression.value=expression;calcFormulaLabels=labels||{};
  renderFormulaFields();showLocalError($('#formulaError'));$('#formulaResult').textContent='';
  const first=$('#formulaFields').querySelector('input');if(first)first.focus();
}
async function loadCalcFormulas(){
  try{
    const d=await calcApi('/calculator/formulas');
    calcSavedFormulas=d.formulas||[];
    const templates=d.templates||[];
    $('#calcTemplates').innerHTML=templates.length?'<span class="calc-hint">Готовые формулы:</span>'+templates.map(t=>`<button type="button" data-template="${esc(t.id)}">${esc(t.name)}</button>`).join(''):'';
    $('#calcTemplates')._templates=templates;
    $('#calcSavedFormulas').innerHTML=calcSavedFormulas.length?'<span class="calc-hint">Сохранённые:</span>'+calcSavedFormulas.map(f=>`<span class="calc-chip-item"><button type="button" data-formula="${Number(f.id)}">${esc(f.name)}</button><button type="button" class="delete" data-delete-formula="${Number(f.id)}" aria-label="Удалить формулу ${esc(f.name)}">×</button></span>`).join(''):'';
  }catch(e){showLocalError($('#formulaError'),e.message)}
}
formulaExpression.addEventListener('input',()=>{calcFormulaLabels={};renderFormulaFields()});
formulaExpression.addEventListener('keydown',e=>{if(e.key==='Enter')$('#formulaRunBtn').click()});
$('#formulaFields').addEventListener('keydown',e=>{if(e.key==='Enter')$('#formulaRunBtn').click()});
$('#calcTemplates').addEventListener('click',e=>{
  const b=e.target.closest('[data-template]');if(!b)return;
  const t=($('#calcTemplates')._templates||[]).find(x=>x.id===b.dataset.template);
  if(t)useFormula(t.name,t.expression,t.variables);
});
$('#calcSavedFormulas').addEventListener('click',async e=>{
  const del=e.target.closest('[data-delete-formula]');
  if(del){
    if(!confirm('Удалить сохранённую формулу?'))return;
    try{await calcApi('/calculator/formulas/delete',{id:Number(del.dataset.deleteFormula)});await loadCalcFormulas()}
    catch(err){showLocalError($('#formulaError'),err.message)}
    return;
  }
  const b=e.target.closest('[data-formula]');if(!b)return;
  const f=calcSavedFormulas.find(x=>x.id===Number(b.dataset.formula));
  if(f)useFormula(f.name,f.expression,{});
});
$('#formulaRunBtn').onclick=async()=>{
  showLocalError($('#formulaError'));$('#formulaResult').textContent='';
  try{
    const variables=formulaValues(true);
    const d=await calcApi('/calculator/formulas/evaluate',{expression:formulaExpression.value,variables});
    $('#formulaResult').textContent='Результат: '+d.result;
    loadCalcHistory();loadCalcStats();
  }catch(e){showLocalError($('#formulaError'),e.message)}
};
$('#formulaSaveBtn').onclick=async()=>{
  showLocalError($('#formulaError'));
  try{
    if(!formulaName.value.trim())throw new Error('Введите название, чтобы сохранить формулу.');
    const variables=formulaValues(false);
    await calcApi('/calculator/formulas/save',{name:formulaName.value,expression:formulaExpression.value,variables});
    calcNotify('Формула «'+formulaName.value.trim()+'» сохранена.');
    await loadCalcFormulas();
  }catch(e){showLocalError($('#formulaError'),e.message)}
};

/* =====================  КАЛЬКУЛЯТОРЫ (карточки)  ===================== */
let calcScenarios=[];
async function loadCalcScenarios(){
  try{const d=await calcApi('/calculator/scenarios');calcScenarios=d.scenarios||[];renderScenarioCards()}
  catch(e){calcNotify(e.message,'error')}
}
function cardHtml(sc){
  const fields=Object.entries(sc.inputs||{}).map(([key,label])=>`<label>${esc(label)}<input type="number" step="any" data-run-input="${esc(key)}" placeholder="0" required></label>`).join('');
  return `<article class="calc-card" data-scenario-id="${Number(sc.id)}">
    <div class="calc-card-head"><div class="calc-card-title"><h3>${esc(sc.name)}</h3>${sc.description?`<p class="calc-card-desc">${esc(sc.description)}</p>`:''}</div>
      <span class="calc-tag">${esc(sc.category||'Пользовательские')}</span>
      <details class="calc-menu"><summary aria-label="Действия: ${esc(sc.name)}">⋯</summary><div class="calc-menu-list">
        <button type="button" data-act="edit">Изменить</button><button type="button" data-act="copy">Копия</button><button type="button" data-act="versions">Версии (сейчас v${Number(sc.version||1)})</button><button type="button" data-act="publish">Опубликовать в каталоге</button><button type="button" class="danger" data-act="delete">Удалить…</button>
      </div></details></div>
    <form class="calc-run" data-run-form><div class="calc-run-fields">${fields}</div><button class="primary" type="submit">Посчитать</button></form>
    <div class="calc-error" data-card-error hidden></div><div data-card-result aria-live="polite"></div><div class="calc-versions" data-card-versions hidden></div>
  </article>`;
}
function renderScenarioCards(){
  $('#calcSavedScenarios').innerHTML=calcScenarios.length?calcScenarios.map(cardHtml).join(''):'<div class="calc-empty">Пока нет ни одного калькулятора. Добавьте готовый из шаблонов или создайте свой.</div>';
}
async function runScenarioCard(card){
  const id=Number(card.dataset.scenarioId),err=card.querySelector('[data-card-error]'),out=card.querySelector('[data-card-result]');
  showLocalError(err);
  const values={};
  card.querySelectorAll('[data-run-input]').forEach(el=>{values[el.dataset.runInput]=el.value});
  try{
    const d=await calcApi('/calculator/scenarios/run',{id,values});
    out.innerHTML='<ul class="calc-steps">'+d.results.map(x=>`<li><span>${esc(x.name)}</span><b>${esc(x.result)}</b></li>`).join('')+`</ul><div class="calc-final">Итог: <b>${esc(d.final_result)}</b></div>`;
    loadCalcHistory();loadCalcStats();
  }catch(e){out.innerHTML='';showLocalError(err,e.message)}
}
async function toggleVersions(card){
  const box=card.querySelector('[data-card-versions]');
  if(!box.hidden){box.hidden=true;return}
  const d=await calcApi('/calculator/scenarios/'+Number(card.dataset.scenarioId)+'/versions');
  const old=(d.versions||[]).filter(v=>!v.current);
  box.innerHTML=old.length?'<strong>Предыдущие версии</strong>'+old.map(v=>`<div class="calc-version"><span>v${Number(v.version)} · ${esc(v.name)} · ${esc(localStamp(v.saved_at))}</span><button type="button" data-restore-version="${Number(v.version)}">Восстановить</button></div>`).join(''):'<span class="calc-hint">Предыдущих версий пока нет: они появляются после правок.</span>';
  box.hidden=false;
}
$('#calcSavedScenarios').addEventListener('submit',e=>{
  const form=e.target.closest('[data-run-form]');if(!form)return;
  e.preventDefault();runScenarioCard(form.closest('.calc-card'));
});
$('#calcSavedScenarios').addEventListener('click',async e=>{
  const card=e.target.closest('.calc-card');if(!card)return;
  const id=Number(card.dataset.scenarioId);
  const restore=e.target.closest('[data-restore-version]');
  const act=e.target.closest('[data-act]');
  try{
    if(restore){
      const version=Number(restore.dataset.restoreVersion);
      if(!confirm(`Восстановить v${version}? Текущая версия сохранится в истории.`))return;
      const d=await calcApi('/calculator/scenarios/restore',{id,version});
      calcNotify('Версия восстановлена как новая v'+d.version+'.');await loadCalcScenarios();return;
    }
    if(!act)return;
    closeMenu(act);
    const sc=calcScenarios.find(x=>x.id===id);
    switch(act.dataset.act){
      case 'edit':if(sc)openScenarioEditor(sc);break;
      case 'copy':await calcApi('/calculator/scenarios/copy',{id});calcNotify('Создана копия калькулятора.');await loadCalcScenarios();break;
      case 'versions':await toggleVersions(card);break;
      case 'publish':await calcApi('/calculator/scenarios/publish',{id});calcNotify('Опубликовано в локальном каталоге (вкладка «Обмен»).');break;
      case 'delete':
        if(!confirm('Удалить калькулятор «'+(sc?sc.name:'')+'» вместе с историей версий?'))return;
        await calcApi('/calculator/scenarios/delete',{id});calcNotify('Калькулятор удалён.');await loadCalcScenarios();break;
    }
  }catch(err){calcNotify(err.message,'error')}
});

/* --- шаблоны из библиотеки --- */
let calcLibrary=[];
async function loadScenarioLibrary(){
  try{const d=await calcApi('/calculator/scenarios/library');calcLibrary=d.templates||[];renderScenarioLibrary()}
  catch(e){calcNotify(e.message,'error')}
}
function renderScenarioLibrary(){
  const category=$('#scenarioLibraryCategory').value;
  const rows=calcLibrary.map((x,i)=>[x,i]).filter(([x])=>!category||x.category===category);
  $('#scenarioLibrary').innerHTML=rows.map(([x,i])=>`<div class="calc-lib-item"><div><strong>${esc(x.name)}</strong> <span class="calc-tag">${esc(x.category)}</span>${x.description?`<p class="calc-card-desc">${esc(x.description)}</p>`:''}</div><button type="button" data-library-index="${i}">Добавить</button></div>`).join('')||'<span class="calc-hint">В этой категории пока нет шаблонов.</span>';
}
$('#scenarioLibraryCategory').onchange=renderScenarioLibrary;
$('#scenarioLibrary').addEventListener('click',async e=>{
  const b=e.target.closest('[data-library-index]');if(!b)return;
  const x=calcLibrary[Number(b.dataset.libraryIndex)];if(!x)return;
  try{
    await calcApi('/calculator/scenarios/save',x);
    calcNotify('«'+x.name+'» добавлен в ваши калькуляторы.');
    await loadCalcScenarios();
  }catch(err){
    calcNotify(/уже существует/.test(err.message)?'«'+x.name+'» уже есть в ваших калькуляторах.':err.message,'error');
  }
});

/* =====================  КОНСТРУКТОР  ===================== */
let calcEditingId=null,calcEditorDirty=false;
const RU_TO_LAT={а:'a',б:'b',в:'v',г:'g',д:'d',е:'e',ё:'e',ж:'zh',з:'z',и:'i',й:'y',к:'k',л:'l',м:'m',н:'n',о:'o',п:'p',р:'r',с:'s',т:'t',у:'u',ф:'f',х:'h',ц:'ts',ч:'ch',ш:'sh',щ:'sch',ъ:'',ы:'y',ь:'',э:'e',ю:'yu',я:'ya'};
// «Цена за единицу» → tsena. Берём первое слово: короткие имена удобнее писать в формулах.
function calcSlug(text,fallback){
  const word=String(text||'').trim().toLowerCase().split(/[^a-zа-яё0-9]+/i).find(Boolean)||'';
  let s=[...word].map(c=>RU_TO_LAT[c]!==undefined?RU_TO_LAT[c]:c).join('').replace(/[^a-z0-9]+/g,'');
  if(!s)s=fallback;
  if(!/^[a-z]/.test(s))s=fallback+s;
  return s.slice(0,20);
}
function calcUniqueName(base,taken){
  let name=base,i=2;
  while(taken.has(name)){const suffix=String(i++);name=base.slice(0,20-suffix.length)+suffix}
  return name;
}
const inputRows=()=>[...$('#scenarioInputRows').querySelectorAll('.input-row')];
const stepRows=()=>[...$('#scenarioStepRows').querySelectorAll('.step-row')];
const fieldVal=(row,key)=>row.querySelector(`[data-field="${key}"]`).value.trim();
function takenNames(exceptRow){
  const taken=new Set();
  inputRows().forEach(r=>{if(r!==exceptRow)taken.add(fieldVal(r,'key'))});
  stepRows().forEach(r=>{if(r!==exceptRow)taken.add(fieldVal(r,'id'))});
  return taken;
}
function markDirty(){calcEditorDirty=true}
// Если имя параметра или шага меняется, привязки в формулах следуют за ним.
function calcRenameRef(kind,from,to){
  if(!from||!to||from===to)return;
  stepRows().forEach(r=>Object.values(r._binds||{}).forEach(b=>{if(b.kind===kind&&b.ref===from)b.ref=to}));
}
function calcScope(row){
  const inputs=inputRows().map(r=>({key:fieldVal(r,'key'),label:fieldVal(r,'label')})).filter(x=>SCENARIO_ID.test(x.key));
  const steps=[];
  for(const r of stepRows()){
    if(r===row)break;
    const id=fieldVal(r,'id');
    if(SCENARIO_ID.test(id))steps.push({id,name:fieldVal(r,'name')||id});
  }
  return {inputs,steps};
}
function calcResolve(scope,name,existing){
  const ok=b=>b&&(b.kind==='const'||(b.kind==='input'&&scope.inputs.some(x=>x.key===b.ref))||(b.kind==='step'&&scope.steps.some(x=>x.id===b.ref)));
  if(ok(existing))return existing;
  if(scope.inputs.some(x=>x.key===name))return {kind:'input',ref:name};
  if(scope.steps.some(x=>x.id===name))return {kind:'step',ref:name};
  return {kind:'',ref:''};
}
function bindHtml(name,b,scope){
  const sel=(kind,ref)=>b.kind===kind&&b.ref===ref?' selected':'';
  const options=[`<option value=""${b.kind===''?' selected':''}>— выберите —</option>`];
  if(scope.inputs.length)options.push('<optgroup label="Параметры">'+scope.inputs.map(x=>`<option value="input:${esc(x.key)}"${sel('input',x.key)}>${esc(x.label||x.key)}</option>`).join('')+'</optgroup>');
  if(scope.steps.length)options.push('<optgroup label="Результаты шагов">'+scope.steps.map(x=>`<option value="step:${esc(x.id)}"${sel('step',x.id)}>${esc(x.name)}</option>`).join('')+'</optgroup>');
  options.push(`<option value="const"${b.kind==='const'?' selected':''}>Число…</option>`);
  const bad=!FORMULA_NAME.test(name);
  return `<span class="bind${b.kind===''||bad?' unresolved':''}"><code>${esc(name)}</code> →
    <select data-bind-var="${esc(name)}" aria-label="Откуда взять ${esc(name)}">${options.join('')}</select>
    ${b.kind==='const'?`<input type="number" step="any" data-bind-const="${esc(name)}" value="${esc(b.value??'')}" aria-label="Число для ${esc(name)}">`:''}
    ${bad?'<span class="calc-bad">это имя нельзя использовать в формуле</span>':''}</span>`;
}
function refreshStepBinds(row){
  const binds=row._binds||(row._binds={});
  const names=formulaIdents(fieldVal(row,'expression'));
  Object.keys(binds).forEach(k=>{if(!names.includes(k))delete binds[k]});
  const scope=calcScope(row);
  names.forEach(n=>{binds[n]=calcResolve(scope,n,binds[n])});
  row.querySelector('.step-binds').innerHTML=names.length?'<span>Откуда брать значения:</span>'+names.map(n=>bindHtml(n,binds[n],scope)).join(''):'';
}
let calcRefreshTimer=null;
function refreshAllBinds(){clearTimeout(calcRefreshTimer);calcRefreshTimer=setTimeout(()=>stepRows().forEach(refreshStepBinds),0)}
function renumberSteps(){stepRows().forEach((r,i)=>{r.querySelector('.calc-rowhead span').textContent='Шаг '+(i+1)})}

function addInputRow(data={}){
  if(inputRows().length>=20)throw new Error('Можно добавить не более 20 параметров.');
  const row=document.createElement('div');row.className='scenario-row input-row';
  row.innerHTML='<input data-field="label" maxlength="80" placeholder="Например: Цена за единицу" aria-label="Подпись параметра"><input data-field="key" maxlength="30" placeholder="price" aria-label="Имя параметра в формулах" autocomplete="off"><button type="button" class="remove" aria-label="Удалить параметр">✕</button>';
  const label=row.querySelector('[data-field=label]'),key=row.querySelector('[data-field=key]');
  label.value=data.label||'';key.value=data.key||'';row._last=key.value;
  if(data.key)row.dataset.manualKey='1';
  label.addEventListener('input',()=>{
    if(!row.dataset.manualKey){
      const next=calcUniqueName(calcSlug(label.value,'param'),takenNames(row));
      calcRenameRef('input',row._last,next);key.value=next;row._last=next;
    }
    markDirty();refreshAllBinds();
  });
  key.addEventListener('input',()=>{
    row.dataset.manualKey='1';
    const next=key.value.trim();calcRenameRef('input',row._last,next);if(next)row._last=next;
    markDirty();refreshAllBinds();
  });
  row.querySelector('.remove').addEventListener('click',()=>{row.remove();markDirty();refreshAllBinds()});
  $('#scenarioInputRows').append(row);
  return row;
}
function addStepRow(data={}){
  if(stepRows().length>=20)throw new Error('Можно добавить не более 20 шагов.');
  const row=document.createElement('div');row.className='scenario-row step-row';
  row.innerHTML='<div class="calc-rowhead"><span></span><button type="button" class="remove" aria-label="Удалить шаг">Удалить</button></div><input data-field="name" maxlength="80" placeholder="Название шага, например: Стоимость" aria-label="Название шага"><input data-field="id" maxlength="30" placeholder="stoimost" aria-label="Имя результата для следующих шагов" autocomplete="off"><input data-field="expression" maxlength="200" placeholder="Формула, например: price * quantity" aria-label="Формула шага" autocomplete="off"><div class="step-binds" aria-live="polite"></div>';
  const name=row.querySelector('[data-field=name]'),id=row.querySelector('[data-field=id]'),expr=row.querySelector('[data-field=expression]');
  name.value=data.name||'';id.value=data.id||'';expr.value=data.expression||'';row._last=id.value;row._binds=data.binds||{};
  if(data.id)row.dataset.manualId='1';
  name.addEventListener('input',()=>{
    if(!row.dataset.manualId){
      const next=calcUniqueName(calcSlug(name.value,'step'),takenNames(row));
      calcRenameRef('step',row._last,next);id.value=next;row._last=next;
    }
    markDirty();refreshAllBinds();
  });
  id.addEventListener('input',()=>{
    row.dataset.manualId='1';
    const next=id.value.trim();calcRenameRef('step',row._last,next);if(next)row._last=next;
    markDirty();refreshAllBinds();
  });
  expr.addEventListener('input',()=>{markDirty();refreshStepBinds(row)});
  row.querySelector('.step-binds').addEventListener('change',e=>{
    const select=e.target.closest('[data-bind-var]');if(!select)return;
    const v=select.dataset.bindVar,value=select.value,prev=row._binds[v]||{};
    if(value==='')row._binds[v]={kind:'',ref:''};
    else if(value==='const')row._binds[v]={kind:'const',ref:'',value:prev.value??''};
    else{const at=value.indexOf(':');row._binds[v]={kind:value.slice(0,at),ref:value.slice(at+1)}}
    markDirty();refreshStepBinds(row);
    const again=row.querySelector(`[data-bind-var="${v}"]`);if(again)again.focus();
  });
  row.querySelector('.step-binds').addEventListener('input',e=>{
    const field=e.target.closest('[data-bind-const]');if(!field)return;
    const b=row._binds[field.dataset.bindConst];if(b){b.value=field.value;markDirty()}
  });
  row.querySelector('.remove').addEventListener('click',()=>{row.remove();renumberSteps();markDirty();refreshAllBinds()});
  $('#scenarioStepRows').append(row);
  renumberSteps();refreshStepBinds(row);
  return row;
}
function buildScenarioPayload(){
  stepRows().forEach(refreshStepBinds);
  const inputs={};
  inputRows().forEach((row,i)=>{
    const key=fieldVal(row,'key'),label=fieldVal(row,'label');
    if(!label)throw new Error(`Параметр ${i+1}: укажите подпись.`);
    if(!SCENARIO_ID.test(key))throw new Error(`Параметр «${label}»: имя — латинские буквы и цифры, начинается с буквы.`);
    if(Object.hasOwn(inputs,key))throw new Error(`Имя «${key}» указано дважды. Сделайте имена разными.`);
    inputs[key]=label;
  });
  const ids=new Set();
  const steps=stepRows().map((row,i)=>{
    const name=fieldVal(row,'name'),id=fieldVal(row,'id'),expression=fieldVal(row,'expression');
    if(!name)throw new Error(`Шаг ${i+1}: укажите название.`);
    if(!expression)throw new Error(`Шаг «${name}»: введите формулу.`);
    if(!SCENARIO_ID.test(id))throw new Error(`Шаг «${name}»: имя результата — латинские буквы и цифры, начинается с буквы.`);
    if(ids.has(id)||Object.hasOwn(inputs,id))throw new Error(`Имя «${id}» уже занято. Сделайте имена параметров и результатов разными.`);
    ids.add(id);
    const variables={};
    for(const v of formulaIdents(expression)){
      if(!FORMULA_NAME.test(v))throw new Error(`Шаг «${name}»: имя «${v}» нельзя использовать в формуле (латинские буквы и цифры, до 20 знаков).`);
      const b=row._binds[v];
      if(!b||!b.kind)throw new Error(`Шаг «${name}»: выберите, откуда взять значение «${v}».`);
      if(b.kind==='const'){
        const n=Number(b.value);
        if(String(b.value??'').trim()===''||!Number.isFinite(n))throw new Error(`Шаг «${name}»: введите число для «${v}».`);
        variables[v]=n;
      }else variables[v]=b.kind+'.'+b.ref;
    }
    if(!Object.keys(variables).length)throw new Error(`Шаг «${name}»: в формуле нужна хотя бы одна переменная.`);
    return {id,name,expression,variables};
  });
  if(!Object.keys(inputs).length)throw new Error('Добавьте хотя бы один параметр.');
  if(!steps.length)throw new Error('Добавьте хотя бы один шаг.');
  return {name:$('#scenarioName').value,description:$('#scenarioDescription').value,category:$('#scenarioCategory').value,inputs,steps};
}
function showCalcEditor(editing){$('#calcEditView').hidden=!editing;$('#calcMainView').hidden=editing}
function resetScenarioEditor(){
  calcEditingId=null;calcEditorDirty=false;
  $('#scenarioName').value='';$('#scenarioDescription').value='';$('#scenarioCategory').value='Пользовательские';
  $('#scenarioInputRows').innerHTML='';$('#scenarioStepRows').innerHTML='';showLocalError($('#scenarioError'));
}
function openScenarioEditor(sc){
  resetScenarioEditor();
  if(sc){
    calcEditingId=sc.id;
    $('#calcEditTitle').textContent='Изменить калькулятор · v'+Number(sc.version||1);
    $('#scenarioName').value=sc.name;$('#scenarioDescription').value=sc.description||'';$('#scenarioCategory').value=sc.category||'Пользовательские';
    Object.entries(sc.inputs||{}).forEach(([key,label])=>addInputRow({key,label}));
    (sc.steps||[]).forEach(step=>{
      const binds={};
      Object.entries(step.variables||{}).forEach(([v,source])=>{
        if(typeof source==='number')binds[v]={kind:'const',ref:'',value:String(source)};
        else{const m=/^(input|step)\.(.+)$/.exec(String(source));binds[v]=m?{kind:m[1],ref:m[2]}:{kind:'',ref:''}}
      });
      addStepRow({id:step.id,name:step.name,expression:step.expression,binds});
    });
    setSaveLabel('Сохранить изменения');
  }else{
    $('#calcEditTitle').textContent='Новый калькулятор';
    addInputRow();addStepRow();setSaveLabel('Сохранить калькулятор');
  }
  stepRows().forEach(refreshStepBinds);
  calcEditorDirty=false;showCalcEditor(true);$('#scenarioName').focus();
}
function setSaveLabel(text){$('#scenarioSaveBtn').textContent=text}
function closeScenarioEditor(force){
  if(!force&&calcEditorDirty&&!confirm('Закрыть редактор без сохранения? Введённые изменения пропадут.'))return;
  resetScenarioEditor();showCalcEditor(false);
}
$('#calcNewBtn').onclick=()=>openScenarioEditor(null);
$('#scenarioBackBtn').onclick=()=>closeScenarioEditor(false);
$('#scenarioCancelBtn').onclick=()=>closeScenarioEditor(false);
$('#scenarioAddInput').onclick=()=>{showLocalError($('#scenarioError'));try{addInputRow().querySelector('input').focus();markDirty()}catch(e){showLocalError($('#scenarioError'),e.message)}};
$('#scenarioAddStep').onclick=()=>{showLocalError($('#scenarioError'));try{addStepRow().querySelector('input').focus();markDirty()}catch(e){showLocalError($('#scenarioError'),e.message)}};
['#scenarioName','#scenarioDescription','#scenarioCategory'].forEach(sel=>$(sel).addEventListener('input',markDirty));
$('#scenarioSaveBtn').onclick=async()=>{
  const err=$('#scenarioError');showLocalError(err);
  try{
    const payload=buildScenarioPayload(),editing=calcEditingId!==null;
    if(editing)payload.id=calcEditingId;
    const d=await calcApi(editing?'/calculator/scenarios/update':'/calculator/scenarios/save',payload);
    calcNotify(editing?`Изменения сохранены. Создана версия v${d.version}.`:'Калькулятор «'+payload.name.trim()+'» сохранён.');
    closeScenarioEditor(true);await loadCalcScenarios();
  }catch(e){showLocalError(err,e.message)}
};

/* =====================  ОБМЕН  ===================== */
async function loadScenarioCommunity(){
  try{
    const d=await calcApi('/calculator/scenarios/community?q='+encodeURIComponent($('#scenarioCommunitySearch').value.trim()));
    $('#scenarioCommunity').innerHTML=(d.publications||[]).map(x=>{
      const id=Number(x.id);
      return `<article class="calc-card calc-pub"><div class="calc-card-head"><div class="calc-card-title"><h3>${esc(x.scenario_name)}</h3>${x.description?`<p class="calc-card-desc">${esc(x.description)}</p>`:''}<p class="calc-pub-meta">★ ${Number(x.average_rating).toFixed(1)} (${Number(x.rating_count)}) · установок: ${Number(x.installs)}</p></div><span class="calc-tag">${esc(x.category)}</span></div>
        <div class="calc-row"><button type="button" class="primary" data-community-install="${id}">Установить</button><button type="button" data-rate-toggle>Оценить</button><span class="calc-row" data-rate-pick hidden>${[1,2,3,4,5].map(n=>`<button type="button" data-community-rate="${id}" data-rating="${n}" aria-label="Оценка ${n} из 5">${n} ★</button>`).join('')}</span><button type="button" class="danger" data-community-unpublish="${id}">Снять с публикации</button></div></article>`;
    }).join('')||'<div class="calc-empty">В каталоге пока нет публикаций. Опубликовать калькулятор можно из меню «⋯» на его карточке.</div>';
  }catch(e){calcNotify(e.message,'error')}
}
$('#scenarioCommunityRefresh').onclick=loadScenarioCommunity;
$('#scenarioCommunitySearch').addEventListener('keydown',e=>{if(e.key==='Enter')loadScenarioCommunity()});
$('#scenarioCommunity').addEventListener('click',async e=>{
  const install=e.target.closest('[data-community-install]'),rate=e.target.closest('[data-community-rate]'),unpub=e.target.closest('[data-community-unpublish]'),toggle=e.target.closest('[data-rate-toggle]');
  try{
    if(toggle){const pick=toggle.parentElement.querySelector('[data-rate-pick]');pick.hidden=!pick.hidden;return}
    if(install){
      await calcApi('/calculator/scenarios/install',{publication_id:Number(install.dataset.communityInstall)});
      calcNotify('Установлено: калькулятор появился на вкладке «Калькуляторы».');await loadScenarioCommunity();return;
    }
    if(rate){
      await calcApi('/calculator/scenarios/rate',{publication_id:Number(rate.dataset.communityRate),rating:Number(rate.dataset.rating)});
      calcNotify('Спасибо, оценка сохранена.');await loadScenarioCommunity();return;
    }
    if(unpub){
      if(!confirm('Снять сценарий с публикации? Установленные копии и ваш исходный калькулятор останутся.'))return;
      await calcApi('/calculator/scenarios/unpublish',{publication_id:Number(unpub.dataset.communityUnpublish)});
      calcNotify('Публикация снята.');await loadScenarioCommunity();
    }
  }catch(err){calcNotify(err.message,'error')}
});
$('#scenarioImportFile').addEventListener('change',async e=>{
  const file=e.target.files[0];if(!file)return;
  try{
    if(file.size>65536)throw new Error('Файл не должен превышать 64 КБ.');
    const d=await calcApi('/calculator/scenarios/import',JSON.parse(await file.text()));
    calcNotify(`Импортировано калькуляторов: ${d.imported}.`);
  }catch(err){calcNotify(err instanceof SyntaxError?'Файл не похож на JSON.':err.message,'error')}
  finally{e.target.value=''}
});

