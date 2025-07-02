import React, { useState, useEffect } from 'react';
import ReactFlow, { Background, Controls, Position, Handle } from 'reactflow';
import 'reactflow/dist/style.css';
import { fetchTraceData } from '@/api/trace'; // 替换为你的请求路径
import './index.less';

// 节点数据接口
interface NodeData {
  show_name: string;
  event_id?: string;
  summary?: string;
  children?: NodeData[];
}

// React Flow 元素接口
interface FlowElements {
  nodes: any[];
  edges: any[];
}

// 组件 Prop 接口
interface TraceXYProps {
  traceId?: string;
  drawerVisible?: boolean;
}

// 自定义 React Flow 的节点样式
const CustomNode = ({ data }: { data: NodeData }) => (
  <div className="custom-node">
    <div>
      <strong>{data.show_name}</strong>
    </div>
    {data.event_id && (
      <div onClick={() => alert(`Event ID: ${data.event_id}`)} style={{ cursor: 'pointer' }}>
        <strong>Event ID:</strong> {data.event_id}
      </div>
    )}
    {data.summary && (
      <div>
        <strong>Summary:</strong> {data.summary}
      </div>
    )}
    <Handle type="target" position={Position.Top} style={{ background: '#555' }} />
    <Handle type="source" position={Position.Bottom} style={{ background: '#555' }} />
  </div>
);

// 构建 ReactFlow 的节点和边
const buildFlowElements = (
  data: NodeData[],
  horizontalSpacing = 150,
  verticalSpacing = 100
): FlowElements => {
  const nodes: any[] = [];
  const edges: any[] = [];
  let nodeId = 1;

  const processNode = (node: NodeData, depth = 0, offsetX = 0, parentId?: string) => {
    const id = `node-${nodeId++}`;
    nodes.push({
      id,
      type: 'customNode',
      data: node,
      position: { x: offsetX, y: depth * verticalSpacing },
    });
    if (parentId) edges.push({ id: `edge-${parentId}-${id}`, source: parentId, target: id,style:{background:'red'}});

    if (node.children?.length) {
      const totalWidth = (node.children.length - 1) * horizontalSpacing;
      node.children.forEach((child, index) =>
        processNode(child, depth + 1, offsetX - totalWidth / 2 + index * horizontalSpacing, id)
      );
    }
  };

  data[0] && processNode(data[0]);
  return { nodes, edges };
};

// 主组件
const TraceXY: React.FC<TraceXYProps> = ({ traceId, drawerVisible }) => {
  const [elements, setElements] = useState<FlowElements>({ nodes: [], edges: [] });

  useEffect(() => {
    if (!traceId || !drawerVisible) return;

    const fetchAndBuildElements = async () => {
      try {
        const result = await fetchTraceData(traceId); // 请求数据
        setElements(buildFlowElements(result?.data || [])); // 构建 Flow 数据
      } catch (error) {
        console.error('Failed to fetch and build trace elements:', error);
      }
    };

    fetchAndBuildElements();
  }, [traceId, drawerVisible]);

  return (
    <div className="traceXYbox" style={{ height: '100%', width: '100%' }}>
      <ReactFlow
        nodes={elements.nodes}
        edges={elements.edges}
        nodeTypes={{ customNode: CustomNode }}
        fitView
      >
        <Background gap={16} />
        <Controls />
      </ReactFlow>
      {/* <p className="trace-id">traceId: {traceId}</p> */}
    </div>
  );
};

export default TraceXY;