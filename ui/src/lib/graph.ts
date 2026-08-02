import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";
import type { CausalGraph, NodeStatus } from "../types";

const NODE_WIDTH = 190;
const NODE_HEIGHT = 72;
const RANKSEP = 84;

export interface CausalNodeData {
  [key: string]: unknown;
  label: string;
  kind: string;
  status: NodeStatus;
  hop: number;
  onCritical: boolean;
  highlighted?: boolean;
}

export type CausalNode = Node<CausalNodeData, "causal">;

const EDGE_COLORS = {
  critical: "#f97316",
  done: "#2dd4bf",
  pending: "#6b7280",
  failed: "#f43f5e",
};

function criticalHopMap(graph: CausalGraph): Map<string, number> {
  const hops = new Map<string, number>();
  for (const [index, id] of (graph.critical_path || []).entries()) hops.set(id, index);
  return hops;
}

export function topologyKey(graph: CausalGraph): string {
  const nodes = (graph.nodes || []).map((node) => node.id).sort().join("|");
  const edges = (graph.edges || [])
    .map((edge) => `${edge.source}->${edge.target}`)
    .sort()
    .join("|");
  return `${nodes}::${edges}`;
}

export function layoutNodes(graph: CausalGraph): CausalNode[] {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", ranksep: RANKSEP, nodesep: 40, marginx: 24, marginy: 24 });
  g.setDefaultEdgeLabel(() => ({}));

  const hops = criticalHopMap(graph);
  for (const node of graph.nodes || []) {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const edge of graph.edges || []) g.setEdge(edge.source, edge.target);

  dagre.layout(g);

  return (graph.nodes || []).map((node) => {
    const positioned = g.node(node.id) as { x: number; y: number } | undefined;
    const hop = hops.get(node.id) ?? 0;
    return {
      id: node.id,
      type: "causal",
      position: {
        x: (positioned?.x ?? 0) - NODE_WIDTH / 2,
        y: (positioned?.y ?? 0) - NODE_HEIGHT / 2,
      },
      data: {
        label: node.label,
        kind: node.kind,
        status: node.status,
        hop,
        onCritical: hops.has(node.id),
      },
    } satisfies CausalNode;
  });
}

export function buildEdges(graph: CausalGraph): Edge[] {
  const criticalSet = new Set<string>();
  const path = graph.critical_path || [];
  for (let i = 0; i < path.length - 1; i += 1) {
    criticalSet.add(`${path[i]}->${path[i + 1]}`);
  }

  return (graph.edges || []).map((edge, index) => {
    const key = `${edge.source}->${edge.target}`;
    const critical = criticalSet.has(key);
    return {
      id: `${key}-${index}`,
      source: edge.source,
      target: edge.target,
      animated: critical,
      label: edge.confidence ? `${edge.relation} (${Math.round(edge.confidence * 100)}%)` : edge.relation,
      style: {
        stroke: critical
          ? EDGE_COLORS.critical
          : edge.confidence < 0.45
            ? EDGE_COLORS.pending
            : EDGE_COLORS.done,
        strokeWidth: critical ? 2.5 : 1.9,
      },
      labelStyle: {
        fontSize: 11,
        fill: critical ? EDGE_COLORS.critical : "#94a3b8",
        fontWeight: 500,
      },
      labelBgStyle: {
        fill: "var(--surface-1, #1e293b)",
        fillOpacity: 0.9,
        stroke: "var(--border, #334155)",
        strokeWidth: 1,
        rx: 4,
        ry: 4,
      },
      labelBgPadding: [6, 4],
      markerEnd: {
        type: "arrowclosed",
        color: critical
          ? EDGE_COLORS.critical
          : edge.confidence < 0.45
            ? EDGE_COLORS.pending
            : EDGE_COLORS.done,
      },
    } satisfies Edge;
  });
}

export function graphHeight(nodes: CausalNode[]): number {
  if (nodes.length === 0) return 280;
  const maxY = Math.max(...nodes.map((node) => node.position.y + NODE_HEIGHT));
  return Math.max(280, Math.ceil(maxY + 40));
}
