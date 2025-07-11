import React from 'react';
import { Handle, Position } from '@xyflow/react';
import type { NodeProps } from '@xyflow/react';

interface CustomNodeData extends Record<string, unknown> {
  label: string;
  content?: React.ReactNode;
}

export const CustomNode = ({ data }: NodeProps) => {
  const nodeData = data as CustomNodeData;
  return (
    <div className="custom-node">
      <div className="custom-node-header">{nodeData.label}</div>
      <div className="custom-node-content">{nodeData.content ?? 'Custom Node Content'}</div>
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
    </div>
  );
};
