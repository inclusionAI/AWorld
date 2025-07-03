import React, { useState, useCallback, useEffect, useMemo } from 'react';
import ReactFlow, { Background, Controls } from 'reactflow';
import 'reactflow/dist/style.css';
import { fetchTraceData } from '@/api/trace';
import './index.less';

interface NodeData {
  id: string;
  data: { label: string };
  position: { x: number; y: number };
  type?: string;
}

interface EdgeData {
  id: string;
  source: string;
  target: string;
}

interface TraceXYProps {
  sessionId?: string;
  traceId?: string;
  drawerVisible?: boolean;
}

const TraceXY: React.FC<TraceXYProps> = ({ traceId, drawerVisible }) => {
  const [traceData, setTraceData] = useState<any[]>([]);

  /**
   * 将树形数据转化为 Flow 类型的节点和边
   */
  const convertToFlowElements = useCallback((data: any[]): { nodes: NodeData[]; edges: EdgeData[] } => {
    if (!data) return { nodes: [], edges: [] };

    const nodes: NodeData[] = [];
    const edges: EdgeData[] = [];

    // 布局偏移控制参数
    const HORIZONTAL_SPACING = 150; // 水平间距（父子节点）
    const VERTICAL_SPACING = 100; // 垂直间距
    let nodeId = 1;

    /**
     * 递归处理每个节点，通过 DFS 填充节点和连线
     */
    const processNode = (node: any, depth: number, offsetX: number, parentId?: string) => {
      const id = `node-${nodeId++}`;
      const x = offsetX; // x 坐标（基于层次偏移）
      const y = depth * VERTICAL_SPACING; // y 坐标（基于深度）

      // 创建当前节点
      nodes.push({
        id,
        data: { label: node.show_name },
        position: { x, y }
      });

      // 如果当前节点有父节点，创建对应的边
      if (parentId) {
        edges.push({
          id: `edge-${parentId}-${id}`,
          source: parentId,
          target: id
        });
      }

      // 处理子节点
      if (node.children && node.children.length > 0) {
        const totalWidth = (node.children.length - 1) * HORIZONTAL_SPACING; // 子节点总宽度
        const startX = offsetX - totalWidth / 2; // 子节点的起始 x 坐标

        node.children.forEach((child: any, index: number) => {
          // 传递水平位置给子节点，递归处理
          processNode(child, depth + 1, startX + index * HORIZONTAL_SPACING, id);
        });
      }
    };

    // 从根节点开始处理（确保树形布局）
    if (data.length > 0) {
      processNode(data[0], 0, 0); // 根节点位于 (0, 0)
    }

    return { nodes, edges };
  }, []);

  /**
   * 从 API 中获取数据并格式化
   */
  const handleFetchTrace = useCallback(async () => {
    if (!traceId) return;

    try {
      const result = await fetchTraceData(traceId);
      // 包装为根节点数据
      const formattedData = result?.data
        ? [
            {
              show_name: 'Trace Root',
              children: Array.isArray(result.data) ? result.data : [result.data]
            }
          ]
        : [];
      setTraceData(formattedData);
    } catch (error) {
      console.error('Trace processing error:', error);
    }
  }, [traceId]);

  // 数据转为流程图格式
  const { nodes, edges } = useMemo(() => convertToFlowElements(traceData), [traceData, convertToFlowElements]);

  // 当 traceId 或抽屉打开时请求数据
  useEffect(() => {
    if (traceId && drawerVisible) {
      handleFetchTrace();
    }
  }, [traceId, drawerVisible, handleFetchTrace]);

  return (
    <div className="traceXYbox" style={{ height: '100%', width: '100%' }}>
      <ReactFlow 
        nodes={nodes}
        edges={edges}
        fitView
      >
        <Background gap={16} />
        <Controls />
      </ReactFlow>
      <p className="trace-id">traceId: {traceId}</p>
    </div>
  );
};

export default TraceXY;
