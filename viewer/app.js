const $ = (id) => document.getElementById(id);
const state = { graph:{nodes:[],edges:[]}, nodeMap:new Map(), selected:null, transform:{x:0,y:0,k:1}, activeStorey:null, simulationToken:0, modelId:null, model:null };
const svg = $('graphSvg');
const NS = 'http://www.w3.org/2000/svg';
let rootGroup, edgeGroup, nodeGroup, labelGroup;
function createSvg(tag, attrs={}) { const el=document.createElementNS(NS,tag); Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v)); return el; }
function fmt(n){ return n===null||n===undefined?'—':Number(n).toLocaleString(); }
async function getJSON(url, options){ const response=await fetch(url,options); if(!response.ok){ let message=`${response.status} ${response.statusText}`; try{ const body=await response.json(); message=typeof body.detail==='string'?body.detail:JSON.stringify(body.detail||body); }catch(_){} throw new Error(message);} if(response.status===204)return null; return response.json(); }
function setLoading(visible){ $('loading').classList.toggle('hidden',!visible); }
function modelUrl(path=''){ if(!state.modelId) throw new Error('Select or import a model first.'); return `/api/models/${encodeURIComponent(state.modelId)}${path}`; }
function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
}

function colorFor(node) {
  if (['IFCPROJECT', 'IFCSITE', 'IFCBUILDING', 'IFCBUILDINGSTOREY', 'IFCSPACE'].includes(node.ifc_class)) return '#67a8ff';
  if (node.typology === 'Structural') return '#ffb25b';
  if (node.typology === 'Architectural') return '#44d1b6';
  return '#9c8cff';
}

function radiusFor(node) {
  if (node.ifc_class === 'IFCPROJECT') return 15;
  if (node.ifc_class === 'IFCSITE') return 14;
  if (node.ifc_class === 'IFCBUILDING') return 15;
  if (node.ifc_class === 'IFCBUILDINGSTOREY') return 12;
  if (node.ifc_class === 'IFCSPACE') return 8;
  return 6.5;
}

function labelFor(node) {
  const n = node.name && !node.name.startsWith('Unnamed') ? node.name : node.ifc_class;
  return n.length > 28 ? `${n.slice(0, 27)}…` : n;
}

function initGraphSvg() {
  svg.innerHTML = '';
  rootGroup = createSvg('g');
  edgeGroup = createSvg('g');
  nodeGroup = createSvg('g');
  labelGroup = createSvg('g');
  rootGroup.append(edgeGroup, nodeGroup, labelGroup);
  svg.appendChild(rootGroup);
  applyTransform();
}

function applyTransform() {
  if (rootGroup) rootGroup.setAttribute('transform', `translate(${state.transform.x},${state.transform.y}) scale(${state.transform.k})`);
}

function initialLayout(nodes, mode, width, height) {
  const cx = width / 2, cy = height / 2;
  if (mode === 'spatial-path' || mode === 'sensor-location') {
    const order = state.graph.path_ids || nodes.map(n => n.step_id);
    nodes.sort((a,b) => order.indexOf(a.step_id) - order.indexOf(b.step_id));
    nodes.forEach((n, i) => { n.x = cx; n.y = 65 + i * Math.min(95, (height - 110) / Math.max(1, nodes.length - 1)); n.fixed = true; });
    return;
  }
  if (mode === 'overview') {
    const rank = n => {
      const c = n.ifc_class || '';
      if (c === 'IFCPROJECT') return 0;
      if (['IFCSITE','IFCFACILITY'].includes(c)) return 1;
      if (['IFCBUILDING','IFCBRIDGE','IFCROAD','IFCRAILWAY','IFCMARINEFACILITY'].includes(c)) return 2;
      if (['IFCBUILDINGSTOREY','IFCFACILITYPART'].includes(c)) return 3;
      if (c === 'IFCSPACE') return 4;
      return 5;
    };
    const groups = new Map();
    nodes.forEach(n => { const r=rank(n); if(!groups.has(r)) groups.set(r,[]); groups.get(r).push(n); });
    [...groups.entries()].forEach(([r, items]) => {
      items.sort((a,b)=>(a.elevation??0)-(b.elevation??0));
      items.forEach((n,i)=>{ n.x=items.length===1?cx:55+i*((width-110)/Math.max(1,items.length-1)); n.y=60+r*Math.min(95,(height-110)/5); n.fixed=true; });
    });
    return;
  }
  const classes = [...new Set(nodes.map(n => n.ifc_class || 'OTHER'))];
  nodes.forEach((n, idx) => {
    const ci = classes.indexOf(n.ifc_class || 'OTHER');
    const angle = (idx / Math.max(nodes.length, 1)) * Math.PI * 2 + ci * .25;
    const ring = 75 + (idx % 9) * 13;
    n.x = cx + Math.cos(angle) * ring; n.y = cy + Math.sin(angle) * ring;
    n.vx = 0; n.vy = 0; n.fixed = false;
  });
  const focus = nodes.find(n => Number(n.step_id) === Number(state.graph.focus_id));
  if (focus) { focus.x = cx; focus.y = cy; focus.fixed = true; }
}

function renderGraph(data) {
  state.graph = data;
  state.nodeMap = new Map(data.nodes.map(n => [Number(n.step_id), n]));
  data.edges.forEach(e => { e.source = Number(e.source); e.target = Number(e.target); });
  $('graphTitle').textContent = data.title || 'IFC graph';
  $('graphMeta').textContent = `${fmt(data.node_count)} nodes · ${fmt(data.edge_count)} relationships · queried from Neo4j`;
  $('cypherText').textContent = data.cypher || '—';
  initGraphSvg();

  const rect = $('graphStage').getBoundingClientRect();
  const width = Math.max(600, rect.width), height = Math.max(420, rect.height);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  initialLayout(data.nodes, data.mode, width, height);

  const edgeEls = data.edges.map((edge) => {
    const line = createSvg('line', { class: 'graph-edge', 'data-source': edge.source, 'data-target': edge.target });
    const title = createSvg('title'); title.textContent = edge.relationship;
    line.appendChild(title); edgeGroup.appendChild(line); return { edge, line };
  });
  const nodeEls = data.nodes.map((node) => {
    const circle = createSvg('circle', {
      class: 'graph-node', r: radiusFor(node), fill: colorFor(node),
      'data-id': node.step_id, tabindex: 0,
    });
    const title = createSvg('title'); title.textContent = `${node.ifc_class}\n${node.name || ''}\n#${node.step_id}`;
    circle.appendChild(title);
    circle.addEventListener('click', (ev) => { ev.stopPropagation(); selectNode(node); });
    circle.addEventListener('dblclick', (ev) => { ev.stopPropagation(); loadEntity(node.step_id); });
    addNodeDrag(circle, node);
    nodeGroup.appendChild(circle);

    const text = createSvg('text', { class: 'node-label', 'text-anchor': 'middle', dy: -(radiusFor(node) + 6) });
    const always = ['IFCPROJECT', 'IFCSITE', 'IFCBUILDING', 'IFCBUILDINGSTOREY'].includes(node.ifc_class) || data.nodes.length <= 45;
    text.textContent = always ? labelFor(node) : '';
    labelGroup.appendChild(text);
    return { node, circle, text };
  });

  function update() {
    edgeEls.forEach(({ edge, line }) => {
      const a = state.nodeMap.get(edge.source), b = state.nodeMap.get(edge.target);
      if (!a || !b) return;
      line.setAttribute('x1', a.x); line.setAttribute('y1', a.y);
      line.setAttribute('x2', b.x); line.setAttribute('y2', b.y);
    });
    nodeEls.forEach(({ node, circle, text }) => {
      circle.setAttribute('cx', node.x); circle.setAttribute('cy', node.y);
      text.setAttribute('x', node.x); text.setAttribute('y', node.y);
    });
  }
  state.updateGraph = update;
  update();

  if (!['overview', 'spatial-path'].includes(data.mode)) runSimulation(data.nodes, data.edges, width, height, update);
  fitGraph();
  if (data.focus_id) {
    const focus = state.nodeMap.get(Number(data.focus_id));
    if (focus) selectNode(focus);
  } else clearSelection();
}

function runSimulation(nodes, edges, width, height, update) {
  const token = ++state.simulationToken;
  const links = edges.map(e => ({ a: state.nodeMap.get(e.source), b: state.nodeMap.get(e.target) })).filter(l => l.a && l.b);
  let ticks = 0;
  function tick() {
    if (token !== state.simulationToken || ticks++ > 190) return;
    const alpha = 1 - ticks / 200;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      if (a.fixed) continue;
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy + 0.01;
        if (d2 > 38000) continue;
        const f = Math.min(2.8, 1250 / d2) * alpha;
        const d = Math.sqrt(d2); dx /= d; dy /= d;
        if (!a.fixed) { a.vx += dx * f; a.vy += dy * f; }
        if (!b.fixed) { b.vx -= dx * f; b.vy -= dy * f; }
      }
    }
    links.forEach(({ a, b }) => {
      let dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const desired = 58;
      const f = (d - desired) * 0.006 * alpha;
      dx /= d; dy /= d;
      if (!a.fixed) { a.vx += dx * f; a.vy += dy * f; }
      if (!b.fixed) { b.vx -= dx * f; b.vy -= dy * f; }
    });
    nodes.forEach(n => {
      if (n.fixed) return;
      n.vx += (width / 2 - n.x) * 0.00055 * alpha;
      n.vy += (height / 2 - n.y) * 0.00055 * alpha;
      n.vx *= 0.88; n.vy *= 0.88;
      n.x = Math.max(25, Math.min(width - 25, n.x + n.vx));
      n.y = Math.max(25, Math.min(height - 25, n.y + n.vy));
    });
    update();
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function addNodeDrag(circle, node) {
  let dragging = false;
  circle.addEventListener('pointerdown', (ev) => {
    ev.stopPropagation(); dragging = true; circle.setPointerCapture(ev.pointerId); node.fixed = true;
  });
  circle.addEventListener('pointermove', (ev) => {
    if (!dragging) return;
    const p = clientToGraph(ev.clientX, ev.clientY); node.x = p.x; node.y = p.y; state.updateGraph?.();
  });
  circle.addEventListener('pointerup', (ev) => { dragging = false; circle.releasePointerCapture(ev.pointerId); });
}

function clientToGraph(clientX, clientY) {
  const rect = svg.getBoundingClientRect();
  const vb = svg.viewBox.baseVal;
  const sx = vb.width / rect.width, sy = vb.height / rect.height;
  const x = (clientX - rect.left) * sx, y = (clientY - rect.top) * sy;
  return { x: (x - state.transform.x) / state.transform.k, y: (y - state.transform.y) / state.transform.k };
}

function selectNode(node) {
  state.selected = node;
  document.querySelectorAll('.graph-node').forEach(el => el.classList.toggle('selected', Number(el.dataset.id) === Number(node.step_id)));
  document.querySelectorAll('.graph-edge').forEach(el => {
    const connected = Number(el.dataset.source) === Number(node.step_id) || Number(el.dataset.target) === Number(node.step_id);
    el.classList.toggle('highlight', connected);
  });
  document.querySelectorAll('.node-label').forEach((el, i) => {
    const n = state.graph.nodes[i];
    const show = Number(n.step_id) === Number(node.step_id) || ['IFCPROJECT','IFCSITE','IFCBUILDING','IFCBUILDINGSTOREY'].includes(n.ifc_class) || state.graph.nodes.length <= 45;
    el.textContent = show ? labelFor(n) : '';
  });
  $('emptyDetails').classList.add('hidden');
  $('entityDetails').classList.remove('hidden');
  $('entityClass').textContent = node.ifc_class || 'IFC ENTITY';
  $('entityName').textContent = node.name || `#${node.step_id}`;
  const props = [
    ['STEP ID', `#${node.step_id}`], ['GlobalId', node.global_id], ['Typology', node.typology],
    ['Storey', node.storey_name], ['Elevation', node.elevation != null ? `${Number(node.elevation).toFixed(3)} m` : null],
    ['Position', [node.x, node.y, node.z].every(v => v != null) ? `${Number(node.x).toFixed(2)}, ${Number(node.y).toFixed(2)}, ${Number(node.z).toFixed(2)}` : null],
  ].filter(([,v]) => v !== null && v !== undefined && v !== '');
  $('entityProps').innerHTML = props.map(([k,v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join('');
  $('expandBtn').disabled = false; $('spatialPathBtn').disabled = false;
}

function clearSelection() {
  state.selected = null;
  $('emptyDetails').classList.remove('hidden'); $('entityDetails').classList.add('hidden');
  $('expandBtn').disabled = true; $('spatialPathBtn').disabled = true;
}

function fitGraph() {
  const nodes = state.graph.nodes;
  if (!nodes.length) return;
  const rect = $('graphStage').getBoundingClientRect();
  const width = Math.max(600, rect.width), height = Math.max(420, rect.height);
  const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
  const minX = Math.min(...xs) - 40, maxX = Math.max(...xs) + 40;
  const minY = Math.min(...ys) - 40, maxY = Math.max(...ys) + 40;
  const k = Math.min(1.7, Math.max(.22, Math.min(width / Math.max(1, maxX - minX), height / Math.max(1, maxY - minY)) * .88));
  state.transform.k = k;
  state.transform.x = width / 2 - ((minX + maxX) / 2) * k;
  state.transform.y = height / 2 - ((minY + maxY) / 2) * k;
  applyTransform();
}


async function withLoading(fn){ try{setLoading(true);await fn();}catch(err){console.error(err);alert(err.message||String(err));}finally{setLoading(false);} }
function renderCapabilities(capabilities={}){
  const list=$('capabilityList'); list.innerHTML='';
  const labels={spatial_navigation:'Spatial navigation',sensor_localization:'IFC sensor localization',mep_trace:'Connected MEP trace',structural_connectivity:'Structural connectivity'};
  Object.entries(labels).forEach(([key,label])=>{ const item=capabilities[key]||{}; const li=document.createElement('li'); li.className=item.available?'ok':'warn'; li.textContent=`${label}: ${item.available?'available':'not evidenced'}`; li.title=JSON.stringify(item.evidence||{}); list.appendChild(li); });
}
async function loadStatus(){ const s=await getJSON('/api/status'); $('backendPill').className='pill live'; $('backendPill').textContent=`● Live Neo4j · ${s.models} model${s.models===1?'':'s'}`; }
async function loadModels(selectId=null){
  const models=await getJSON('/api/models'); const sel=$('modelSelect'); sel.innerHTML='<option value="">Select a model</option>';
  models.forEach(m=>{ const o=document.createElement('option'); o.value=m.model_id; o.textContent=`${m.source_file} · ${m.schema||'unknown'}`; sel.appendChild(o); });
  const chosen=selectId || state.modelId || models[0]?.model_id;
  if(chosen){ sel.value=chosen; await activateModel(chosen,models.find(m=>m.model_id===chosen)); }
  else { state.modelId=null; state.model=null; }
}
async function activateModel(modelId, summary=null){
  state.modelId=modelId; state.model=summary || await getJSON(`/api/models/${encodeURIComponent(modelId)}`);
  const m=summary || state.model; const details=m.summary||m;
  $('pageTitle').textContent=`${m.source_file||details.source_file} · IFC + Neo4j`;
  $('metricNodes').textContent=fmt(m.semantic_nodes||details.semantic_nodes); $('metricEdges').textContent=fmt(m.semantic_relationships||details.semantic_relationships); $('metricSchema').textContent=m.schema||details.schema||'—';
  renderCapabilities(m.capabilities||details.capabilities||{}); await loadStoreys(); await loadOverview();
}
async function loadStoreys(){ if(!state.modelId)return; const rows=await getJSON(modelUrl('/storeys')); $('metricStoreys').textContent=rows.length; const stack=$('storeyStack'); stack.innerHTML=''; rows.forEach(s=>{ const row=document.createElement('div'); row.className='storey'; row.dataset.id=s.step_id; const elev=s.elevation==null?'':`${Number(s.elevation).toFixed(2)} m`; row.innerHTML=`<strong>${escapeHtml(s.name||'#'+s.step_id)}</strong><span>${escapeHtml(elev)} · ${fmt(s.direct_elements)}</span>`; row.onclick=()=>loadStorey(s.step_id); stack.appendChild(row); }); }
async function loadOverview(){ if(!state.modelId)return; state.activeStorey=null; document.querySelectorAll('.storey').forEach(el=>el.classList.remove('active')); await withLoading(async()=>renderGraph(await getJSON(modelUrl('/graph/overview')))); }
async function loadStorey(id){ state.activeStorey=Number(id); document.querySelectorAll('.storey').forEach(el=>el.classList.toggle('active',Number(el.dataset.id)===Number(id))); const cls=$('classFilter').value; await withLoading(async()=>renderGraph(await getJSON(modelUrl(`/graph/storey/${encodeURIComponent(id)}?limit=500${cls?`&ifc_class=${encodeURIComponent(cls)}`:''}`)))); }
async function loadEntity(identifier){ await withLoading(async()=>renderGraph(await getJSON(modelUrl(`/graph/entity/${encodeURIComponent(identifier)}?depth=1&limit=600`)))); }
async function loadSpatialPath(identifier){ await withLoading(async()=>renderGraph(await getJSON(modelUrl(`/graph/spatial-path/${encodeURIComponent(identifier)}`)))); }
async function runSearch(){ const q=$('searchInput').value.trim(); if(!q)return; await withLoading(async()=>renderGraph(await getJSON(modelUrl(`/graph/search?q=${encodeURIComponent(q)}&limit=100`)))); }
async function uploadModel(){ const file=$('fileInput').files[0]; if(!file){alert('Choose an IFC or IFCZIP file first.');return;} const form=new FormData(); form.append('file',file); $('uploadProgress').textContent=`Uploading and parsing ${file.name}…`; await withLoading(async()=>{ const result=await getJSON('/api/models/import',{method:'POST',body:form}); $('uploadProgress').textContent=`Imported ${result.semantic_nodes.toLocaleString()} nodes and ${result.semantic_relationships.toLocaleString()} relationships.`; await loadStatus(); await loadModels(result.model_id); }); }
function setupPanZoom(){ let panning=false,start=null; svg.addEventListener('pointerdown',ev=>{if(ev.target!==svg)return;panning=true;start={x:ev.clientX,y:ev.clientY,tx:state.transform.x,ty:state.transform.y};svg.setPointerCapture(ev.pointerId);}); svg.addEventListener('pointermove',ev=>{if(!panning)return;const rect=svg.getBoundingClientRect(),vb=svg.viewBox.baseVal;state.transform.x=start.tx+(ev.clientX-start.x)*vb.width/rect.width;state.transform.y=start.ty+(ev.clientY-start.y)*vb.height/rect.height;applyTransform();}); svg.addEventListener('pointerup',ev=>{panning=false;try{svg.releasePointerCapture(ev.pointerId)}catch(_){}}); svg.addEventListener('wheel',ev=>{ev.preventDefault();const before=clientToGraph(ev.clientX,ev.clientY);state.transform.k=Math.max(.15,Math.min(4,state.transform.k*(ev.deltaY<0?1.12:.89)));const rect=svg.getBoundingClientRect(),vb=svg.viewBox.baseVal,px=(ev.clientX-rect.left)*vb.width/rect.width,py=(ev.clientY-rect.top)*vb.height/rect.height;state.transform.x=px-before.x*state.transform.k;state.transform.y=py-before.y*state.transform.k;applyTransform();},{passive:false}); svg.addEventListener('click',ev=>{if(ev.target===svg)clearSelection();}); }
function bindEvents(){ $('uploadBtn').onclick=uploadModel; $('refreshModelsBtn').onclick=()=>loadModels(); $('modelSelect').onchange=e=>e.target.value&&activateModel(e.target.value); $('overviewBtn').onclick=loadOverview; $('searchBtn').onclick=runSearch; $('searchInput').onkeydown=e=>{if(e.key==='Enter')runSearch();}; $('classFilter').onchange=()=>state.activeStorey&&loadStorey(state.activeStorey); $('fitBtn').onclick=fitGraph; $('expandBtn').onclick=()=>state.selected&&loadEntity(state.selected.step_id); $('spatialPathBtn').onclick=()=>state.selected&&loadSpatialPath(state.selected.step_id); $('detailExpandBtn').onclick=()=>state.selected&&loadEntity(state.selected.step_id); $('detailPathBtn').onclick=()=>state.selected&&loadSpatialPath(state.selected.step_id); $('copyCypher').onclick=async()=>{await navigator.clipboard.writeText($('cypherText').textContent);}; window.addEventListener('resize',()=>setTimeout(fitGraph,100)); setupPanZoom(); }
async function start(){ bindEvents(); await loadStatus(); await loadModels(); }
start().catch(err=>{console.error(err);alert(`Viewer failed to start: ${err.message}`);});
