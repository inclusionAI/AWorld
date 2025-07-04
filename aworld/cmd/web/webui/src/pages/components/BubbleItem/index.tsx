import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Drawer } from 'antd';
import { ShrinkOutlined } from '@ant-design/icons';
import CardDefault from './cardDefault';
import CardLinkList from './cardLinkList';
import { extractToolCards } from './utils';
import Workspace from '../Drawer/Workspace';
import type { ToolCardData } from './utils';
import './index.less'

interface BubbleItemProps {
  sessionId: string;
  data: string;
  trace_id: string;
}

const BubbleItem: React.FC<BubbleItemProps> = ({ sessionId, data }) => {
  const [workspaceVisible, setWorkspaceVisible] = useState(false);
  const [toolCardData, setToolCardData] = useState<ToolCardData | undefined>(undefined);
  const openWorkspace = (data: ToolCardData) => {
    setToolCardData(data);
    setWorkspaceVisible(true);
  };
  const closeWorkspace = () => setWorkspaceVisible(false);
  const { segments } = extractToolCards(data);
  // console.log('segments:', segments);
  return (
    <div className="card">
      {segments.map((segment, index) => {
        if (segment.type === 'text') {
          return (
            <div className="markdownbox">
              <ReactMarkdown key={`text-${index}`}>{segment.content}</ReactMarkdown>
            </div>
          );
        } else if (segment.type === 'tool_card') {
          const cardType = segment.data?.card_type;
          if (cardType === 'tool_call_card_link_list') {
            return <CardLinkList key={`tool-${index}`} sessionId={sessionId} data={segment.data} onOpenWorkspace={openWorkspace} />;
          } else {
            return <CardDefault key={`tool-${index}`} sessionId={sessionId} data={segment.data} onOpenWorkspace={openWorkspace} />;
          }
        }
      })}
      <Drawer
        title="Workspace"
        width={700}
        placement="right"
        onClose={closeWorkspace}
        open={workspaceVisible}
        extra={
          <ShrinkOutlined
            onClick={closeWorkspace}
            style={{
              fontSize: '18px',
              color: '#444',
              cursor: 'pointer'
            }}
          />
        }
      >
        <Workspace sessionId={sessionId} toolCardData={toolCardData} />
      </Drawer>
    </div>
  );
};

export default BubbleItem;
