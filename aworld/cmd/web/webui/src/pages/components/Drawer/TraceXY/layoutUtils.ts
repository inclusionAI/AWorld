import dagre from 'dagre';

const calculateEdgeLength = (
  sourcePos: { x: number; y: number },
  targetPos: { x: number; y: number }
) => Math.hypot(targetPos.x - sourcePos.x, targetPos.y - sourcePos.y);

export const getLayoutedElements = (nodes: any[], edges: any[]) => {
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));
  dagreGraph.setGraph({ rankdir: 'TB', nodesep: 50, ranksep: 50 });

  // set nodes
  nodes.forEach((node) => dagreGraph.setNode(node.id, { width: 200, height: 100 }));

  // set edges
  edges.forEach((edge) => dagreGraph.setEdge(edge.source, edge.target));

  dagre.layout(dagreGraph);

  const virtualNodes: any[] = [];
  const newEdges: any[] = [];

  // Handle long edges and generate virtual nodes.
  edges.forEach((edge) => {
    const sourceNode = nodes.find((n) => n.id === edge.source);
    const targetNode = nodes.find((n) => n.id === edge.target);
    if (!sourceNode || !targetNode) return;

    const sourcePos = dagreGraph.node(edge.source);
    const targetPos = dagreGraph.node(edge.target);
    const length = calculateEdgeLength(sourcePos, targetPos);
    console.log('length:', length);
    if (length > 400) {
      const virtualCount = Math.floor(length / 300);
      const virtualNodeIds = Array.from({ length: virtualCount }, (_, i) => {
        const id = `virtual-${edge.id}-${i + 1}`;
        virtualNodes.push({
          id,
          width: 0,
          height: 0,
          type: 'customNode',
          data: { type: 'virtual' },
          className: 'virtual-node',
          style: { opacity: 0, pointerEvents: 'none' }
        });
        return id;
      });

      // Adjust edges to connect nodes and corresponding virtual nodes.
      const allTargets = [edge.source, ...virtualNodeIds, edge.target];
      allTargets.reduce((prev, curr, index) => {
        if (index > 0) {
          newEdges.push({
            id: `${edge.id}-virtual-${index - 1}`,
            source: prev,
            target: curr,
            data: { ...edge.data, isVirtual: index < virtualNodeIds.length },
            style: { pointerEvents: 'none' },
            className: 'virtual-node-edge'
          });
        }
        return curr;
      });
    } else {
      newEdges.push({
        ...edge,
        style: { pointerEvents: 'none' }
      });
    }
  });

  // Add virtual nodes to the graph.
  virtualNodes.forEach((node) => dagreGraph.setNode(node.id, { width: 0, height: 0 }));

  // Add Edges to the graph.
  newEdges.forEach((edge) => dagreGraph.setEdge(edge.source, edge.target));

  dagre.layout(dagreGraph);

  return {
    nodes: [...nodes, ...virtualNodes].map((node) => {
      const { x, y } = dagreGraph.node(node.id);
      return {
        ...node,
        position: { x: x - 100, y: y - 50 } // Subtract half the width and height of the node to center it.
      };
    }),
    edges: newEdges
  };
};
