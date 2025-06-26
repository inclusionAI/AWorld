import { CheckOutlined } from '@ant-design/icons';
import { Card, Flex } from 'antd';
import React from 'react';
import type { ToolCardData } from '../utils';
import './index.less';

const MAX_DISPLAY_ITEMS = 3;

interface Props {
  data: ToolCardData;
}

interface ItemInterface {
  title: string;
  snippet: string;
  link?: string;
  isViewMore?: boolean;
}

const cardLinkList: React.FC<Props> = ({ data }) => {
  const items = data?.card_data?.search_items || [];
  const remainingCount = Math.max(0, items.length - MAX_DISPLAY_ITEMS);
  const cardItems = [
    ...items.slice(0, MAX_DISPLAY_ITEMS),
    ...(remainingCount > 0
      ? [
        {
          title: `View ${remainingCount} more`,
          snippet: '',
          link: 'https://www.baidu.com',
          isViewMore: true
        }
      ]
      : [])
  ];

  return (
    <div className="cardwrap">
      {items.length > 0 && (
        <div className="card-length">
          <CheckOutlined className="check-icon" />
          {items.length} results
        </div>
      )}
      <Flex className="cardbox" justify="space-between">
        {cardItems.map((item: ItemInterface, index: number) => (
          <Card key={index} className={`card-item ${item.isViewMore ? 'view-more' : ''}`} onClick={() => item.link && (window.open(item.link, '_blank'))}>
            <Card.Meta title={item.title} description={item.snippet} />
          </Card>
        ))}
      </Flex>
    </div>
  );
};

export default cardLinkList;
