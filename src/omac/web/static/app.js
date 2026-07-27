(function(){
"use strict";

/* ---------- 状态 → 着色 ---------- */
const STATES = [
  "todo","pending","in_progress","running","ci_check","in_review","review",
  "rework","merging","done","failed","blocked","abandoned",
];
const COPY = {
  en: {
    choose:"Choose", manifest_title:"Choose a manifest", last_refresh:"Last status refresh",
    theme:"Theme", theme_auto:"System", theme_dark:"Dark", theme_light:"Light",
    dag_overview:"DAG overview", dag_visualization:"DAG visualization", fit:"Fit",
    reset_view:"Reset", reset_view_title:"Reset zoom and pan",
    collapse:"Collapse", expand_all:"Expand all", focus_active:"Active", focus_anomaly:"Anomaly focus", hidden:"hidden",
    show_next:"show next", budget_limited:"Visible budget reached",
    fit_title:"Fit the full graph", node_details:"Node details",
    select_node:"Select a DAG node to inspect its contract, evidence, and links.",
    anomalies:"Anomalies", static_info:"Static information",
    no_anomalies:"No failed or blocked nodes. The DAG is converged or still progressing.",
    refresh:"Refresh", static_intro:"Choose a manifest to load configuration, source manifest, and acceptance checks.",
    dag_loading:"Loading DAG from manifest…", dag_load_failed:"Could not load the manifest DAG.",
    progress:"Progress", refresh_failed:"Refresh failed", no_selected:"No node is selected.",
    none:"None", node:"Node", status:"Status", dependencies:"Dependencies",
    loading_evidence:"Loading contract and evidence…", evidence_chain:"Evidence",
    no_evidence:"No evidence has been collected for this node yet.", bounce_count:"Bounce count",
    copy_retry:"Copy retry", copy_abandon:"Copy abandon", detail_failed:"Failed to load node details",
    reason:"Reason", blocked_downstream:"Blocked downstream", copy:"Copy",
    copied:"Copied to clipboard", copy_failed:"Copy failed", loading_static:"Loading configuration and acceptance checks…",
    config_summary:"config.yaml summary", acceptance_checks:"Acceptance checks",
    acceptance_missing:"Acceptance file not found", static_failed:"Failed to load static information",
    state_todo:"Todo", state_pending:"Pending", state_in_progress:"In progress", state_running:"Running", state_ci_check:"CI check",
    state_in_review:"In review", state_review:"Review", state_rework:"Rework", state_merging:"Merging", state_done:"Done",
    state_failed:"Failed", state_blocked:"Blocked", state_abandoned:"Abandoned", state_unknown:"Unknown",
    dag_error_unknown_dependency:"Cannot render DAG: it references a missing dependency.",
    dag_error_cycle:"Cannot render DAG: it contains a dependency cycle."
  },
  cn: {
    choose:"选择", manifest_title:"选择要查看的 manifest", last_refresh:"最后状态刷新时间",
    theme:"主题", theme_auto:"跟随系统", theme_dark:"深色", theme_light:"浅色",
    dag_overview:"DAG 总览", dag_visualization:"DAG 可视化", fit:"全图",
    reset_view:"重置", reset_view_title:"重置缩放和平移",
    collapse:"收起", expand_all:"全部展开", focus_active:"进行中", focus_anomaly:"异常聚焦", hidden:"已隐藏",
    show_next:"展开下一批", budget_limited:"已达到可见预算",
    fit_title:"回到全图视野", node_details:"节点详情",
    select_node:"点击 DAG 中的一个节点查看 contract、证据和链接。",
    anomalies:"异常面板", static_info:"静态信息页",
    no_anomalies:"当前无 failed 或 blocked 节点，所有节点已收敛或仍在推进。",
    refresh:"刷新", static_intro:"选择一个 manifest 后加载配置、manifest 原文和验收清单。",
    dag_loading:"正在从 manifest 加载 DAG…", dag_load_failed:"无法加载 manifest DAG。",
    progress:"进度", refresh_failed:"刷新失败", no_selected:"当前无选中节点。",
    none:"无", node:"节点", status:"状态", dependencies:"依赖",
    loading_evidence:"正在加载 contract 和证据…", evidence_chain:"证据链",
    no_evidence:"该节点暂无已收集证据。", bounce_count:"回退计数",
    copy_retry:"复制 retry", copy_abandon:"复制 abandon", detail_failed:"节点详情加载失败",
    reason:"原因", blocked_downstream:"受阻下游", copy:"复制",
    copied:"已复制到剪贴板", copy_failed:"复制失败", loading_static:"正在加载配置和验收清单…",
    config_summary:"config.yaml 摘要", acceptance_checks:"验收清单",
    acceptance_missing:"未找到验收文件", static_failed:"静态信息加载失败",
    state_todo:"待开始", state_pending:"待开始", state_in_progress:"进行中", state_running:"运行中", state_ci_check:"CI 校验",
    state_in_review:"评审中", state_review:"评审中", state_rework:"返工中", state_merging:"合并中", state_done:"完成",
    state_failed:"失败", state_blocked:"受阻", state_abandoned:"已放弃", state_unknown:"未知",
    dag_error_unknown_dependency:"无法渲染 DAG：它引用了不存在的依赖。",
    dag_error_cycle:"无法渲染 DAG：它包含依赖环。"
  }
};
function copy(key){ return (COPY[state.language]||COPY.en)[key] || COPY.en[key] || key; }
function stateLabel(value){
  const key = "state_"+(value || "unknown");
  const label = copy(key);
  return label === key ? (value || copy("state_unknown")) : label;
}
function applyLanguage(language){
  state.language = language === "cn" ? "cn" : "en";
  document.documentElement.lang = state.language === "cn" ? "zh-CN" : "en";
  document.title = state.language === "cn" ? "omac web — DAG 可视化面板" : "omac web — DAG dashboard";
  document.querySelectorAll("[data-i18n]").forEach(el => el.textContent=copy(el.dataset.i18n));
  document.querySelectorAll("[data-i18n-title]").forEach(el => el.title=copy(el.dataset.i18nTitle));
  document.querySelectorAll("[data-i18n-aria]").forEach(el => el.setAttribute("aria-label",copy(el.dataset.i18nAria)));
}
function stateColor(status, root){
  const view = OMACDag.presentState(status);
  const name = view.safe_fallback ? "unknown" : status;
  return getComputedStyle(root||document.documentElement).getPropertyValue("--"+name).trim() || "#888";
}

/* ---------- 全局状态 ---------- */
const state = {
  manifests: [],
  current: null,
  status: null,
  refresh: 10,
  nodes: {},
  selected: null,
  expanded: [],
  focus: "default",
  viewport: {x:0, y:0, scale:1, initialized:false, userAdjusted:false},
  graphBounds: null,
  detailGeneration: 0,
  pollTimer: null,
  statusRequest: null,
  language: "en",
};

/* ---------- 工具 ---------- */
const $ = (id) => document.getElementById(id);
function toast(msg, isErr){
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("is-visible");
  t.classList.toggle("err", !!isErr);
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("is-visible"), 2500);
}
async function api(path){
  const r = await fetch(path);
  if(!r.ok){
    let detail = r.status;
    try { detail = (await r.json()).detail || (await r.json()).error || detail; } catch(e){}
    throw new Error(path+" → "+detail);
  }
  if(((r.headers.get("content-type"))||"").includes("application/json")) return r.json();
  return r.text();
}
function esc(s){
  return String(s==null?"":s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}
function setTheme(mode){
  localStorage.setItem("omac-theme", mode);
  applyTheme(mode);
}
function applyTheme(mode){
  const dark = mode==="dark" || (mode==="auto" && window.matchMedia("(prefers-color-scheme:dark)").matches);
  document.documentElement.classList.toggle("light", !dark);
}
applyTheme(localStorage.getItem("omac-theme")||"auto");

$("theme-select").value = localStorage.getItem("omac-theme")||"auto";
$("theme-select").addEventListener("change", e => setTheme(e.target.value));
window.matchMedia("(prefers-color-scheme:dark)").addEventListener("change", ()=>{
  if((localStorage.getItem("omac-theme")||"auto")==="auto") applyTheme("auto");
});

/* ---------- manifest 选择 ---------- */
function clearNodeDetail(){
  state.detailGeneration += 1;
  $("detail-empty").classList.remove("is-hidden");
  $("detail-content").innerHTML = "";
}

function clearRenderedStatus(){
  state.status = null;
  state.nodes = {};
  const svg = $("dag-canvas");
  while(svg.firstChild) svg.removeChild(svg.firstChild);
  $("dag-legend").innerHTML = "";
  $("anomaly-content").innerHTML = "";
  $("anomaly-empty").classList.remove("is-hidden");
  const progress = $("progress-badge");
  progress.classList.add("is-hidden");
  progress.textContent = "";
  $("poll-ts").textContent = "—";
  $("tick-state").textContent = "—";
  $("dag-budget").classList.add("is-hidden");
  $("dag-budget").textContent = "";
}

function resetDagView(){
  state.expanded = [];
  state.focus = "default";
  state.selected = null;
  state.viewport = {x:0, y:0, scale:1, initialized:false, userAdjusted:false};
  state.graphBounds = null;
  clearNodeDetail();
}

async function loadManifests(){
  state.manifests = await api("/api/manifests");
  const sel = $("manifest-selector");
  sel.innerHTML = '<option value="">— '+copy("choose")+' —</option>' +
    state.manifests.map(m => '<option value="'+esc(m.path)+'">'+esc(m.name)+' ('+m.done+'/'+m.total+')</option>').join("");
  // 记住上次选择
  const last = localStorage.getItem("omac-last-manifest");
  if(last && state.manifests.some(m => m.path===last)){
    sel.value = last;
    selectManifest(last);
  }
}
$("manifest-selector").addEventListener("change", e => selectManifest(e.target.value));

async function selectManifest(path){
  const isNewManifest = state.current !== path;
  cancelPoll();
  if(isNewManifest){
    resetDagView();
    clearRenderedStatus();
    if(path) renderDagNotice(copy("dag_loading"), false);
  }
  if(!path){ state.current = null; return; }
  localStorage.setItem("omac-last-manifest", path);
  state.current = path;
  await fetchStatus();
  startPoll();
}

/* ---------- 状态获取 + 轮询 ---------- */
async function fetchStatus(){
  const manifest = state.current;
  if(!manifest) return;
  if(state.statusRequest && state.statusRequest.manifest===manifest){
    return state.statusRequest.promise;
  }
  const request = (async ()=>{
    try{
      const s = await api("/api/dag/status?manifest="+encodeURIComponent(manifest));
      if(state.current !== manifest) return;
      state.status = s;
      state.nodes = {};
      (s.nodes||[]).forEach(n => state.nodes[n.key]=n);
      renderDAG(s);
      renderAnomaly(s);
      const pb = $("progress-badge");
      pb.classList.remove("is-hidden");
      pb.textContent = copy("progress")+" "+(s.progress?s.progress.done+"/"+s.progress.total:"?");
      $("poll-ts").textContent = new Date().toLocaleTimeString();
    }catch(e){
      if(state.current===manifest && !state.status){
        renderDagNotice(copy("dag_load_failed"), true);
      }
      toast(copy("refresh_failed")+": "+e.message, true);
    }
  })();
  state.statusRequest = {manifest, promise:request};
  try{
    return await request;
  }finally{
    if(state.statusRequest && state.statusRequest.promise===request){
      state.statusRequest = null;
    }
  }
}
function startPoll(){
  cancelPoll();
  state.pollTimer = setInterval(fetchStatus, state.refresh*1000);
}
function cancelPoll(){ if(state.pollTimer){ clearInterval(state.pollTimer); state.pollTimer=null; } }

function redrawDAG(){ if(state.status) renderDAG(state.status); }
$("collapse-btn").addEventListener("click", ()=>{
  state.expanded = OMACDag.collapseBranches(state.expanded);
  state.focus = "default";
  redrawDAG();
});
$("expand-all-btn").addEventListener("click", ()=>{
  state.expanded = [];
  state.focus = "all";
  redrawDAG();
});
$("focus-active-btn").addEventListener("click", ()=>{
  state.expanded = [];
  state.focus = "active";
  redrawDAG();
});
$("focus-anomaly-btn").addEventListener("click", ()=>{
  state.expanded = [];
  state.focus = "anomaly";
  redrawDAG();
});

/* ---------- DAG 布局渲染 ---------- */
function layeredLayout(projection){
  const colW=parseInt(getComputedStyle(document.documentElement).getPropertyValue("--col-w"))||230;
  const rowH=parseInt(getComputedStyle(document.documentElement).getPropertyValue("--row-h"))||88;
  return OMACDag.layoutDag(projection, {colW, rowH});
}

function canvasSize(){
  const svg = $("dag-canvas");
  return {width:svg.clientWidth||960, height:svg.clientHeight||520};
}

function applyViewport(){
  const world = $("dag-canvas").querySelectorAll(".dag-world")[0];
  if(!world) return;
  const view = state.viewport;
  world.setAttribute("transform", "translate("+view.x+" "+view.y+") scale("+view.scale+")");
}

function setViewport(next, userAdjusted){
  const scale = Math.max(.2, Math.min(3.5, Number(next.scale)||1));
  state.viewport = {
    x:Number(next.x)||0,
    y:Number(next.y)||0,
    scale,
    initialized:true,
    userAdjusted:userAdjusted === undefined ? true : !!userAdjusted,
  };
  applyViewport();
}

function fitView(userAdjusted){
  if(!state.graphBounds) return;
  const size = canvasSize();
  const margin = 32;
  const scale = Math.min(
    (size.width-margin*2)/state.graphBounds.W,
    (size.height-margin*2)/state.graphBounds.H,
    1.5,
  );
  const safeScale = Math.max(.2, scale);
  setViewport({
    scale:safeScale,
    x:(size.width-state.graphBounds.W*safeScale)/2,
    y:(size.height-state.graphBounds.H*safeScale)/2,
  }, userAdjusted);
}

function resetViewport(){
  setViewport({x:0, y:0, scale:1}, true);
}

function renderDagError(svg, error){
  const ns = "http://www.w3.org/2000/svg";
  const details = error.code === "unknown_dependency"
    ? error.dependencies.map(item => item.node+" → "+item.dependency).join(", ")
    : error.nodes.join(", ");
  const message = error.code === "unknown_dependency"
    ? copy("dag_error_unknown_dependency") : copy("dag_error_cycle");
  const size = canvasSize();
  svg.setAttribute("viewBox", "0 0 "+size.width+" "+size.height);
  const text = document.createElementNS(ns, "text");
  text.setAttribute("x", "24"); text.setAttribute("y", "80");
  text.setAttribute("class", "dag-error"); text.textContent = message;
  svg.appendChild(text);
  const detail = document.createElementNS(ns, "text");
  detail.setAttribute("x", "24"); detail.setAttribute("y", "108");
  detail.setAttribute("class", "dag-error-detail"); detail.textContent = details;
  svg.appendChild(detail);
}

function renderDagNotice(message, isError){
  const svg = $("dag-canvas");
  while(svg.firstChild) svg.removeChild(svg.firstChild);
  const size = canvasSize();
  svg.setAttribute("viewBox", "0 0 "+size.width+" "+size.height);
  const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
  text.setAttribute("x", "24"); text.setAttribute("y", "80");
  text.setAttribute("class", isError ? "dag-error" : "dag-notice");
  text.textContent = message;
  svg.appendChild(text);
}

function renderDAG(s){
  const svg = $("dag-canvas");
  const projection = OMACDag.projectDag(s.nodes||[], {
    expanded: state.expanded,
    focus: state.focus,
    pinned: state.selected ? [state.selected] : [],
  });
  const L = layeredLayout(projection);
  const size = canvasSize();
  svg.setAttribute("viewBox", "0 0 "+size.width+" "+size.height);
  state.graphBounds = {W:Math.max(L.W,400), H:Math.max(L.H,200)};
  // 清空 + 渲染组
  const ns = "http://www.w3.org/2000/svg";
  while(svg.firstChild) svg.removeChild(svg.firstChild);
  if(projection.error){
    renderDagError(svg, projection.error);
    $("dag-legend").innerHTML = "";
    syncSelectedProjection(projection);
    return;
  }
  const defs = document.createElementNS(ns,"defs");
  const marker = document.createElementNS(ns,"marker");
  marker.setAttribute("id","dag-arrow");
  marker.setAttribute("markerWidth","8"); marker.setAttribute("markerHeight","8");
  marker.setAttribute("refX","7"); marker.setAttribute("refY","4");
  marker.setAttribute("orient","auto"); marker.setAttribute("markerUnits","strokeWidth");
  const arrow = document.createElementNS(ns,"path");
  arrow.setAttribute("d","M 0 0 L 8 4 L 0 8 z"); arrow.setAttribute("class","arrowhead");
  marker.appendChild(arrow); defs.appendChild(marker); svg.appendChild(defs);
  const world = document.createElementNS(ns,"g");
  world.setAttribute("class","dag-world");
  const edgesG = document.createElementNS(ns,"g");
  const nodesG = document.createElementNS(ns,"g");
  world.appendChild(edgesG); world.appendChild(nodesG); svg.appendChild(world);

  projection.edges.forEach(edge => {
      const a=L.pos[edge.from], b=L.pos[edge.to];
      const x1=a.x+L.nodeWidth, y1=a.y+L.nodeHeight/2, x2=b.x, y2=b.y+L.nodeHeight/2;
      const mx=(x1+x2)/2;
      const path=document.createElementNS(ns,"path");
      path.setAttribute("d","M "+x1+" "+y1+" C "+mx+" "+y1+", "+mx+" "+y2+", "+x2+" "+y2);
      path.setAttribute("class","edge");
      path.setAttribute("marker-end","url(#dag-arrow)");
      path.dataset.from=edge.from; path.dataset.to=edge.to;
      edgesG.appendChild(path);
  });

  projection.nodes.forEach(n => {
    const p=L.pos[n.key]; if(!p) return;
    const presentation = OMACDag.presentState(n.status);
    const styleName = presentation.safe_fallback ? "unknown" : n.status;
    const g=document.createElementNS(ns,"g");
    g.setAttribute("class","node state-"+styleName+(state.selected===n.key?" selected":""));
    g.dataset.key=n.key;
    const rect=document.createElementNS(ns,"rect");
    rect.setAttribute("x",p.x); rect.setAttribute("y",p.y);
    rect.setAttribute("width",L.nodeWidth); rect.setAttribute("height",L.nodeHeight);
    rect.setAttribute("fill", stateColor(n.status, document.documentElement)+"22");
    rect.setAttribute("stroke", stateColor(n.status, document.documentElement));
    g.appendChild(rect);
    const t1=document.createElementNS(ns,"text");
    t1.setAttribute("x",p.x+10); t1.setAttribute("y",p.y+22); t1.setAttribute("class","ni");
    t1.textContent=presentation.icon;
    g.appendChild(t1);
    const tKey=document.createElementNS(ns,"text");
    tKey.setAttribute("x",p.x+28); tKey.setAttribute("y",p.y+22);
    tKey.setAttribute("class","nk"); tKey.textContent=n.key;
    g.appendChild(tKey);
    const t2=document.createElementNS(ns,"text");
    t2.setAttribute("x",p.x+10); t2.setAttribute("y",p.y+40);
    t2.setAttribute("class","ns"); t2.textContent=stateLabel(n.status);
    g.appendChild(t2);
    g.addEventListener("click", ()=> selectNode(n.key));
    nodesG.appendChild(g);
  });

  projection.aggregates.forEach(aggregate => {
    const token=aggregate.source===null ? OMACDag.ROOT_AGGREGATE : aggregate.source;
    const source=aggregate.source===null ? null : L.pos[aggregate.source];
    const aggregatePosition=L.aggregatePositions[token];
    if(!aggregatePosition) return;
    const x=aggregatePosition.x, y=aggregatePosition.y;
    if(source){
      const edge=document.createElementNS(ns,"path");
      edge.setAttribute("d","M "+(source.x+L.nodeWidth)+" "+(source.y+L.nodeHeight/2)+" L "+x+" "+(y+L.aggregateHeight/2));
      edge.setAttribute("class","aggregate-edge");
      edge.setAttribute("marker-end","url(#dag-arrow)");
      edgesG.appendChild(edge);
    }
    const g=document.createElementNS(ns,"g");
    g.setAttribute("class","aggregate"); g.dataset.source=token;
    const rect=document.createElementNS(ns,"rect");
    rect.setAttribute("x",x); rect.setAttribute("y",y); rect.setAttribute("width",L.aggregateWidth); rect.setAttribute("height",L.aggregateHeight); rect.setAttribute("rx",6);
    g.appendChild(rect);
    const label=document.createElementNS(ns,"text");
    const summary=aggregate.status_summary.map(item => stateLabel(item.status)+" "+item.count).join(" · ");
    const nextCount=Math.min(OMACDag.EXPAND_BATCH, aggregate.hidden_count);
    label.setAttribute("x",x+8); label.setAttribute("y",y+16);
    label.textContent="+ "+aggregate.hidden_count+" "+copy("hidden")+" · "+copy("show_next")+" "+nextCount;
    g.appendChild(label);
    const detail=document.createElementNS(ns,"text");
    detail.setAttribute("x",x+8); detail.setAttribute("y",y+30); detail.textContent=summary;
    g.appendChild(detail);
    g.addEventListener("click", ()=>{
      state.expanded.push(token);
      state.focus="default"; redrawDAG();
    });
    nodesG.appendChild(g);
  });

  syncSelectedProjection(projection);
  if(!state.viewport.initialized) fitView(false); else applyViewport();

  const budget = $("dag-budget");
  const limited = projection.budget && projection.budget.truncated;
  budget.classList.toggle("is-hidden", !limited);
  budget.textContent = limited
    ? copy("budget_limited")+": "+projection.budget.visible_nodes+"/"+projection.budget.node_limit+" nodes · "+projection.budget.visible_edges+"/"+projection.budget.edge_limit+" edges"
    : "";

  const present = new Set((s.nodes||[]).map(n => n.status));
  const lg = $("dag-legend");
  lg.innerHTML = STATES.filter(status => present.has(status))
    .map(status=> '<span class="legend-'+esc(status)+'"><i></i>'+stateLabel(status)+'</span>').join("");
}

function dimForSelected(){
  const svg=$("dag-canvas"); if(!state.selected){
    svg.querySelectorAll(".node,.edge").forEach(e=>{e.classList.remove("dim","hl");}); return;
  }
  const relations=OMACDag.directRelations(Object.values(state.nodes), state.selected);
  const reach=new Set([state.selected, ...relations.upstream, ...relations.downstream]);
  svg.querySelectorAll(".node").forEach(g=>{
    const k=g.dataset.key;
    g.classList.toggle("dim", !reach.has(k));
  });
  svg.querySelectorAll(".edge").forEach(e=>{
    const f=e.dataset.from, t=e.dataset.to;
    const highlighted = state.selected===f || state.selected===t;
    e.classList.toggle("hl", highlighted);
    e.classList.toggle("dim", !highlighted);
  });
}

$("fit-btn").addEventListener("click", ()=> fitView(true));
$("reset-view-btn").addEventListener("click", resetViewport);

const dagCanvas = $("dag-canvas");
dagCanvas.addEventListener("wheel", event=>{
  event.preventDefault();
  const rect = dagCanvas.getBoundingClientRect ? dagCanvas.getBoundingClientRect() : {left:0, top:0};
  const px = (event.clientX||0)-rect.left;
  const py = (event.clientY||0)-rect.top;
  const previous = state.viewport;
  const factor = Math.exp(-(event.deltaY||0)*.0015);
  const scale = Math.max(.2, Math.min(3.5, previous.scale*factor));
  const worldX = (px-previous.x)/previous.scale;
  const worldY = (py-previous.y)/previous.scale;
  setViewport({x:px-worldX*scale, y:py-worldY*scale, scale}, true);
}, {passive:false});

let pan = null;
dagCanvas.addEventListener("pointerdown", event=>{
  pan = {pointerId:event.pointerId, x:event.clientX, y:event.clientY,
    originX:state.viewport.x, originY:state.viewport.y};
  if(dagCanvas.setPointerCapture) dagCanvas.setPointerCapture(event.pointerId);
  dagCanvas.classList.add("is-panning");
});
dagCanvas.addEventListener("pointermove", event=>{
  if(!pan || pan.pointerId!==event.pointerId) return;
  setViewport({
    x:pan.originX+event.clientX-pan.x,
    y:pan.originY+event.clientY-pan.y,
    scale:state.viewport.scale,
  }, true);
});
function endPan(event){
  if(!pan || pan.pointerId!==event.pointerId) return;
  pan=null; dagCanvas.classList.remove("is-panning");
}
dagCanvas.addEventListener("pointerup", endPan);
dagCanvas.addEventListener("pointercancel", endPan);

function clearSelection(){
  state.selected = null;
  clearNodeDetail();
  dimForSelected();
}

function syncSelectedProjection(projection){
  if(!state.selected) return;
  const visibleKeys = new Set(projection.nodes.map(node => node.key));
  if(!visibleKeys.has(state.selected) || !state.nodes[state.selected]){
    clearSelection();
    return;
  }
  dimForSelected();
}

/* ---------- 节点详情 ---------- */
function selectNode(key){
  if(state.selected === key){
    clearSelection();
    return;
  }
  state.selected = key;
  $("dag-canvas").querySelectorAll(".node").forEach(g=>{
    g.classList.toggle("selected", g.dataset.key===state.selected);
  });
  dimForSelected();
  renderDetail(key);
}
async function renderDetail(key){
  // 立即用 status 中的节点信息绘制基本卡, 再通过 /api/node/{key} 拿合约与证据
  const manifest = state.current;
  const detailGeneration = ++state.detailGeneration;
  const n = state.nodes[key];
  const dc = $("detail-content");
  $("detail-empty").classList.add("is-hidden");
  if(!n){ dc.innerHTML='<p class="empty">'+copy("no_selected")+'</p>'; return; }
  const sub = (arr)=> (Array.isArray(arr)&&arr.length)?"<ul class='acceptance'>"+arr.map(x=>"<li>"+esc(x)+"</li>")+"</ul>" : '<span class="empty">'+copy("none")+'</span>';
  dc.innerHTML =
    '<h3 class="region-title">'+copy("node")+' '+esc(key)+'</h3>'+
    '<dl class="kv">'+
      '<dt>'+copy("status")+'</dt><dd class="hl-'+esc(n.status)+'">'+stateLabel(n.status)+'</dd>'+
      '<dt>worker</dt><dd>'+esc(n.worker||"—")+'</dd>'+
      '<dt>reviewer</dt><dd>'+esc(n.reviewer||"—")+'</dd>'+
      '<dt>work_item_id</dt><dd>'+esc(n.work_item_id||"—")+'</dd>'+
      '<dt>pr_url</dt><dd>'+(n.pr_url?'<a class="ext" href="'+esc(n.pr_url)+'" target="_blank" rel="noopener">'+esc(n.pr_url)+'</a>':"—")+'</dd>'+
      '<dt>'+copy("dependencies")+'</dt><dd>'+((n.blocked_by||[]).join(", ")||copy("none"))+'</dd>'+
    '</dl>'+
    '<p class="empty" id="detail-extra">'+copy("loading_evidence")+'</p>';
  try{
    const full = await api("/api/node/"+encodeURIComponent(key)+"?manifest="+encodeURIComponent(manifest));
    if(state.detailGeneration !== detailGeneration || state.selected !== key || state.current !== manifest) return;
    const c = full.contract||{};
    const ev = full.evidence;
    $("detail-extra").outerHTML =
      '<h3 class="region-title section-title">Contract</h3>'+
      '<dl class="kv">'+
        '<dt>objective</dt><dd>'+esc(c.objective||"—")+'</dd>'+
        '<dt>pr_base</dt><dd>'+esc(c.pr_base||"—")+'</dd>'+
        '<dt>non_goals</dt><dd>'+sub(c.non_goals)+'</dd>'+
        '<dt>acceptance</dt><dd>'+sub(c.acceptance)+'</dd>'+
        '<dt>verification_commands</dt><dd>'+sub(c.verification_commands)+'</dd>'+
        '<dt>integration_gates</dt><dd>'+sub(c.integration_gates)+'</dd>'+
      '</dl>'+
      '<h3 class="region-title section-title">'+copy("evidence_chain")+'</h3>'+
      (ev ? '<dl class="kv">'+
        '<dt>work_item_id</dt><dd>'+esc(ev.work_item_id||"—")+'</dd>'+
        '<dt>platform_status</dt><dd>'+esc(ev.platform_status||"—")+'</dd>'+
        '<dt>review_verdict</dt><dd>'+esc(ev.review_verdict||"—")+'</dd>'+
        '<dt>review_comment</dt><dd>'+esc(ev.review_comment||"—")+'</dd>'+
        '<dt>has verification</dt><dd>'+(ev.verification? "✓":"—")+'</dd>'+
        '<dt>artifacts</dt><dd><pre class="contract">'+esc(JSON.stringify(ev.artifacts||{},null,2))+'</pre></dd>'+
      '</dl>' : '<p class="empty">'+copy("no_evidence")+'</p>')+
      '<p class="detail-meta">'+copy("bounce_count")+': '+(full.rollback_count!=null?full.rollback_count:"—")+' · '+esc(full.comments||"")+'</p>'+
      '<div class="copy-row copy-row-spaced"><code id="retry-cmd">omac node retry '+esc(state.current)+' '+esc(key)+'</code><button data-copy="retry-cmd">'+copy("copy_retry")+'</button></div>'+
      '<div class="copy-row"><code id="abandon-cmd">omac node abandon '+esc(state.current)+' '+esc(key)+'</code><button data-copy="abandon-cmd">'+copy("copy_abandon")+'</button></div>';
    wireCopy();
  }catch(e){
    if(state.detailGeneration !== detailGeneration || state.selected !== key || state.current !== manifest) return;
    $("detail-extra").outerHTML = '<p class="empty">'+copy("detail_failed")+': '+esc(e.message)+'</p>';
  }
}

/* ---------- 异常面板 ---------- */
function renderAnomaly(s){
  const nd = s.needs_decision;
  const wrap = $("anomaly-content");
  $("anomaly-empty").classList.toggle("is-hidden", !!nd);
  if(!nd){ wrap.innerHTML=""; return; }
  const card = (n) =>
    '<div class="anomaly-card '+(n.status==="blocked"?"blocked":"")+'">'+
      '<span class="state hl-'+esc(n.status)+'">'+stateLabel(n.status)+' · '+esc(n.key)+'</span>'+
      '<div class="reason">'+copy("reason")+': '+esc(n.reason||"—")+'</div>'+
      (n.pr_url?'<div class="small">PR: <a class="ext" href="'+esc(n.pr_url)+'" target="_blank" rel="noopener">'+esc(n.pr_url)+'</a></div>':"")+
      (n.work_item_id?'<div class="muted">work_item_id: '+esc(n.work_item_id)+'</div>':"")+
      (n.evidence_summary?'<div class="muted">review_verdict: '+esc(n.evidence_summary.review_verdict||"—")+'</div>':"")+
    '</div>';
  wrap.innerHTML =
    (nd.failed_nodes||[]).map(card).join("") +
    ((nd.blocked_downstream&&nd.blocked_downstream.length)?
      '<div class="anomaly-card blocked"><span class="state">'+copy("blocked_downstream")+'</span><div class="reason">'+esc((nd.blocked_downstream||[]).join(", "))+'</div></div>'
    :"") +
    (nd.next_actions||[]).map((a, i) =>
      '<div class="copy-row"><code id="na-'+i+'">'+esc(a)+'</code>'+
      '<button data-copy="na-'+i+'">'+copy("copy")+'</button></div>'
    ).join("");
  wireCopy();
}
function wireCopy(){
  document.querySelectorAll("[data-copy]").forEach(btn=>{
    const src = $(btn.dataset.copy);
    if(!src) return;
    btn.addEventListener("click", async ()=>{
      try { await navigator.clipboard.writeText(src.textContent); toast(copy("copied")); }
      catch(e){ toast(copy("copy_failed")+": "+e.message, true); }
    });
  });
}

/* ---------- 静态信息页 ---------- */
async function loadStatic(){
  if(!state.current) return;
  const wrap=$("static-content");
  wrap.innerHTML='<p class="empty">'+copy("loading_static")+'</p>';
  try{
    const [cfg, acc] = await Promise.all([
      api("/api/config"),
      api("/api/plan/acceptance?manifest="+encodeURIComponent(state.current)),
    ]);
    const meta = acc._meta||{};
    const manifestYaml = state.manifests.find(m=>m.path===state.current);
    wrap.innerHTML =
      '<h3 class="region-title">'+copy("config_summary")+'</h3>'+
      '<dl class="kv">'+
        '<dt>engine</dt><dd>'+esc((cfg.engine||cfg.defaults&&cfg.defaults))+'</dd>'+
      '</dl>'+
      '<pre class="contract">'+esc(JSON.stringify(cfg,null,2))+'</pre>'+
      '<h3 class="region-title section-title">'+copy("acceptance_checks")+'</h3>'+
      (meta.found===false ?
        '<p class="empty">'+copy("acceptance_missing")+': '+esc(meta.acceptance_file||"—")+'</p>'
        : '<ul class="acceptance">'+((acc.flows||[]).map(f=>"<li><b>"+esc(f.id||"?")+"</b> · "+esc(f.name||"")+"</li>").join("") || '<span class="empty">'+copy("none")+'</span>')+'</ul>'
      );
    wireCopy();
  }catch(e){
    wrap.innerHTML='<p class="empty">'+copy("static_failed")+': '+esc(e.message)+'</p>';
  }
}
$("reload-static").addEventListener("click", loadStatic);
$("tab-static").addEventListener("change", e=>{ if(e.target.checked) loadStatic(); });

/* ---------- 标签页切换 ---------- */
document.querySelectorAll("footer.tabs input[name=tab]").forEach(r=>{
  r.addEventListener("change", e=>{
    document.querySelectorAll(".tab-body").forEach(b=>{
      b.classList.toggle("active", b.dataset.tab===e.target.id);
    });
  });
});

/* ---------- meta + 启动 ---------- */
async function init(){
  applyLanguage("en");
  try{
    const meta = await api("/api/meta");
    state.refresh = meta.refresh||10;
  }catch(e){ /* 缺 meta 不影响主流程 */ }
  try{
    const config = await api("/api/config");
    applyLanguage(config.language||"en");
  }catch(e){ /* 配置不可用时保留英文默认值 */ }
  await loadManifests();
}
init();
})();
