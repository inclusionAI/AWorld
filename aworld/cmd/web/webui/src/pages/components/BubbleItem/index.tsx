import React, { useEffect } from 'react';
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
  isLoading?: boolean;
}

const BubbleItem: React.FC<BubbleItemProps> = ({ sessionId, data, onOpenWorkspace, isLoading = false }) => {
  // 移除workspaceVisible和toolCardData状态，不再需要内部Drawer

  // 修改openWorkspace函数，直接调用外部回调
  const openWorkspace = (data: ToolCardData) => {
    if (onOpenWorkspace) {
      onOpenWorkspace(data);
    }
  };

  const { segments } = extractToolCards(data);

    // 自动打开workspace的逻辑 - 只在流式输出过程中自动打开
  useEffect(() => {
    // 只有在流式输出过程中才自动打开workspace
    if (!isLoading) return;
    
    // 检查是否有cardLinkList类型的工具卡片且有workspace功能
    const cardLinkListSegment = segments.find(segment => {
      if (segment.type !== 'tool_card') return false;
      return segment.data?.card_type === 'tool_call_card_link_list' &&
             segment.data?.artifacts?.length > 0;
    });
    
    if (cardLinkListSegment && cardLinkListSegment.type === 'tool_card' && onOpenWorkspace) {
      // 使用setTimeout确保BubbleItem完全渲染后再打开workspace
      const timer = setTimeout(() => {
        openWorkspace(cardLinkListSegment.data);
      }, 100);
      
      return () => clearTimeout(timer);
    }
  }, [segments, onOpenWorkspace, openWorkspace, isLoading]);

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
