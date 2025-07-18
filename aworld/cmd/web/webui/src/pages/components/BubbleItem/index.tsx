import React from 'react';
import ReactMarkdown from 'react-markdown';
import CardDefault from './cardDefault';
import CardLinkList from './cardLinkList';
import './index.less';
import type { ToolCardData } from './utils';
import { extractToolCards } from './utils';

interface BubbleItemProps {
  sessionId: string;
  data: string;
  trace_id: string;
  onOpenWorkspace?: (data: ToolCardData) => void;
}

const BubbleItem: React.FC<BubbleItemProps> = ({ sessionId, data, onOpenWorkspace }) => {
  // 移除workspaceVisible和toolCardData状态，不再需要内部Drawer

  // 修改openWorkspace函数，直接调用外部回调
  const openWorkspace = (data: ToolCardData) => {
    if (onOpenWorkspace) {
      onOpenWorkspace(data);
    }
  };

  const { segments } = extractToolCards(data);
  // console.log('segments:', segments);
  return (
    <div className="card">
      {segments.map((segment, index) => {
        if (segment.type === 'text') {
          return (
            <div className="markdownbox" key={`text-${index}`}>
              <ReactMarkdown>{segment.content}</ReactMarkdown>
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
      {/* 移除内部的Drawer */}
    </div>
  );
};

export default BubbleItem;
