import { memo, useEffect, useMemo, useRef, useState } from "react";
import {
  Handle,
  Position,
  ReactFlow,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  buildEdges,
  graphHeight,
  layoutNodes,
  topologyKey,
  type CausalNode,
  type CausalNodeData,
} from "../../lib/graph";
import type { CausalGraph as CausalGraphType } from "../../types";

const LEGEND: [string, string][] = [
  ["done", "done"],
  ["pending", "pending"],
  ["failed", "failed"],
  ["critical", "critical path"],
];

/**
 * One component in the causal DAG.
 *
 * memo'd on its data: the executor loop re-serializes the whole graph on every
 * iteration, so without this every node re-renders on every frame.
 * `--hop` drives the ripple's transition-delay purely in CSS — no JS timers.
 */
const CausalNodeView = memo(function CausalNodeView({ data }: NodeProps<CausalNode>) {
  const { label, kind, status, hop, onCritical, highlighted } = data as CausalNodeData;
  return (
    <div
      className={
        `dag-node ${status}` +
        (onCritical ? " critical" : "") +
        (highlighted ? " highlighted" : "")
      }
      style={{ ["--hop" as string]: hop }}
      data-status={status}
      title={`${label} · ${kind} · ${status} — click for details`}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <span className="dag-node-label">{label}</span>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
});

const NODE_TYPES: NodeTypes = { causal: CausalNodeView };

interface Props {
  graph: CausalGraphType;
  /** Opens the drill-down drawer for a component. */
  onOpenNode?: (id: string, label: string) => void;
  highlightedId?: string | null;
}

export function CausalGraph({ graph, onOpenNode, highlightedId }: Props) {
  const key = topologyKey(graph);

  // Read through a ref so the memo below can depend on the topology key alone.
  // That is the whole point: layout must not recompute when only a status
  // changed, which is what every frame after the graph is built carries.
  const graphRef = useRef(graph);
  graphRef.current = graph;

  // Synchronous, so the container is correctly sized on the very first paint.
  // Deriving height from state instead would let fitView run against a stale
  // height and zoom the diagram down to an unreadable size.
  // `key` is the only dependency by design: it is the topology fingerprint, and
  // depending on `graph` instead would re-run layout on every status frame —
  // exactly the jump this is here to prevent.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const base = useMemo(() => layoutNodes(graphRef.current), [key]);
  const [nodes, setNodes] = useState<CausalNode[]>(base);

  useEffect(() => setNodes(base), [base]);

  // Status-only update: patch data in place, preserving node identity so React
  // Flow keeps the same DOM element and CSS transitions it rather than
  // remounting.
  useEffect(() => {
    setNodes((prev) => {
      const byId = new Map((graph.nodes || []).map((n) => [n.id, n.status]));
      let changed = false;
      const next = prev.map((node) => {
        const status = byId.get(node.id);
        if (!status || status === node.data.status) return node;
        changed = true;
        return { ...node, data: { ...node.data, status } };
      });
      return changed ? next : prev;
    });
  }, [graph]);

  const edges = useMemo(() => buildEdges(graph), [graph]);
  const height = useMemo(() => graphHeight(base), [base]);

  const rendered = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        // Selectable so React Flow gives the node pointer events at all: it
        // sets pointer-events:none inline unless something needs them, and an
        // onClick inside the custom node is invisible to that decision.
        selectable: true,
        focusable: true,
        data: { ...node.data, highlighted: highlightedId === node.id },
      })),
    [nodes, highlightedId],
  );

  // Mid-run the ledger is still partial, so the streaming graph is rendered
  // without a click handler. Don't advertise an affordance that isn't there.
  const interactive = !!onOpenNode;

  return (
    <div className="graph-card">
      <div
        className={"causal-graph-container" + (interactive ? " interactive" : "")}
        style={{ height }}
      >
        <ReactFlow
          nodes={rendered}
          edges={edges}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.12 }}
          onNodeClick={(_, node) =>
            onOpenNode?.(node.id, (node.data as CausalNodeData).label)
          }
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          nodesFocusable
          panOnScroll={false}
          zoomOnScroll={false}
          preventScrolling={false}
          minZoom={0.2}
          maxZoom={1.6}
        />
      </div>
      <div className="graph-legend">
        {LEGEND.map(([cls, label]) => (
          <span key={cls}>
            <i className={"lg-dot " + cls} />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}
