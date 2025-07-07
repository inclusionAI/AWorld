import React, { useState, useEffect } from 'react';
import ReactFlow, { Background, Controls } from 'reactflow';
import CustomNode from './CustomNode';
import 'reactflow/dist/style.css';
import { fetchTraceData } from '@/api/trace';
import './index.less';
import type { NodeData, FlowElements, TraceXYProps } from './TraceXY.types';

const nodeTypes = {
  customNode: CustomNode
};

const buildFlowElements = (data: NodeData[], horizontalSpacing = 200, verticalSpacing = 150): FlowElements => {
  const nodes: any[] = [];
  const edges: any[] = [];
  let nodeId = 1;
  const processNode = (node: NodeData, depth = 0, offsetX = 0, parentId?: string) => {
    const id = `node-${nodeId++}`;
    nodes.push({
      id,
      type: 'customNode',
      data: node,
      position: { x: offsetX, y: depth * verticalSpacing }
    });
    if (parentId) edges.push({ id: `edge-${parentId}-${id}`, source: parentId, target: id, style: { background: 'red' } });

    if (node.children?.length) {
      const totalWidth = (node.children.length - 1) * horizontalSpacing;
      node.children.forEach((child, index) => processNode(child, depth + 1, offsetX - totalWidth / 2 + index * horizontalSpacing, id));
    }
  };

  if (data[0]) {
    processNode(data[0]);
  }

  return { nodes, edges };
};
const TraceXY: React.FC<TraceXYProps> = ({ traceId, traceQuery, drawerVisible }) => {
  const [elements, setElements] = useState<FlowElements>({ nodes: [], edges: [] });

  useEffect(() => {
    if (!traceId || !drawerVisible) return;

    const fetchAndBuildElements = async () => {
      try {
        const result = await fetchTraceData(traceId);

        // Always wrap as root node data
        let formattedData = [];
        if (result?.data) {
          const { data } = result;
          formattedData = Array.isArray(data) && data.length >= 1 ? [{ show_name: traceQuery, children: data }] : data;
        }
        setElements(buildFlowElements(formattedData));
      } catch (error) {
        console.error('Failed to fetch and build trace elements:', error);
      }
    };

    fetchAndBuildElements();
  }, [traceId, drawerVisible]);

  return (
    <div className="traceXYbox" style={{ height: '100%', width: '100%' }}>
      <ReactFlow nodes={elements.nodes} edges={elements.edges} nodeTypes={nodeTypes} fitView>
        <Background gap={16} />
        <Controls />
      </ReactFlow>
    </div>
  );
};

export default TraceXY;
