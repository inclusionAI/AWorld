import { CheckOutlined } from '@ant-design/icons';
import { Card, Flex } from 'antd';
import React from 'react';
import type { ToolCardData } from '../utils';
import './index.less';

interface Props {
  data: ToolCardData;
}

interface ItemInterface {
  title: string;
  snippet: string;
  link?: string;
}

const cardLinkList: React.FC<Props> = ({ data }) => {
  const items = data?.card_data?.search_items || [];
  const cardItems = items;

  return (
    <div className="cardwrap">
      {items.length > 0 && (
        <div className="card-length">
          <CheckOutlined className="check-icon" />
          {items.length} results
        </div>
      )}
      <Flex className="cardbox">
        {cardItems.map((item: ItemInterface, index: number) => (
          <Card key={index} className="card-item" onClick={() => item.link && (window.open(item.link, '_blank', 'noopener,noreferrer'))}>
            <Card.Meta title={item.title} description={item.snippet} />
          </Card>
        ))}
      </Flex>
    </div>
  );
};

export default cardLinkList;
