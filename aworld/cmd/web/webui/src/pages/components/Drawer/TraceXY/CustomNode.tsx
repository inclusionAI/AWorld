import { Tooltip, Typography } from 'antd';
import { Position, Handle } from 'reactflow';

import type { NodeData } from './TraceXY.types';

export const CustomNode: React.FC<{ data: NodeData }> = ({ data }: { data: NodeData }) => {
  const content = (
    <>
      <strong>{data.show_name}</strong>
      <Typography.Paragraph className="desc" ellipsis={{ rows: 1 }}>
        {data.event_id}
      </Typography.Paragraph>
    </>
  );

  try {
    return (
      <div className="custom-node">
        {data.summary ? (
          <Tooltip
            className="Tooltipbox"
            title={
              <div className="Tooltipbox">
                {/* <pre>{JSON.stringify(JSON.parse(data.summary), null, 2)}</pre> */}
                <pre>{JSON.parse(data.summary).summary}</pre>
              </div>
            }
            placement="bottom"
          >
            {content}
          </Tooltip>
        ) : (
          content
        )}
        <Handle type="target" position={Position.Top} style={{ background: '#555' }} />
        <Handle type="source" position={Position.Bottom} style={{ background: '#555' }} />
      </div>
    );
  } catch (e: unknown) {
    return (
      <div className="custom-node">
        {content}
        <Handle type="target" position={Position.Top} style={{ background: '#555' }} />
        <Handle type="source" position={Position.Bottom} style={{ background: '#555' }} />
      </div>
    );
  }
};
