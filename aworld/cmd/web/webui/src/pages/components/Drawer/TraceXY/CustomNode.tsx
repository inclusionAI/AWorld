import React from 'react';
import { Tooltip } from 'antd';
import { Position, Handle } from 'reactflow';
import type { NodeData } from './TraceXY.types';

interface CustomNodeProps {
  data: NodeData;
}

const CustomNode: React.FC<CustomNodeProps> = ({ data }) => {
  const tooltipContent = (
    <div className="Tooltipbox">
      {data.summary ? <pre>{JSON.parse(data.summary).summary}</pre> : <div>{data.show_name}</div>}
      <div>{data.event_id}</div>
    </div>
  );

  return (
    <Tooltip title={tooltipContent} placement="bottom" className="Tooltipbox">
      <div className="custom-node">
        <span className="name">{data.show_name}</span>
        <Handle type="target" position={Position.Top} style={{ background: '#555' }} />
        <Handle type="source" position={Position.Bottom} style={{ background: '#555' }} />
      </div>
    </Tooltip>
  );
};
export default CustomNode;
