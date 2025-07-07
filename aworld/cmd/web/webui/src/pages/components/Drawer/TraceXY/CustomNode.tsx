import React from 'react';
import { Tooltip, Typography } from 'antd';
import { Position, Handle } from 'reactflow';
import type { NodeData } from './TraceXY.types';

interface CustomNodeProps {
  data: NodeData;
}

const CustomNode: React.FC<CustomNodeProps> = ({ data }) => {
  const summary = data.summary ? JSON.parse(data.summary).summary : '';
  const tooltipContent = data.event_id ? (
    <div className="Tooltipbox">
      {/* {data.summary ? <pre>{JSON.parse(data.summary).summary}</pre> :''} */}
      {summary.length > 120 ? summary : ''}
      <div>{data.event_id}</div>
    </div>
  ) : null;
  return (
    <Tooltip title={tooltipContent} placement="bottom" className="Tooltipbox">
      <div className="custom-node">
        <Typography.Paragraph className="summary" ellipsis={{ rows: 4 }}>
          {summary}
        </Typography.Paragraph>
        <div className="name">{data.show_name}</div>
        <Handle type="target" position={Position.Top} style={{ background: '#555' }} />
        <Handle type="source" position={Position.Bottom} style={{ background: '#555' }} />
      </div>
    </Tooltip>
  );
};
export default CustomNode;
