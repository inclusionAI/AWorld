import React, { useCallback, useState } from 'react';
import { ReactFlow, Background, MiniMap, useNodesState, useEdgesState, useReactFlow, ReactFlowProvider } from '@xyflow/react';
import { FlowControls } from './components/FlowControls';
import type { Node, Edge, Connection } from '@xyflow/react';
import { CustomNode } from './components/CustomNode/index';
import { saveFlow, loadFlow } from './utils/flowStorageUtils';

import { initialNodes, initialEdges } from './constants';
import { addNode } from './utils/nodeUtils';
import { addEdge, deleteEdge, updateEdgeStyles } from './utils/edgeUtils';
import { autoLayout } from './utils/layoutUtils';
import '@xyflow/react/dist/style.css';
import './index.less';


const nodeTypes = {
  customNode: CustomNode
};

function FlowChart() {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialEdges);
  const [showMinimap, setShowMinimap] = useState(false);
  const [isStraightLine, setIsStraightLine] = useState(false);

  const reactFlowInstance = useReactFlow();

  const handleAddNode = useCallback(() => {
    addNode(nodes, setNodes);
  }, [nodes, setNodes]);

  const handleConnect = useCallback(
    (params: Connection) => {
      setEdges(addEdge(edges, params.source, params.target));
    },
    [edges]
  );

  const handleAutoLayout = useCallback(() => {
    autoLayout(nodes, edges, setNodes, reactFlowInstance);
  }, [nodes, edges, setNodes, reactFlowInstance]);

  const handleSave = useCallback(() => {
    saveFlow(nodes, edges);
  }, [nodes, edges]);

  const handleLoad = useCallback(() => {
    loadFlow(setNodes, setEdges);
  }, [setNodes, setEdges]);

  const handleDeleteEdge = useCallback(
    (edgeId: string) => {
      setEdges(deleteEdge(edges, edgeId));
    },
    [edges, setEdges]
  );

  const updatedEdges = updateEdgeStyles(edges, isStraightLine).map((edge) => ({
    ...edge,
    label:
      edge.label &&
      React.cloneElement(edge.label as React.ReactElement, {
        onClick: (e: React.MouseEvent) => {
          (edge.label as React.ReactElement)?.props?.onClick?.(e);
          handleDeleteEdge(edge.id);
        }
      })
  }));
  return (
    <div style={{ width: '100%', height: '100vh' }}>
        <ReactFlow 
          nodes={nodes} 
          edges={updatedEdges} 
          onNodesChange={onNodesChange} 
          onEdgesChange={onEdgesChange} 
          onConnect={handleConnect} 
          fitView 
          nodesDraggable 
          edgesFocusable 
          panOnScroll 
          nodeTypes={nodeTypes}
        >
        <Background />
        {showMinimap && <MiniMap />}
        <FlowControls
          isStraightLine={isStraightLine}
          showMinimap={showMinimap}
          onToggleLine={() => setIsStraightLine(!isStraightLine)}
          onSave={handleSave}
          onLoad={handleLoad}
          onAutoLayout={handleAutoLayout}
          onToggleMinimap={() => setShowMinimap(!showMinimap)}
          onAddNode={handleAddNode}
        />
      </ReactFlow>
    </div>
  );
}

export default function () {
  return (
    <ReactFlowProvider>
      <FlowChart />
    </ReactFlowProvider>
  );
}
