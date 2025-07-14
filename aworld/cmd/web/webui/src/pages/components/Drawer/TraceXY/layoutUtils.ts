import dagre from 'dagre';

export const getLayoutedElements = (nodes: any[], edges: any[]) => {
  const dagreGraph = new (dagre as any).graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));
  dagreGraph.setGraph({ rankdir: 'TB', nodesep: 50, ranksep: 50 });

  nodes.forEach((node) => {
    dagreGraph.setNode(node.id, { width: 200, height: 100 });
  });

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target);
  });

  dagre.layout(dagreGraph);

  return {
    nodes: nodes.map((node) => {
      const nodeWithPosition = dagreGraph.node(node.id);
      return {
        ...node,
        position: {
          x: nodeWithPosition.x - 100, // 减去节点宽度的一半
          y: nodeWithPosition.y - 50 // 减去节点高度的一半
        }
      };
    }),
    edges
  };
};
