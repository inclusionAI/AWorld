import React from 'react';
import ReactMarkdown from 'react-markdown';
import { extractToolCards } from './utils';
import CardDefault from './cardDefault';
import CardLinkList from './cardLinkList';

interface BubbleItemProps {
  data: string;
}

const BubbleItem: React.FC<BubbleItemProps> = ({ data }) => {
  const { segments } = extractToolCards(data);
  console.log(segments);
  return (
    <div className="card">
      {segments.map((segment, index) => {
        if (segment.type === 'text') {
          return <ReactMarkdown key={`text-${index}`}>{segment.content}</ReactMarkdown>;
        } else if (segment.type === 'tool_card') {
          const cardType = segment.data?.card_type;
          if (cardType === 'tool_call_card_link_list') {
            return <CardLinkList key={`tool-${index}`} data={segment.data} />;
          } else {
            return <CardDefault key={`tool-${index}`} data={segment.data} />;
          }
        }
      })}
    </div>
  );
};

export default BubbleItem;
