(()=> {
const q=s=>document.querySelector(s);
const roles=[
 ['router_provider_id','Модель-роутер','Анализирует план и назначает минимально достаточный tier.'],
 ['multimodal_provider_id','Мультимодальная модель','Используется для задач, которым нужен визуальный контекст.'],
 ['smart_provider_id','Умная модель','Сложные рассуждения, код и финальная сборка.'],
 ['medium_provider_id','Средняя модель','Обычные содержательные и структурированные задачи.'],
 ['weak_provider_id','Слабая модель','Простые механические и подготовительные действия.']
];
let providers=[];
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function apiL(url,options={}){
 const r=await fetch(url,{credentials:'same-origin',...options});
 const d=await r.json().catch(()=>({}));
 if(!r.ok)throw new Error(d.error||('HTTP '+r.status));
 return d;
}
function options(role,selected){
 const multi=role==='multimodal_provider_id';
 const rows=providers.filter(p=>p.kind!=='image'&&(!multi||p.kind==='multimodal'));
 return '<option value="">Не выбрано</option>'+rows.map(p=>'<option value="'+p.id+'" '+(String(p.id)===String(selected??'')?'selected':'')+'>'+esc(p.name)+' · '+esc(p.model)+'</option>').join('');
}
function render(data={}){
 const box=q('#workLadderBox');if(!box)return;
 box.innerHTML=roles.map(r=>'<label class="ladder-row"><span><strong>'+r[1]+'</strong><small>'+r[2]+'</small></span><select data-ladder-key="'+r[0]+'">'+options(r[0],data[r[0]])+'</select></label>').join('')+
 '<div class="ladder-fallback"><strong>Резервные модели</strong><small>До пяти моделей. Пробуются по порядку после ошибки основной модели.</small><div id="ladderFallbacks">'+
 (data.fallback_provider_ids||[]).map((id,i)=>fallbackRow(i,id)).join('')+
 '</div><button type="button" class="ghost" id="addFallback">＋ Добавить резервную модель</button></div>';
}
function fallbackRow(i,id){return '<div class="fallback-row"><span>'+(i+1)+'.</span><select data-fallback="'+i+'">'+options('fallback',id)+'</select><button type="button" data-remove-fallback="'+i+'">×</button></div>'}
function sync(){
 const on=!!q('#workLadderEnabled')?.checked;
 document.querySelectorAll('#workLadderBox select,#addFallback,#ladderSave').forEach(x=>x.disabled=!on);
 q('#workLadderBox')?.classList.toggle('disabled',!on);
}
async function load(){
 try{
  const d=await apiL('/api/work/ladder');
  providers=await apiL('/api/providers');
  render(d);q('#workLadderEnabled').checked=!!d.enabled;sync();
 }catch(e){console.warn('work ladder:',e)}
}
async function save(){
 const payload={enabled:!!q('#workLadderEnabled')?.checked};
 document.querySelectorAll('[data-ladder-key]').forEach(x=>payload[x.dataset.ladderKey]=x.value?Number(x.value):null);
 payload.fallback_provider_ids=[...document.querySelectorAll('[data-fallback]')].map(x=>Number(x.value)).filter(Boolean);
 try{
  const d=await apiL('/api/work/ladder',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  render(d);q('#workLadderEnabled').checked=!!d.enabled;sync();q('#ladderStatus').textContent='Сохранено';
  setTimeout(()=>q('#ladderStatus').textContent='',1800);
 }catch(e){q('#ladderStatus').textContent=e.message}
}
function bind(){
 q('#ladderSave')?.addEventListener('click',save);
 q('#workLadderEnabled')?.addEventListener('change',sync);
 document.addEventListener('click',e=>{
  if(e.target.id==='addFallback'){
   const box=q('#ladderFallbacks');if(!box)return;
   const n=box.querySelectorAll('[data-fallback]').length;if(n>=5)return;
   box.insertAdjacentHTML('beforeend',fallbackRow(n,''));
   sync();
  }
  if(e.target.dataset.removeFallback!==undefined){
   e.target.closest('.fallback-row')?.remove();
   [...document.querySelectorAll('[data-fallback]')].forEach((x,i)=>x.dataset.fallback=i);
  }
 });
}
if(q('#modal')){load();bind();}
})();
