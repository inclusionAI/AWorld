import React, { useState, useEffect, useCallback } from 'react';
import { ReactFlow, Background, Controls } from '@xyflow/react';
import CustomNode from './CustomNode';
import '@xyflow/react/dist/style.css';
import { fetchTraceData } from '@/api/trace';
import { getLayoutedElements } from './layoutUtils';
import './index.less';
import type { TraceXYProps, NodeData, EdgeData } from './TraceXY.types';

const nodeTypes = {
  customNode: CustomNode
};
const TraceXY: React.FC<TraceXYProps> = ({ traceId, drawerVisible }) => {
  const [nodes, setNodes] = useState<NodeData[]>([]);
  const [edges, setEdges] = useState<EdgeData[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const processNodes = useCallback((rawNodes: any[] = []): NodeData[] => {
    return (rawNodes || []).map((node) => ({
        ...node,
        id: node.span_id || node.id || '',
        type: 'customNode',
        position: node.position || { x: 0, y: 0 },
        data: {
          ...node.data,
          label: node.show_name,
          summary: node.summary || '',
          show_name: node.show_name,
          event_id: node.event_id
        }
    }));
  }, []);

  const processEdges = useCallback((rawEdges: any[] = []): EdgeData[] => {
    return (rawEdges || []).map((edge) => ({
      ...edge,
      id: `${edge.source}-${edge.target}`,
      className: 'node-edge'
    }));
  }, []);

  useEffect(() => {
    if (!traceId || !drawerVisible) return;

    const fetchAndBuildElements = async () => {
      setLoading(true);
      setError(null);

      try {
        const result = await fetchTraceData(traceId);
        const nodesWithPosition = processNodes(result?.nodes);
        const edgesWithId = processEdges(result?.edges);
        const { nodes: layoutedNodes, edges: layoutedEdges } = await getLayoutedElements(
          nodesWithPosition,
          edgesWithId
        );
        setNodes(layoutedNodes);
        setEdges(layoutedEdges);
      } catch (err) {
        setError('Failed to load trace data, please try again later');
        console.error('Failed to fetch and build trace elements:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchAndBuildElements();
  }, [traceId, drawerVisible, processNodes, processEdges]);

  return (
    <div className="traceXYbox" style={{ height: '100%', width: '100%' }}>
      {loading && <div className="loading-indicator">Loading...</div>}
      {error && <div className="error-message">{error}</div>}
      {!loading && !error && nodes.length === 0 && (
        <div
          className="empty-state"
          style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            height: '100%',
            color: '#888'
          }}
        >
          No trace data available
        </div>
      )}
      {nodes.length > 0 ? (
        <ReactFlow
          nodes={nodes.map((node, index) => ({
            ...node,
            isFirst: index === 0,
            isLast: index === nodes.length - 1
          }))}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          minZoom={0.1}
          maxZoom={2}
        >
          <Background gap={16} />
          <Controls />
        </ReactFlow>
      ) : null}
    </div>
  );
};

export default TraceXY;
