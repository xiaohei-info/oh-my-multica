(function(global){
"use strict";

const DEFAULT_VISIBLE_DEPTH = 3;
const EXPAND_BATCH = 8;
const DEFAULT_NODE_BUDGET = 60;
const DEFAULT_EDGE_BUDGET = 240;
const ROOT_AGGREGATE = "@roots";
const ACTIVE_STATES = new Set([
  "in_progress", "running", "ci_check", "in_review", "review", "rework",
  "merging",
]);
const ANOMALY_STATES = new Set(["failed", "blocked"]);
const AUTO_REVEAL_STATES = new Set([...ACTIVE_STATES, "failed"]);

const statePresentation = {
  todo: {icon:"•", marker:"neutral", strong_border:false, safe_fallback:false},
  pending: {icon:"○", marker:"pending", strong_border:false, safe_fallback:false},
  in_progress: {icon:"▶", marker:"running", strong_border:false, safe_fallback:false},
  running: {icon:"▶", marker:"running", strong_border:false, safe_fallback:false},
  ci_check: {icon:"CI", marker:"ci", strong_border:false, safe_fallback:false},
  in_review: {icon:"◉", marker:"review", strong_border:false, safe_fallback:false},
  review: {icon:"◉", marker:"review", strong_border:false, safe_fallback:false},
  rework: {icon:"↺", marker:"rework", strong_border:false, safe_fallback:false},
  merging: {icon:"⇄", marker:"merge", strong_border:false, safe_fallback:false},
  done: {icon:"✓", marker:"check", strong_border:false, safe_fallback:false},
  failed: {icon:"✕", marker:"error", strong_border:true, safe_fallback:false},
  blocked: {icon:"🔒", marker:"lock", strong_border:false, safe_fallback:false},
  abandoned: {icon:"⊘", marker:"abandoned", strong_border:false, safe_fallback:false},
};

function buildGraph(nodes){
  const byNodeKey = {};
  (nodes || []).forEach(node => {
    if(node && typeof node.key === "string") byNodeKey[node.key] = node;
  });
  const keys = Object.keys(byNodeKey).sort();
  const dependencies = {};
  const children = {};
  const unknownDependencies = [];
  keys.forEach(key => { children[key] = []; });
  keys.forEach(key => {
    const declared = Array.isArray(byNodeKey[key].blocked_by)
      ? byNodeKey[key].blocked_by : [];
    dependencies[key] = declared.filter(dep => {
      const known = typeof dep === "string"
        && Object.prototype.hasOwnProperty.call(byNodeKey, dep);
      if(!known) unknownDependencies.push({node:key, dependency:String(dep)});
      return known;
    }).slice().sort();
    dependencies[key].forEach(dep => children[dep].push(key));
  });
  keys.forEach(key => children[key].sort());
  unknownDependencies.sort((left, right) =>
    left.node.localeCompare(right.node) || left.dependency.localeCompare(right.dependency));
  return {byNodeKey, keys, dependencies, children, unknownDependencies};
}

function cycleNodes(graph){
  const states = {};
  const stack = [];
  const result = new Set();
  const visit = key => {
    if(states[key] === "done") return;
    if(states[key] === "visiting") {
      stack.slice(stack.indexOf(key)).forEach(node => result.add(node));
      return;
    }
    states[key] = "visiting";
    stack.push(key);
    graph.dependencies[key].forEach(visit);
    stack.pop();
    states[key] = "done";
  };
  graph.keys.forEach(visit);
  return [...result].sort();
}

function graphError(graph){
  if(graph.unknownDependencies.length) {
    return {code:"unknown_dependency", dependencies:graph.unknownDependencies};
  }
  const nodes = cycleNodes(graph);
  return nodes.length ? {code:"cycle", nodes} : null;
}

function invalidProjection(error){
  return {depths:{}, nodes:[], edges:[], aggregates:[], error};
}

function computeDepths(graph){
  const depths = {};
  const visiting = new Set();
  function visit(key){
    if(depths[key] !== undefined) return depths[key];
    if(visiting.has(key)) return 1;
    visiting.add(key);
    const parentDepth = graph.dependencies[key].reduce(
      (maximum, dependency) => Math.max(maximum, visit(dependency)), 0);
    visiting.delete(key);
    depths[key] = parentDepth + 1;
    return depths[key];
  }
  graph.keys.forEach(visit);
  return depths;
}

function ancestorKeys(graph, initialKeys){
  const result = new Set();
  const visit = key => {
    if(result.has(key) || !graph.byNodeKey[key]) return;
    result.add(key);
    graph.dependencies[key].forEach(visit);
  };
  initialKeys.forEach(visit);
  return result;
}

function statusSummary(graph, keys){
  const counts = {};
  keys.forEach(key => {
    const status = graph.byNodeKey[key].status || "unknown";
    counts[status] = (counts[status] || 0) + 1;
  });
  return Object.keys(counts).sort().map(status => ({status, count: counts[status]}));
}

function aggregateToken(sources){
  return sources.length ? "@deps:"+JSON.stringify(sources) : ROOT_AGGREGATE;
}

function hiddenFrontierGroups(graph, visible){
  // Only nodes whose direct dependencies are visible belong in an aggregate.
  // This keeps every hidden node in at most one exact dependency signature.
  const groups = {};
  graph.keys.forEach(key => {
    if(visible.has(key)) return;
    const sources = graph.dependencies[key];
    if(sources.some(source => !visible.has(source))) return;
    const token = aggregateToken(sources);
    if(!groups[token]) groups[token] = {token, sources:sources.slice(), hidden_keys:[]};
    groups[token].hidden_keys.push(key);
  });
  return Object.values(groups)
    .map(group => ({
      ...group,
      hidden_count: group.hidden_keys.length,
      status_summary: statusSummary(graph, group.hidden_keys),
    }))
    .sort((left, right) => left.token.localeCompare(right.token));
}

function focusKeys(graph, focus){
  const states = focus === "active" ? ACTIVE_STATES : ANOMALY_STATES;
  return graph.keys.filter(key => states.has(graph.byNodeKey[key].status));
}

function keysInStates(graph, states){
  return graph.keys.filter(key => states.has(graph.byNodeKey[key].status));
}

function edgeCount(graph, visible){
  let count = 0;
  visible.forEach(key => {
    graph.dependencies[key].forEach(dependency => {
      if(visible.has(dependency)) count += 1;
    });
  });
  return count;
}

function orderedKeys(keys, depths){
  return [...new Set(keys)].sort((left, right) =>
    depths[left] - depths[right] || left.localeCompare(right));
}

function addVisible(graph, depths, visible, keys, limits, maxAdded){
  let added = 0;
  let truncated = false;
  orderedKeys(keys, depths).forEach(key => {
    if(visible.has(key)) return;
    if(added >= maxAdded) return;
    if(visible.size >= limits.nodeLimit) {
      truncated = true;
      return;
    }
    if(graph.dependencies[key].some(dependency => !visible.has(dependency))) return;
    visible.add(key);
    if(edgeCount(graph, visible) > limits.edgeLimit) {
      visible.delete(key);
      truncated = true;
      return;
    }
    added += 1;
  });
  return {added, truncated};
}

function projectDag(nodes, options){
  const graph = buildGraph(nodes);
  const error = graphError(graph);
  if(error) return invalidProjection(error);
  const depths = computeDepths(graph);
  const settings = options || {};
  const focus = settings.focus || "default";
  const limits = {
    nodeLimit: settings.nodeBudget || DEFAULT_NODE_BUDGET,
    edgeLimit: settings.edgeBudget || DEFAULT_EDGE_BUDGET,
  };
  const visible = new Set();
  let budgetTruncated = false;

  const add = (keys, maxAdded=Number.POSITIVE_INFINITY) => {
    const result = addVisible(graph, depths, visible, keys, limits, maxAdded);
    budgetTruncated = budgetTruncated || result.truncated;
  };
  add(ancestorKeys(graph, settings.pinned || []));
  if(focus === "all") {
    add(graph.keys);
  } else if(focus === "active" || focus === "anomaly") {
    add(ancestorKeys(graph, focusKeys(graph, focus)));
  } else {
    const activePaths = ancestorKeys(graph, keysInStates(graph, AUTO_REVEAL_STATES));
    add(activePaths);
    add(graph.keys.filter(key => depths[key] <= DEFAULT_VISIBLE_DEPTH));
  }

  (settings.expanded || []).forEach(source => {
    const groups = hiddenFrontierGroups(graph, visible);
    const matching = groups.filter(group => group.token === source);
    add(matching.flatMap(group => group.hidden_keys), EXPAND_BATCH);
  });

  const visibleNodes = graph.keys
    .filter(key => visible.has(key))
    .map(key => graph.byNodeKey[key])
    .sort((left, right) => depths[left.key] - depths[right.key] || left.key.localeCompare(right.key));
  const edges = [];
  visibleNodes.forEach(node => {
    graph.dependencies[node.key].forEach(from => {
      if(visible.has(from)) edges.push({from, to: node.key});
    });
  });
  edges.sort((left, right) => left.from.localeCompare(right.from) || left.to.localeCompare(right.to));
  const aggregates = hiddenFrontierGroups(graph, visible);
  return {
    depths, nodes: visibleNodes, edges, aggregates,
    budget: {
      node_limit: limits.nodeLimit,
      edge_limit: limits.edgeLimit,
      visible_nodes: visibleNodes.length,
      visible_edges: edges.length,
      truncated: budgetTruncated,
    },
  };
}

function barycenter(keys, order){
  if(!keys.length) return Number.POSITIVE_INFINITY;
  return keys.reduce((sum, key) => sum + (order[key] ?? 0), 0) / keys.length;
}

function layoutDag(projection, options){
  const settings = options || {};
  const colW = settings.colW || 230;
  const rowH = settings.rowH || 88;
  const padX = settings.padX || 48;
  const padY = settings.padY || 56;
  const nodeWidth = settings.nodeWidth || 160;
  const nodeHeight = settings.nodeHeight || 60;
  const aggregateWidth = settings.aggregateWidth || 170;
  const aggregateHeight = settings.aggregateHeight || 44;
  const nodes = projection.nodes || [];
  const depths = projection.depths || {};
  const groups = {};
  nodes.forEach(node => {
    const depth = depths[node.key];
    (groups[depth] = groups[depth] || []).push(node.key);
  });
  Object.values(groups).forEach(group => group.sort());
  const incoming = {};
  const outgoing = {};
  nodes.forEach(node => { incoming[node.key] = []; outgoing[node.key] = []; });
  (projection.edges || []).forEach(edge => {
    incoming[edge.to].push(edge.from);
    outgoing[edge.from].push(edge.to);
  });
  const depthKeys = Object.keys(groups).map(Number).sort((left, right) => left - right);
  const orderMap = () => {
    const result = {};
    depthKeys.forEach(depth => groups[depth].forEach((key, index) => { result[key] = index; }));
    return result;
  };
  for(let pass=0; pass<4; pass += 1) {
    let order = orderMap();
    depthKeys.slice(1).forEach(depth => {
      groups[depth].sort((left, right) =>
        barycenter(incoming[left], order) - barycenter(incoming[right], order)
        || order[left] - order[right] || left.localeCompare(right));
    });
    order = orderMap();
    depthKeys.slice(0, -1).reverse().forEach(depth => {
      groups[depth].sort((left, right) =>
        barycenter(outgoing[left], order) - barycenter(outgoing[right], order)
        || order[left] - order[right] || left.localeCompare(right));
    });
  }

  const pos = {};
  const occupied = {};
  depthKeys.forEach(depth => {
    occupied[depth] = new Set();
    groups[depth].forEach((key, index) => {
      occupied[depth].add(index);
      pos[key] = {x: padX + (depth - 1) * colW, y: padY + index * rowH};
    });
  });

  const aggregates = projection.aggregates || [];
  const aggregatePositions = {};
  aggregates.forEach(aggregate => {
    const sources = aggregate.sources || [];
    const targetDepth = sources.length
      ? Math.max(...sources.map(source => depths[source] || 0)) + 1 : 1;
    occupied[targetDepth] = occupied[targetDepth] || new Set();
    const preferred = sources.length
      ? Math.max(0, Math.round(
        (sources.reduce((sum, source) => sum + pos[source].y, 0)
        / sources.length - padY) / rowH))
      : 0;
    let row = preferred;
    for(let distance=0; occupied[targetDepth].has(row); distance += 1) {
      const before = preferred - distance - 1;
      const after = preferred + distance + 1;
      row = before >= 0 && !occupied[targetDepth].has(before) ? before : after;
    }
    occupied[targetDepth].add(row);
    aggregatePositions[aggregate.token] = {
      x: padX + (targetDepth - 1) * colW,
      y: padY + row * rowH,
    };
  });

  const allBoxes = [
    ...Object.values(pos).map(point => ({...point, width:nodeWidth, height:nodeHeight})),
    ...Object.values(aggregatePositions).map(point => ({...point, width:aggregateWidth, height:aggregateHeight})),
  ];
  const W = allBoxes.reduce((maximum, box) => Math.max(maximum, box.x + box.width + padX), 400);
  const H = allBoxes.reduce((maximum, box) => Math.max(maximum, box.y + box.height + padY), 200);
  return {
    pos, aggregatePositions, W, H, colW, rowH, padX, padY,
    nodeWidth, nodeHeight, aggregateWidth, aggregateHeight,
  };
}

function collapseBranches(){ return []; }

function presentState(status){
  if(statePresentation[status]) return statePresentation[status];
  return {
    icon:"?", marker:"unknown", strong_border:false,
    safe_fallback:true, status: status || "unknown",
  };
}

function directRelations(nodes, key){
  const graph = buildGraph(nodes);
  return {
    upstream: (graph.dependencies[key] || []).slice(),
    downstream: (graph.children[key] || []).slice(),
  };
}

global.OMACDag = {
  DEFAULT_VISIBLE_DEPTH,
  EXPAND_BATCH,
  DEFAULT_NODE_BUDGET,
  DEFAULT_EDGE_BUDGET,
  ROOT_AGGREGATE,
  statePresentation,
  presentState,
  collapseBranches,
  directRelations,
  layoutDag,
  projectDag,
};
})(globalThis);
