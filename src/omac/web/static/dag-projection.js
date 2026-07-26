(function(global){
"use strict";

const DEFAULT_VISIBLE_DEPTH = 3;
const ACTIVE_STATES = new Set(["in_progress", "ci_check", "in_review", "merging"]);
const ANOMALY_STATES = new Set(["failed", "blocked"]);
const IMPORTANT_STATES = new Set([...ACTIVE_STATES, ...ANOMALY_STATES]);

const statePresentation = {
  todo: {icon:"•", marker:"neutral", strong_border:false, safe_fallback:false},
  in_progress: {icon:"▶", marker:"running", strong_border:false, safe_fallback:false},
  ci_check: {icon:"CI", marker:"ci", strong_border:false, safe_fallback:false},
  in_review: {icon:"◉", marker:"review", strong_border:false, safe_fallback:false},
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

function revealClosure(graph, source){
  return ancestorKeys(graph, graph.children[source] || []);
}

function hiddenRevealClosure(graph, source, visible){
  return new Set([...revealClosure(graph, source)].filter(key => !visible.has(key)));
}

function statusSummary(graph, keys){
  const counts = {};
  keys.forEach(key => {
    const status = graph.byNodeKey[key].status || "unknown";
    counts[status] = (counts[status] || 0) + 1;
  });
  return Object.keys(counts).sort().map(status => ({status, count: counts[status]}));
}

function focusKeys(graph, focus){
  const states = focus === "active" ? ACTIVE_STATES : ANOMALY_STATES;
  return graph.keys.filter(key => states.has(graph.byNodeKey[key].status));
}

function projectDag(nodes, options){
  const graph = buildGraph(nodes);
  const error = graphError(graph);
  if(error) return invalidProjection(error);
  const depths = computeDepths(graph);
  const settings = options || {};
  const focus = settings.focus || "default";
  let visible;
  if(focus === "all") {
    visible = new Set(graph.keys);
  } else if(focus === "active" || focus === "anomaly") {
    visible = ancestorKeys(graph, focusKeys(graph, focus));
  } else {
    visible = new Set(graph.keys.filter(key => depths[key] <= DEFAULT_VISIBLE_DEPTH));
    ancestorKeys(graph, graph.keys.filter(key => IMPORTANT_STATES.has(graph.byNodeKey[key].status)))
      .forEach(key => visible.add(key));
  }

  (settings.expanded || []).forEach(source => {
    if(!visible.has(source) || !graph.children[source]) return;
    revealClosure(graph, source).forEach(key => visible.add(key));
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
  const aggregates = visibleNodes.reduce((result, node) => {
    const hidden = hiddenRevealClosure(graph, node.key, visible);
    if(!hidden.size) return result;
    result.push({
      source: node.key,
      hidden_count: hidden.size,
      status_summary: statusSummary(graph, hidden),
    });
    return result;
  }, []);
  return {depths, nodes: visibleNodes, edges, aggregates};
}

function layoutDag(projection, options){
  const settings = options || {};
  const colW = settings.colW || 200;
  const rowH = settings.rowH || 84;
  const padX = settings.padX || 48;
  const padY = settings.padY || 56;
  const nodeWidth = settings.nodeWidth || 120;
  const nodeHeight = settings.nodeHeight || 56;
  const aggregateWidth = settings.aggregateWidth || 150;
  const aggregateHeight = settings.aggregateHeight || 40;
  const aggregateGap = settings.aggregateGap || 12;
  const nodes = projection.nodes || [];
  const depths = projection.depths || {};
  const groups = {};
  nodes.forEach(node => {
    const depth = depths[node.key];
    (groups[depth] = groups[depth] || []).push(node);
  });
  Object.values(groups).forEach(group => group.sort((left, right) =>
    left.key.localeCompare(right.key)));

  const pos = {};
  let maxRow = 0;
  Object.keys(groups).sort((left, right) => +left - +right).forEach(depth => {
    const row = groups[depth];
    maxRow = Math.max(maxRow, row.length);
    row.forEach((node, index) => {
      pos[node.key] = {x: padX + (+depth - 1) * colW, y: padY + index * rowH};
    });
  });

  const maxVisibleDepth = nodes.reduce((maximum, node) =>
    Math.max(maximum, depths[node.key] || 1), 1);
  const aggregates = projection.aggregates || [];
  const aggregatePositions = {};
  const aggregateY = padY + maxRow * rowH + aggregateGap;
  aggregates.forEach((aggregate, index) => {
    aggregatePositions[aggregate.source] = {
      x: padX + index * (aggregateWidth + aggregateGap), y: aggregateY,
    };
  });
  const graphWidth = padX * 2 + maxVisibleDepth * colW;
  const aggregateWidthTotal = aggregates.length
    ? padX * 2 + aggregates.length * aggregateWidth
      + (aggregates.length - 1) * aggregateGap
    : 0;
  const W = Math.max(graphWidth, aggregateWidthTotal);
  const H = aggregates.length
    ? aggregateY + aggregateHeight + padY
    : padY * 2 + maxRow * rowH;
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

global.OMACDag = {
  DEFAULT_VISIBLE_DEPTH,
  statePresentation,
  presentState,
  collapseBranches,
  layoutDag,
  projectDag,
};
})(globalThis);
