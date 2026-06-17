<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import type { GraphResponse } from '@/types/api';

interface CanvasNode {
  id: string;
  label: string;
  entityType: string;
  color: string;
  size: number;
  weight: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  fx: number | null;
  fy: number | null;
}

interface CanvasEdge {
  source: string;
  target: string;
  label: string;
  weight: number;
}

interface HoverNode {
  id: string;
  label: string;
  entityType: string;
}

interface ViewTransform {
  x: number;
  y: number;
  scale: number;
}

interface GraphBounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  width: number;
  height: number;
  centerX: number;
  centerY: number;
}

type ViewMode = 'global' | 'local';

const props = defineProps<{
  graph: GraphResponse;
  selectedNodeId?: string;
  focusNodeId?: string;
  mode?: 'full' | 'label';
  viewportMode?: ViewMode;
  currentRootLabel?: string;
}>();

const emit = defineEmits<{
  select: [label: string];
  hover: [node: HoverNode | null];
}>();

const containerRef = ref<HTMLDivElement | null>(null);
const canvasRef = ref<HTMLCanvasElement | null>(null);

const transform = ref<ViewTransform>({
  x: 0,
  y: 0,
  scale: 1
});

const entityColors: Record<string, string> = {
  EQUIPMENT: '#3B82F6',
  COMPONENT: '#7EC8E3',
  FAULTCASE: '#F59E0B',
  FAULT_PROPERTY: '#10B981',
  FAULT: '#EF4444',
  CAUSE: '#FB7185',
  ACTION: '#10B981',
  FAULTCODE: '#9333EA',
  CHUNK: '#94A3B8',
  UNKNOWN: '#64748B'
};

const nodes: CanvasNode[] = [];
const edges: CanvasEdge[] = [];
const dragState = {
  node: null as CanvasNode | null,
  isPanning: false,
  didMove: false,
  suppressClick: false,
  startX: 0,
  startY: 0,
  originX: 0,
  originY: 0
};

let resizeObserver: ResizeObserver | null = null;
let animationFrameId = 0;
let hoveredNode: CanvasNode | null = null;
let previousAcceptedLabelIds = new Set<string>();
const labelAnchorCache = new Map<string, { x: number; y: number }>();
const labelTextCache = new Map<string, { text: string; width: number }>();
const savedTransforms: Record<ViewMode, ViewTransform | null> = {
  global: null,
  local: null
};
const baselineTransforms: Record<ViewMode, ViewTransform | null> = {
  global: null,
  local: null
};

type GlobalCameraIntent = 'reset' | 'replay';

const globalResetViewport = {
  fill: 1.56,
  minScale: 0.5,
  maxScale: 2.14,
  minWidth: 300,
  minHeight: 240,
  padding: 36
} as const;

const globalReplayViewport = {
  fill: 1.24,
  minScale: 0.44,
  maxScale: 2.04,
  minWidth: 320,
  minHeight: 260,
  padding: 60
} as const;

let targetTransform: ViewTransform | null = null;
let lastLocalFocusNodeKey: string | null = null;
let simulationActive = false;
let simulationSettledFrames = 0;
let simulationProfile: 'default' | 'gentle' = 'default';
let lastCanvasWidth = 0;
let lastCanvasHeight = 0;
const dragThresholdSquared = 9;

function normalizeType(entityType: unknown) {
  return String(entityType ?? 'UNKNOWN').replace(/"/g, '').toUpperCase();
}

function resolveNodeId(node: GraphResponse['nodes'][number], index: number) {
  return String(node.id ?? node.labels?.[0] ?? `未知节点-${index}`);
}

function resolveNodeLabel(node: GraphResponse['nodes'][number], index: number) {
  return String(node.labels?.[0] ?? node.id ?? `未知节点-${index}`);
}

function resolveSelectedNodeKey(selectedNodeId?: string) {
  if (!selectedNodeId) {
    return null;
  }

  const selectedNode = nodes.find(
    (node) => node.id === selectedNodeId || node.label === selectedNodeId
  );
  return selectedNode?.id ?? selectedNodeId;
}

function resolveSelectedNodeAliases(selectedNodeId?: string) {
  const aliases = new Set<string>();
  if (!selectedNodeId) {
    return aliases;
  }

  aliases.add(selectedNodeId);
  const selectedNode = nodes.find(
    (node) => node.id === selectedNodeId || node.label === selectedNodeId
  );
  if (selectedNode) {
    aliases.add(selectedNode.id);
    aliases.add(selectedNode.label);
  }
  return aliases;
}

function resolveFocusNodeKey(focusNodeId?: string) {
  return resolveSelectedNodeKey(focusNodeId ?? props.selectedNodeId);
}

function resolveFocusNodeAliases(focusNodeId?: string) {
  return resolveSelectedNodeAliases(focusNodeId ?? props.selectedNodeId);
}

function currentViewMode(): ViewMode {
  return props.viewportMode ?? 'global';
}

function cloneTransform(view: ViewTransform): ViewTransform {
  return {
    x: view.x,
    y: view.y,
    scale: view.scale
  };
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function persistTransform(viewMode = currentViewMode(), view = transform.value) {
  savedTransforms[viewMode] = cloneTransform(view);
}

function persistBaselineTransform(viewMode: ViewMode, view: ViewTransform | null) {
  baselineTransforms[viewMode] = view ? cloneTransform(view) : null;
}

function buildNodeLookup() {
  const lookup = new Map<string, CanvasNode>();
  nodes.forEach((node) => {
    lookup.set(node.id, node);
    lookup.set(node.label, node);
  });
  return lookup;
}

function getGraphBounds(targetNodes = nodes): GraphBounds | null {
  if (!targetNodes.length) {
    return null;
  }

  let minX = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;

  targetNodes.forEach((node) => {
    minX = Math.min(minX, node.x);
    maxX = Math.max(maxX, node.x);
    minY = Math.min(minY, node.y);
    maxY = Math.max(maxY, node.y);
  });

  return {
    minX,
    maxX,
    minY,
    maxY,
    width: maxX - minX,
    height: maxY - minY,
    centerX: (minX + maxX) / 2,
    centerY: (minY + maxY) / 2
  };
}

function buildTransformFromBounds(
  bounds: GraphBounds | null,
  options: {
    fill?: number;
    minScale?: number;
    maxScale?: number;
    minWidth?: number;
    minHeight?: number;
    padding?: number;
  } = {}
) {
  const container = containerRef.value;
  if (!container || !bounds) {
    return null;
  }

  const padding = options.padding ?? 72;
  const fitWidth = Math.max(bounds.width + padding * 2, options.minWidth ?? 240);
  const fitHeight = Math.max(bounds.height + padding * 2, options.minHeight ?? 220);
  const unclampedScale = Math.min(
    (container.clientWidth * (options.fill ?? 0.84)) / fitWidth,
    (container.clientHeight * (options.fill ?? 0.84)) / fitHeight
  );
  const scale = clamp(unclampedScale, options.minScale ?? 0.24, options.maxScale ?? 2.2);

  return {
    x: container.clientWidth / 2 - bounds.centerX * scale,
    y: container.clientHeight / 2 - bounds.centerY * scale,
    scale
  };
}

function highlightedNodeIds(selectedNodeId = props.selectedNodeId) {
  const aliases = resolveSelectedNodeAliases(selectedNodeId);
  if (!aliases.size) {
    return null;
  }

  const nodeLookup = buildNodeLookup();
  const connected = new Set<string>();

  aliases.forEach((alias) => {
    const node = nodeLookup.get(alias);
    if (node) {
      connected.add(node.id);
    }
  });

  edges.forEach((edge) => {
    if (aliases.has(edge.source)) {
      const targetNode = nodeLookup.get(edge.target);
      if (targetNode) {
        connected.add(targetNode.id);
      }
    }
    if (aliases.has(edge.target)) {
      const sourceNode = nodeLookup.get(edge.source);
      if (sourceNode) {
        connected.add(sourceNode.id);
      }
    }
  });

  return connected.size ? connected : null;
}

function focusNeighborhoodIds(focusNodeId = props.focusNodeId ?? props.selectedNodeId) {
  const aliases = resolveFocusNodeAliases(focusNodeId);
  if (!aliases.size) {
    return null;
  }

  const nodeLookup = buildNodeLookup();
  const connected = new Set<string>();

  aliases.forEach((alias) => {
    const node = nodeLookup.get(alias);
    if (node) {
      connected.add(node.id);
    }
  });

  edges.forEach((edge) => {
    if (aliases.has(edge.source)) {
      const targetNode = nodeLookup.get(edge.target);
      if (targetNode) {
        connected.add(targetNode.id);
      }
    }
    if (aliases.has(edge.target)) {
      const sourceNode = nodeLookup.get(edge.source);
      if (sourceNode) {
        connected.add(sourceNode.id);
      }
    }
  });

  return connected.size ? connected : null;
}

function localFocusNodes() {
  const focusIds = focusNeighborhoodIds();
  if (!focusIds?.size) {
    return [];
  }
  return nodes.filter((node) => focusIds.has(node.id));
}

function computedDefaultTransformFor(
  viewMode = currentViewMode(),
  cameraIntent: GlobalCameraIntent = 'reset'
) {
  if (!nodes.length) {
    return null;
  }

  if (viewMode === 'local') {
    const focusNodes = localFocusNodes();
    return buildTransformFromBounds(getGraphBounds(focusNodes.length ? focusNodes : nodes), {
      fill: 0.72,
      minScale: 0.72,
      maxScale: 2.8,
      minWidth: 220,
      minHeight: 180,
      padding: 58
    });
  }

  const globalViewport = cameraIntent === 'replay' ? globalReplayViewport : globalResetViewport;
  return buildTransformFromBounds(getGraphBounds(), globalViewport);
}

function defaultTransformFor(
  viewMode = currentViewMode(),
  options: {
    fresh?: boolean;
    cameraIntent?: GlobalCameraIntent;
    persist?: boolean;
  } = {}
) {
  if (!nodes.length) {
    return null;
  }

  const cameraIntent = options.cameraIntent ?? 'reset';
  const baseline = baselineTransforms[viewMode];
  if (!options.fresh && cameraIntent === 'reset' && baseline) {
    return cloneTransform(baseline);
  }

  const nextTransform = computedDefaultTransformFor(viewMode, cameraIntent);
  if (options.persist ?? cameraIntent === 'reset') {
    persistBaselineTransform(viewMode, nextTransform);
  }
  return nextTransform;
}

function focusTransformFor(nodeKey = props.focusNodeId ?? props.selectedNodeId, viewMode = currentViewMode()) {
  if (!nodeKey || !nodes.length) {
    return null;
  }

  const nodeLookup = buildNodeLookup();
  const node = nodeLookup.get(nodeKey);
  const container = containerRef.value;
  if (!node || !container) {
    return null;
  }

  const minScale = viewMode === 'local' ? 0.72 : 0.42;
  const maxScale = viewMode === 'local' ? 2.8 : 2.6;
  const fallbackScale =
    savedTransforms[viewMode]?.scale ??
    baselineTransforms[viewMode]?.scale ??
    1;
  const currentScale =
    Number.isFinite(transform.value.scale) && transform.value.scale > 0
      ? transform.value.scale
      : fallbackScale;
  const baselineScale =
    baselineTransforms[viewMode]?.scale ??
    computedDefaultTransformFor(viewMode)?.scale ??
    fallbackScale;
  const focusScale =
    viewMode === 'global'
      ? Math.max(currentScale, baselineScale * 1.26, 0.62)
      : currentScale;
  const scale = clamp(focusScale, minScale, maxScale);
  return {
    x: container.clientWidth / 2 - node.x * scale,
    y: container.clientHeight / 2 - node.y * scale,
    scale
  };
}

function applyTransform(nextTransform: ViewTransform | null, options: { animate?: boolean; viewMode?: ViewMode } = {}) {
  if (!nextTransform) {
    return;
  }

  const viewMode = options.viewMode ?? currentViewMode();
  savedTransforms[viewMode] = cloneTransform(nextTransform);

  if (options.animate) {
    targetTransform = cloneTransform(nextTransform);
    return;
  }

  targetTransform = null;
  transform.value = cloneTransform(nextTransform);
  draw();
}

function rememberCurrentViewport() {
  persistTransform(currentViewMode(), targetTransform ?? transform.value);
}

function stepCamera() {
  if (!targetTransform) {
    return false;
  }

  const dx = targetTransform.x - transform.value.x;
  const dy = targetTransform.y - transform.value.y;
  const ds = targetTransform.scale - transform.value.scale;

  if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(ds) < 0.003) {
    transform.value = cloneTransform(targetTransform);
    persistTransform(currentViewMode(), targetTransform);
    targetTransform = null;
    return true;
  }

  transform.value = {
    x: transform.value.x + dx * 0.18,
    y: transform.value.y + dy * 0.18,
    scale: transform.value.scale + ds * 0.16
  };
  return true;
}

function restartSimulation(profile: 'default' | 'gentle' = 'default') {
  simulationProfile = profile;
  simulationSettledFrames = 0;
  simulationActive = nodes.length > 0;
  nodes.forEach((node) => {
    if (node.fx !== null || node.fy !== null) {
      node.vx = 0;
      node.vy = 0;
      return;
    }
    node.vx *= 0.6;
    node.vy *= 0.6;
  });
}

function seedNodePosition(index: number, total: number) {
  const width = containerRef.value?.clientWidth ?? 1200;
  const height = containerRef.value?.clientHeight ?? 760;
  const radius = Math.min(width, height) * 0.28;
  const jitter = 32;
  const angle = (index / Math.max(total, 1)) * Math.PI * 2;
  const offsetX = Math.sin(index * 12.9898 + total * 0.31) * jitter * 0.5;
  const offsetY = Math.cos(index * 78.233 + total * 0.17) * jitter * 0.5;

  return {
    x: radius * Math.cos(angle) + offsetX,
    y: radius * Math.sin(angle) + offsetY
  };
}

function buildGraphData() {
  const degreeMap = new Map<string, number>();
  const previousPositions = new Map(nodes.map((node) => [node.id, { x: node.x, y: node.y }]));

  props.graph.edges.forEach((edge) => {
    degreeMap.set(edge.source, (degreeMap.get(edge.source) ?? 0) + 1);
    degreeMap.set(edge.target, (degreeMap.get(edge.target) ?? 0) + 1);
  });

  nodes.splice(0, nodes.length);
  props.graph.nodes.forEach((node, index) => {
    const id = resolveNodeId(node, index);
    const label = resolveNodeLabel(node, index);
    const entityType = normalizeType(node.entity_type);
    const degree = degreeMap.get(id) ?? degreeMap.get(label) ?? 1;
    const previous = previousPositions.get(id) ?? previousPositions.get(label);
    const seeded = seedNodePosition(index, props.graph.nodes.length);
    const x = previous?.x ?? seeded.x;
    const y = previous?.y ?? seeded.y;

    nodes.push({
      id,
      label,
      entityType,
      color: entityColors[entityType] ?? entityColors.UNKNOWN,
      size: entityType === 'EQUIPMENT' ? 11 + Math.min(degree, 6) : 8 + Math.min(degree, 5),
      weight: degree,
      x,
      y,
      vx: 0,
      vy: 0,
      fx: null,
      fy: null
    });
  });

  edges.splice(0, edges.length);
  props.graph.edges.forEach((edge) => {
    edges.push({
      source: edge.source,
      target: edge.target,
      label: edge.type ?? '',
      weight: 1
    });
  });
}

function reseedNodePositions() {
  if (!nodes.length) {
    return;
  }

  nodes.forEach((node, index) => {
    const seeded = seedNodePosition(index, nodes.length);
    node.x = seeded.x;
    node.y = seeded.y;
    node.vx = 0;
    node.vy = 0;
    node.fx = null;
    node.fy = null;
  });

  const seededBounds = getGraphBounds(nodes);
  if (!seededBounds) {
    return;
  }

  nodes.forEach((node) => {
    node.x -= seededBounds.centerX;
    node.y -= seededBounds.centerY;
  });
}

function resizeCanvas() {
  if (!canvasRef.value || !containerRef.value) {
    return;
  }

  const rect = containerRef.value.getBoundingClientRect();
  const previousWidth = lastCanvasWidth;
  const previousHeight = lastCanvasHeight;
  const shouldPreserveCenter =
    previousWidth > 0 &&
    previousHeight > 0 &&
    Number.isFinite(transform.value.scale) &&
    transform.value.scale > 0;

  const centerGraph = shouldPreserveCenter
    ? {
        x: (previousWidth / 2 - transform.value.x) / transform.value.scale,
        y: (previousHeight / 2 - transform.value.y) / transform.value.scale
      }
    : null;

  const dpr = window.devicePixelRatio || 1;
  canvasRef.value.width = rect.width * dpr;
  canvasRef.value.height = rect.height * dpr;
  canvasRef.value.style.width = `${rect.width}px`;
  canvasRef.value.style.height = `${rect.height}px`;

  if (centerGraph) {
    transform.value = {
      x: rect.width / 2 - centerGraph.x * transform.value.scale,
      y: rect.height / 2 - centerGraph.y * transform.value.scale,
      scale: transform.value.scale
    };
  }

  lastCanvasWidth = rect.width;
  lastCanvasHeight = rect.height;
}

function getContext() {
  return canvasRef.value?.getContext('2d') ?? null;
}

function draw() {
  const ctx = getContext();
  const canvas = canvasRef.value;
  const container = containerRef.value;
  if (!ctx || !canvas || !container) {
    return;
  }

  const dpr = window.devicePixelRatio || 1;
  const width = container.clientWidth;
  const height = container.clientHeight;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  ctx.fillStyle = '#F0F7FF';
  ctx.fillRect(0, 0, width, height);

  ctx.save();
  ctx.translate(transform.value.x, transform.value.y);
  ctx.scale(transform.value.scale, transform.value.scale);

  const nodeMap = buildNodeLookup();
  const selectedId = resolveSelectedNodeKey(props.selectedNodeId);
  const connectedNodes = highlightedNodeIds();
  const focusedNodes = focusNeighborhoodIds();
  const isLocalView = currentViewMode() === 'local';
  const neighborhoodNodes = isLocalView ? focusedNodes : connectedNodes;

  const scale = transform.value.scale;
  const baseBudget = isLocalView ? 18 : props.mode === 'label' ? 96 : 56;
  const zoomTier = scale < 0.6 ? 'far' : scale < 1.1 ? 'mid' : 'near';
  const zoomMultiplier = zoomTier === 'far' ? 0.4 : zoomTier === 'mid' ? 0.9 : scale < 1.7 ? 1.8 : 2.8;
  const currentBudget = Math.floor(baseBudget * zoomMultiplier);
  const planningLimit = zoomTier === 'far'
    ? Math.max(currentBudget * 2, 24)
    : zoomTier === 'mid'
      ? Math.max(currentBudget * 2, 48)
      : Math.max(currentBudget * 3, 96);
  const allowOpportunisticLabels = zoomTier === 'near';

  const labelCandidates = nodes
    .filter((node) => {
      if (!isLocalView || !neighborhoodNodes?.size) {
        return true;
      }
      return (
        neighborhoodNodes.has(node.id) ||
        node.id === selectedId ||
        hoveredNode?.id === node.id
      );
    })
    .map((node) => {
    const isSelected = node.id === selectedId;
    const isHovered = hoveredNode?.id === node.id;
    const isRoot = props.currentRootLabel && node.label === props.currentRootLabel;
    const isForced = isSelected || isHovered || isRoot;
    const isConnected = neighborhoodNodes?.has(node.id);
    const wasAccepted = previousAcceptedLabelIds.has(node.id);
    
    let score = 0;
    if (isForced) score += 1000000;
    if (isConnected) score += 100000;
    if (wasAccepted) score += 25000;
    score += node.weight * 1000 + node.size;
    
      return { node, isForced, wasAccepted, score, id: node.id };
    });

  labelCandidates.sort((a, b) => {
    if (a.score !== b.score) return b.score - a.score;
    return a.id.localeCompare(b.id);
  });

  ctx.font = `500 ${11 / scale}px Inter, sans-serif`;
  const acceptedLabels: Array<{x: number, y: number, w: number, h: number, node: CanvasNode, text: string}> = [];
  let budgetUsed = 0;

  const drawRoundedRect = (x: number, y: number, w: number, h: number, r: number) => {
    const radius = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + w - radius, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + radius);
    ctx.lineTo(x + w, y + h - radius);
    ctx.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
    ctx.lineTo(x + radius, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - radius);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
    ctx.closePath();
  };

  const truncateLabel = (text: string, maxWidth: number) => {
    let width = ctx.measureText(text).width;
    if (width <= maxWidth) return text;
    let ellipsis = '...';
    let ellipsisWidth = ctx.measureText(ellipsis).width;
    if (maxWidth <= ellipsisWidth) return '';
    let result = text;
    while (result.length > 0 && ctx.measureText(result).width + ellipsisWidth > maxWidth) {
      result = result.slice(0, -1);
    }
    return result + ellipsis;
  };

  const snapToPixel = (value: number) => Math.round(value * scale) / scale;

  const resolveCachedLabelLayout = (node: CanvasNode, isSelected: boolean) => {
    const scaleKey = Math.round(scale * 100);
    const cacheKey = `${node.id}:${isSelected ? 1 : 0}:${scaleKey}`;
    const cached = labelTextCache.get(cacheKey);
    if (cached) {
      return cached;
    }

    ctx.font = `${isSelected ? 600 : 500} ${11 / scale}px Inter, sans-serif`;
    const maxWidth = 168 / scale;
    const truncatedText = truncateLabel(node.label, maxWidth);
    const width = truncatedText ? ctx.measureText(truncatedText).width : 0;
    const layout = { text: truncatedText, width };
    labelTextCache.set(cacheKey, layout);
    return layout;
  };

  for (const cand of labelCandidates.slice(0, planningLimit)) {
    const isOpportunistic = allowOpportunisticLabels && !cand.isForced && budgetUsed >= currentBudget;
    if (!allowOpportunisticLabels && !cand.isForced && budgetUsed >= currentBudget) {
      continue;
    }
    
    const node = cand.node;
    const isSelected = node.id === selectedId;
    const { text, width: textWidth } = resolveCachedLabelLayout(node, isSelected);
    if (!text) continue;

    const w = textWidth + 12 / scale;
    const h = 18 / scale;
    const radius = node.size * (isSelected ? 1.52 : (hoveredNode?.id === node.id) ? 1.18 : 1);
    const targetX = node.x - w / 2;
    const targetY = node.y + radius + 6 / scale;
    const previousAnchor = labelAnchorCache.get(node.id);
    const anchorDeadzone = 10 / scale;
    const easing = previousAnchor ? 0.18 : 1;

    let anchorX = targetX;
    let anchorY = targetY;
    if (previousAnchor) {
      const dx = targetX - previousAnchor.x;
      const dy = targetY - previousAnchor.y;
      if (Math.abs(dx) < anchorDeadzone && Math.abs(dy) < anchorDeadzone) {
        anchorX = previousAnchor.x + dx * easing;
        anchorY = previousAnchor.y + dy * easing;
      }
    }

    const x = snapToPixel(anchorX);
    const y = snapToPixel(anchorY);

    const pad = isOpportunistic ? (cand.wasAccepted ? 8 / scale : 14 / scale) : 0;
    let collides = false;
    for (const rect of acceptedLabels) {
      if (!(x - pad > rect.x + rect.w || x + w + pad < rect.x || y - pad > rect.y + rect.h || y + h + pad < rect.y)) {
        collides = true;
        break;
      }
    }

    if (!collides || cand.isForced) {
      acceptedLabels.push({ x, y, w, h, node, text });
      labelAnchorCache.set(node.id, { x, y });
      if (!cand.isForced && !isOpportunistic) {
        budgetUsed++;
      }
    }
  }

  previousAcceptedLabelIds = new Set(acceptedLabels.map((label) => label.node.id));
  const acceptedIds = new Set(acceptedLabels.map((label) => label.node.id));
  for (const cachedId of [...labelAnchorCache.keys()]) {
    if (!acceptedIds.has(cachedId)) {
      labelAnchorCache.delete(cachedId);
    }
  }

  edges.forEach((edge) => {
    const sourceNode = nodeMap.get(edge.source);
    const targetNode = nodeMap.get(edge.target);
    if (!sourceNode || !targetNode) {
      return;
    }

    const sourceMatchesSelected = selectedId ? sourceNode.id === selectedId : false;
    const targetMatchesSelected = selectedId ? targetNode.id === selectedId : false;
    const isActive = sourceMatchesSelected || targetMatchesSelected;
    const isEdgeSelected = isActive;
    const inFocus = Boolean(
      neighborhoodNodes?.has(sourceNode.id) && neighborhoodNodes?.has(targetNode.id)
    );
    const opacity = isLocalView
      ? isActive
        ? 0.96
        : inFocus
          ? 0.34
          : 0.12
      : selectedId
        ? isActive
          ? 0.88
          : 0.08
        : hoveredNode
          ? edge.source === hoveredNode.id || edge.target === hoveredNode.id
            ? 0.7
            : 0.16
          : 0.3;

    ctx.beginPath();
    ctx.moveTo(sourceNode.x, sourceNode.y);
    ctx.lineTo(targetNode.x, targetNode.y);
    ctx.strokeStyle = isActive ? `rgba(59,130,246,${opacity})` : `rgba(148,163,184,${opacity})`;
    ctx.lineWidth = isActive ? 1.7 : isLocalView && inFocus ? 1.1 : 0.9;
    if (isActive) {
      ctx.shadowBlur = 12;
      ctx.shadowColor = 'rgba(59,130,246,0.3)';
    } else {
      ctx.shadowBlur = 0;
    }
    ctx.stroke();
    ctx.shadowBlur = 0;

    if (isEdgeSelected && edge.label && transform.value.scale > 0.8) {
      const middleX = (sourceNode.x + targetNode.x) / 2;
      const middleY = (sourceNode.y + targetNode.y) / 2;
      ctx.font = `${10 / transform.value.scale}px Inter, sans-serif`;
      const labelWidth = ctx.measureText(edge.label).width;

      const ew = labelWidth + 12;
      const eh = 18 / transform.value.scale;
      const ex = middleX - ew / 2;
      const ey = middleY - eh / 2;

      let edgeCollides = false;
      for (const rect of acceptedLabels) {
        if (!(ex > rect.x + rect.w || ex + ew < rect.x || ey > rect.y + rect.h || ey + eh < rect.y)) {
          edgeCollides = true;
          break;
        }
      }

      if (!edgeCollides) {
        ctx.fillStyle = 'rgba(255,255,255,0.92)';
        ctx.fillRect(ex, ey, ew, eh);
        ctx.fillStyle = 'rgba(37,99,235,0.92)';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(edge.label, middleX, middleY);
      }
    }
  });

  nodes.forEach((node) => {
    const isSelected = node.id === selectedId;
    const isHovered = hoveredNode?.id === node.id;
    const isInFocus = neighborhoodNodes?.has(node.id);
    const isDimmed = isLocalView
      ? Boolean(neighborhoodNodes?.size && !isInFocus && !isSelected && !isHovered)
      : (selectedId && !connectedNodes?.has(node.id)) ||
        (hoveredNode && hoveredNode.id !== node.id && hoveredNode.id !== selectedId);
    const radius = node.size * (isSelected ? 1.54 : isHovered ? 1.18 : isLocalView && isInFocus ? 1.04 : 1);
    const opacity = isLocalView ? (isDimmed ? 0.18 : isSelected ? 1 : isInFocus ? 0.96 : 0.58) : isDimmed ? 0.18 : 1;

    if (isSelected || isHovered) {
      const glow = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, radius * 3.2);
      glow.addColorStop(0, `${node.color}40`);
      glow.addColorStop(1, `${node.color}00`);
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius * 3.2, 0, Math.PI * 2);
      ctx.fillStyle = glow;
      ctx.fill();
    }

    if (isSelected) {
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius + 5, 0, Math.PI * 2);
      ctx.strokeStyle = node.color;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.globalAlpha = opacity;
    ctx.beginPath();
    ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
    const fill = ctx.createRadialGradient(node.x - radius * 0.34, node.y - radius * 0.34, 0, node.x, node.y, radius);
    fill.addColorStop(0, `${node.color}FF`);
    fill.addColorStop(1, `${node.color}B6`);
    ctx.fillStyle = fill;
    if (isSelected || isHovered) {
      ctx.shadowBlur = 18;
      ctx.shadowColor = node.color;
    } else {
      ctx.shadowBlur = 0;
    }
    ctx.fill();
    ctx.shadowBlur = 0;

    ctx.beginPath();
    ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
      ctx.strokeStyle = isSelected ? node.color : 'rgba(255,255,255,0.92)';
      ctx.lineWidth = isSelected ? 2 : 0.6;
      ctx.stroke();
      ctx.globalAlpha = 1;
  });

  acceptedLabels.forEach((lbl) => {
    const node = lbl.node;
    const isSelected = node.id === selectedId;
    const isHovered = hoveredNode?.id === node.id;
    const isDimmed = isLocalView
      ? Boolean(neighborhoodNodes?.size && !neighborhoodNodes.has(node.id) && !isSelected && !isHovered)
      : (selectedId && !connectedNodes?.has(node.id)) ||
        (hoveredNode && hoveredNode.id !== node.id && hoveredNode.id !== selectedId);

    ctx.globalAlpha = isDimmed ? 0.28 : isSelected || isHovered ? 1 : 0.78;
    ctx.font = `${isSelected ? 600 : 500} ${11 / transform.value.scale}px Inter, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    drawRoundedRect(lbl.x, lbl.y, lbl.w, lbl.h, 8 / transform.value.scale);
    ctx.fillStyle = isSelected
      ? 'rgba(255,255,255,0.98)'
      : isHovered
        ? 'rgba(255,255,255,0.96)'
        : 'rgba(248,250,252,0.92)';
    ctx.fill();
    ctx.strokeStyle = isSelected
      ? 'rgba(59,130,246,0.35)'
      : 'rgba(148,163,184,0.28)';
    ctx.lineWidth = 1 / transform.value.scale;
    ctx.stroke();
    ctx.fillStyle = isSelected ? '#0F172A' : '#475569';
    ctx.fillText(lbl.text, lbl.x + lbl.w / 2, lbl.y + lbl.h / 2 + 0.5 / transform.value.scale);
    ctx.globalAlpha = 1;
  });

  ctx.restore();
}

function tick() {
  if (!simulationActive || !nodes.length) {
    return false;
  }

  const centerX = 0;
  const centerY = 0;
  const isGentleProfile = simulationProfile === 'gentle';
  const alpha = isGentleProfile ? 0.024 : 0.032;
  const repulsion = isGentleProfile ? 3600 : 4000;
  const springLength = isGentleProfile ? 116 : 118;
  const springStrength = isGentleProfile ? 0.052 : 0.07;
  const damping = isGentleProfile ? 0.76 : 0.84;
  const centerStrength = isGentleProfile ? 0.006 : 0.007;

  let totalVelocity = 0;
  let maxStep = 0;

  nodes.forEach((node) => {
    if (node.fx !== null || node.fy !== null) {
      return;
    }
    node.vx += (centerX - node.x) * centerStrength;
    node.vy += (centerY - node.y) * centerStrength;
  });

  for (let index = 0; index < nodes.length; index += 1) {
    for (let innerIndex = index + 1; innerIndex < nodes.length; innerIndex += 1) {
      const sourceNode = nodes[index];
      const targetNode = nodes[innerIndex];
      const dx = targetNode.x - sourceNode.x;
      const dy = targetNode.y - sourceNode.y;
      const distanceSquared = dx * dx + dy * dy + 0.01;
      const force = repulsion / distanceSquared;
      const normalizedForceX = (dx / Math.sqrt(distanceSquared)) * force;
      const normalizedForceY = (dy / Math.sqrt(distanceSquared)) * force;

      if (sourceNode.fx === null) {
        sourceNode.vx -= normalizedForceX;
        sourceNode.vy -= normalizedForceY;
      }

      if (targetNode.fx === null) {
        targetNode.vx += normalizedForceX;
        targetNode.vy += normalizedForceY;
      }
    }
  }

  const nodeMap = buildNodeLookup();
  edges.forEach((edge) => {
    const sourceNode = nodeMap.get(edge.source);
    const targetNode = nodeMap.get(edge.target);
    if (!sourceNode || !targetNode) {
      return;
    }

    const dx = targetNode.x - sourceNode.x;
    const dy = targetNode.y - sourceNode.y;
    const distance = Math.sqrt(dx * dx + dy * dy) || 1;
    const force = (distance - springLength) * springStrength * edge.weight;
    const forceX = (dx / distance) * force;
    const forceY = (dy / distance) * force;

    if (sourceNode.fx === null) {
      sourceNode.vx += forceX;
      sourceNode.vy += forceY;
    }

    if (targetNode.fx === null) {
      targetNode.vx -= forceX;
      targetNode.vy -= forceY;
    }
  });

  nodes.forEach((node) => {
    if (node.fx !== null && node.fy !== null) {
      node.x = node.fx;
      node.y = node.fy;
      return;
    }

    node.vx *= damping;
    node.vy *= damping;
    const stepX = node.vx * alpha;
    const stepY = node.vy * alpha;
    node.x += stepX;
    node.y += stepY;

    maxStep = Math.max(maxStep, Math.abs(stepX), Math.abs(stepY));
    totalVelocity += Math.abs(node.vx) + Math.abs(node.vy);
  });

  if (totalVelocity < 0.22 * nodes.length && maxStep < 0.24) {
    simulationSettledFrames += 1;
  } else {
    simulationSettledFrames = 0;
  }

  if (simulationSettledFrames >= 30) {
    simulationActive = false;
    simulationProfile = 'default';
    nodes.forEach((node) => {
      node.vx = 0;
      node.vy = 0;
    });
  }

  return true;
}

function animate() {
  const cameraMoved = stepCamera();
  const graphMoved = tick();
  if (cameraMoved || graphMoved) {
    draw();
  }
  animationFrameId = window.requestAnimationFrame(animate);
}

function viewportToGraph(clientX: number, clientY: number) {
  if (!canvasRef.value) {
    return { x: 0, y: 0 };
  }

  const rect = canvasRef.value.getBoundingClientRect();
  return {
    x: (clientX - rect.left - transform.value.x) / transform.value.scale,
    y: (clientY - rect.top - transform.value.y) / transform.value.scale
  };
}

function findNodeAtPosition(clientX: number, clientY: number) {
  const graphPoint = viewportToGraph(clientX, clientY);
  for (let index = nodes.length - 1; index >= 0; index -= 1) {
    const node = nodes[index];
    const dx = graphPoint.x - node.x;
    const dy = graphPoint.y - node.y;
    const hitRadius = node.size * 1.8;
    if (dx * dx + dy * dy < hitRadius * hitRadius) {
      return node;
    }
  }
  return null;
}

function setHoverNode(node: CanvasNode | null) {
  hoveredNode = node;
  emit(
    'hover',
    node
      ? {
          id: node.id,
          label: node.label,
          entityType: node.entityType
        }
      : null
  );
}

function handlePointerMove(event: MouseEvent) {
  const node = findNodeAtPosition(event.clientX, event.clientY);
  if (dragState.node) {
    const dx = event.clientX - dragState.startX;
    const dy = event.clientY - dragState.startY;
    if (!dragState.didMove && dx * dx + dy * dy > dragThresholdSquared) {
      dragState.didMove = true;
    }

    if (dragState.didMove) {
      const graphPoint = viewportToGraph(event.clientX, event.clientY);
      dragState.node.fx = graphPoint.x;
      dragState.node.fy = graphPoint.y;
      dragState.node.x = graphPoint.x;
      dragState.node.y = graphPoint.y;
      draw();
    }
    return;
  }

  if (dragState.isPanning) {
    const dx = event.clientX - dragState.startX;
    const dy = event.clientY - dragState.startY;
    if (!dragState.didMove && dx * dx + dy * dy > dragThresholdSquared) {
      dragState.didMove = true;
    }
    if (!dragState.didMove) {
      return;
    }
    targetTransform = null;
    transform.value = {
      ...transform.value,
      x: dragState.originX + dx,
      y: dragState.originY + dy
    };
    draw();
    return;
  }

  if (node?.id !== hoveredNode?.id) {
    setHoverNode(node);
    if (canvasRef.value) {
      canvasRef.value.style.cursor = node ? 'pointer' : 'grab';
    }
    draw();
  }
}

function handlePointerDown(event: MouseEvent) {
  dragState.didMove = false;
  dragState.suppressClick = false;
  dragState.startX = event.clientX;
  dragState.startY = event.clientY;
  targetTransform = null;

  const node = findNodeAtPosition(event.clientX, event.clientY);
  if (node) {
    dragState.node = node;
    node.fx = node.x;
    node.fy = node.y;
    node.vx = 0;
    node.vy = 0;
    restartSimulation('gentle');
    return;
  }

  dragState.isPanning = true;
  dragState.originX = transform.value.x;
  dragState.originY = transform.value.y;
  if (canvasRef.value) {
    canvasRef.value.style.cursor = 'grabbing';
  }
}

function handlePointerUp() {
  const shouldSuppressClick = dragState.didMove;
  if (dragState.node) {
    dragState.node.fx = null;
    dragState.node.fy = null;
    dragState.node.vx = 0;
    dragState.node.vy = 0;
    if (shouldSuppressClick) {
      restartSimulation('gentle');
    }
    dragState.node = null;
  }
  dragState.isPanning = false;
  dragState.didMove = false;
  dragState.suppressClick = shouldSuppressClick;
  if (canvasRef.value) {
    canvasRef.value.style.cursor = hoveredNode ? 'pointer' : 'grab';
  }
  rememberCurrentViewport();
}

function handleClick(event: MouseEvent) {
  if (dragState.suppressClick || dragState.isPanning || dragState.node || dragState.didMove) {
    dragState.suppressClick = false;
    dragState.didMove = false;
    return;
  }

  const isLocalView = currentViewMode() === 'local';
  const node = findNodeAtPosition(event.clientX, event.clientY);
  if (node) {
    const selectedKey = resolveSelectedNodeKey(props.selectedNodeId);
    if (selectedKey && (node.id === selectedKey || node.label === props.selectedNodeId)) {
      if (isLocalView) {
        return;
      }
      emit('select', '');
      return;
    }
    emit('select', node.id);
  } else if (!isLocalView) {
    emit('select', '');
  }
}

function handleDoubleClick(event: MouseEvent) {
  const node = findNodeAtPosition(event.clientX, event.clientY);
  if (!node) {
    return;
  }
  node.fx = null;
  node.fy = null;
}

function handleWheel(event: WheelEvent) {
  event.preventDefault();
  if (!canvasRef.value) {
    return;
  }

  const rect = canvasRef.value.getBoundingClientRect();
  const pointerX = event.clientX - rect.left;
  const pointerY = event.clientY - rect.top;
  const delta = event.deltaY < 0 ? 1.12 : 0.9;
  const newScale = Math.max(0.14, Math.min(4.4, transform.value.scale * delta));
  const currentScale = transform.value.scale;

  targetTransform = null;
  transform.value = {
    x: pointerX - (pointerX - transform.value.x) * (newScale / currentScale),
    y: pointerY - (pointerY - transform.value.y) * (newScale / currentScale),
    scale: newScale
  };
  rememberCurrentViewport();
  draw();
}

function resetView() {
  const viewMode = currentViewMode();
  applyTransform(defaultTransformFor(viewMode, {
    fresh: viewMode === 'global',
    cameraIntent: 'reset'
  }), {
    animate: true,
    viewMode
  });
}

function fitGraph() {
  resetView();
}

function replaySimulation(options: { resetView?: boolean } = {}) {
  const viewMode = currentViewMode();
  reseedNodePositions();
  const resetTransform = options.resetView
    ? defaultTransformFor(viewMode, {
        fresh: true,
        cameraIntent: 'replay',
        persist: false
      })
    : null;
  if (resetTransform) {
    applyTransform(resetTransform, {
      animate: false,
      viewMode
    });
  }
  restartSimulation();
  draw();
}

function zoomIn() {
  const container = containerRef.value;
  if (!container) {
    return;
  }
  const centerX = container.clientWidth / 2;
  const centerY = container.clientHeight / 2;
  const delta = 1.12;
  const newScale = Math.min(transform.value.scale * delta, 4.4);
  const currentScale = transform.value.scale;

  targetTransform = null;
  transform.value = {
    x: centerX - (centerX - transform.value.x) * (newScale / currentScale),
    y: centerY - (centerY - transform.value.y) * (newScale / currentScale),
    scale: newScale
  };
  rememberCurrentViewport();
  draw();
}

function zoomOut() {
  const container = containerRef.value;
  if (!container) {
    return;
  }
  const centerX = container.clientWidth / 2;
  const centerY = container.clientHeight / 2;
  const delta = 0.9;
  const newScale = Math.max(transform.value.scale * delta, 0.14);
  const currentScale = transform.value.scale;

  targetTransform = null;
  transform.value = {
    x: centerX - (centerX - transform.value.x) * (newScale / currentScale),
    y: centerY - (centerY - transform.value.y) * (newScale / currentScale),
    scale: newScale
  };
  rememberCurrentViewport();
  draw();
}

function downloadSnapshot(fileName: string) {
  if (!canvasRef.value) {
    return;
  }
  draw();
  const link = document.createElement('a');
  link.href = canvasRef.value.toDataURL('image/png');
  link.download = fileName;
  link.click();
}

function handleMouseLeave() {
  setHoverNode(null);
  handlePointerUp();
  draw();
}

function bindCanvasEvents() {
  const canvas = canvasRef.value;
  if (!canvas) {
    return;
  }

  canvas.addEventListener('mousemove', handlePointerMove);
  canvas.addEventListener('mousedown', handlePointerDown);
  canvas.addEventListener('mouseup', handlePointerUp);
  canvas.addEventListener('mouseleave', handleMouseLeave);
  canvas.addEventListener('click', handleClick);
  canvas.addEventListener('dblclick', handleDoubleClick);
  canvas.addEventListener('wheel', handleWheel, { passive: false });
}

function unbindCanvasEvents() {
  const canvas = canvasRef.value;
  if (!canvas) {
    return;
  }

  canvas.removeEventListener('mousemove', handlePointerMove);
  canvas.removeEventListener('mousedown', handlePointerDown);
  canvas.removeEventListener('mouseup', handlePointerUp);
  canvas.removeEventListener('mouseleave', handleMouseLeave);
  canvas.removeEventListener('click', handleClick);
  canvas.removeEventListener('dblclick', handleDoubleClick);
  canvas.removeEventListener('wheel', handleWheel);
}

defineExpose({
  fitGraph,
  resetView,
  replaySimulation,
  zoomIn,
  zoomOut,
  downloadSnapshot
});

watch(
  () => props.graph,
  async () => {
    const viewMode = currentViewMode();
    buildGraphData();
    labelTextCache.clear();
    labelAnchorCache.clear();
    previousAcceptedLabelIds.clear();
    savedTransforms.global = null;
    savedTransforms.local = null;
    baselineTransforms.global = null;
    baselineTransforms.local = null;
    lastLocalFocusNodeKey = resolveFocusNodeKey(props.focusNodeId);
    targetTransform = null;
    await nextTick();
    resizeCanvas();
    applyTransform(
      focusTransformFor(props.focusNodeId, viewMode) ?? defaultTransformFor(viewMode),
      { animate: false, viewMode }
    );
    restartSimulation();
  },
  { deep: true }
);

watch(
  () => props.selectedNodeId,
  () => {
    draw();
  }
);

watch(
  () => props.focusNodeId,
  (nextFocusNodeId, previousFocusNodeId) => {
    const nextFocusNodeKey = resolveFocusNodeKey(nextFocusNodeId);
    const previousFocusNodeKey = resolveFocusNodeKey(previousFocusNodeId);

    if (!nextFocusNodeKey || nextFocusNodeKey === previousFocusNodeKey) {
      draw();
      return;
    }

    if (currentViewMode() === 'local') {
      lastLocalFocusNodeKey = nextFocusNodeKey;
      applyTransform(focusTransformFor(nextFocusNodeId, 'local') ?? defaultTransformFor('local'), {
        animate: true,
        viewMode: 'local'
      });
      return;
    }

    applyTransform(focusTransformFor(nextFocusNodeId, 'global') ?? defaultTransformFor('global'), {
      animate: true,
      viewMode: 'global'
    });
  }
);

watch(
  () => props.viewportMode ?? 'global',
  async (nextMode, previousMode) => {
    persistTransform(previousMode ?? 'global');
    await nextTick();
    resizeCanvas();

    if (nextMode === 'local') {
      const selectedKey = resolveFocusNodeKey(props.focusNodeId);
      const shouldReuseLocalView =
        Boolean(savedTransforms.local) &&
        Boolean(selectedKey) &&
        selectedKey === lastLocalFocusNodeKey;

      lastLocalFocusNodeKey = selectedKey;
      if (!shouldReuseLocalView) {
        baselineTransforms.local = null;
      }
      applyTransform(
        shouldReuseLocalView ? savedTransforms.local : defaultTransformFor('local'),
        { animate: true, viewMode: 'local' }
      );
      return;
    }

    baselineTransforms.global = null;
    applyTransform(savedTransforms.global ?? defaultTransformFor('global'), {
      animate: true,
      viewMode: 'global'
    });
  }
);

onMounted(async () => {
  buildGraphData();
  bindCanvasEvents();
  await nextTick();
  resizeCanvas();
  resizeObserver = new ResizeObserver(() => {
    resizeCanvas();
    rememberCurrentViewport();
    draw();
  });
  if (containerRef.value) {
    resizeObserver.observe(containerRef.value);
  }
  lastLocalFocusNodeKey = resolveFocusNodeKey(props.focusNodeId);
  applyTransform(defaultTransformFor(), { animate: false });
  restartSimulation();
  animationFrameId = window.requestAnimationFrame(animate);
});

onBeforeUnmount(() => {
  window.cancelAnimationFrame(animationFrameId);
  resizeObserver?.disconnect();
  unbindCanvasEvents();
});
</script>

<template>
  <div ref="containerRef" class="graph-canvas-shell">
    <canvas ref="canvasRef" class="graph-canvas-surface"></canvas>
  </div>
</template>

<style scoped>
.graph-canvas-shell {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 200px;
  flex: 1;
  overflow: hidden;
  border-radius: 26px;
  background:
    radial-gradient(circle at 18% 18%, rgba(59, 130, 246, 0.16), transparent 28%),
    radial-gradient(circle at 82% 14%, rgba(16, 185, 129, 0.12), transparent 22%),
    linear-gradient(180deg, #f8fbff 0%, #eef6ff 100%);
  border: 1px solid rgba(191, 219, 254, 0.84);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.72),
    0 18px 36px rgba(59, 130, 246, 0.12);
}

.graph-canvas-shell::before {
  content: '';
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(148, 163, 184, 0.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(148, 163, 184, 0.08) 1px, transparent 1px);
  background-size: 32px 32px;
  pointer-events: none;
}

.graph-canvas-surface {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  cursor: grab;
}
</style>
