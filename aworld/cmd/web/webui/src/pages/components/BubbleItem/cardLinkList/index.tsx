import { CheckOutlined, SearchOutlined } from '@ant-design/icons';
import { Button, Card, Flex } from 'antd';
import React from 'react';
import type { ToolCardData } from '../utils';
import './index.less';

interface Props {
  sessionId: string;
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
        <Flex justify="space-between" align="center" className="card-length">
          <Button icon={<SearchOutlined />}>{`search keywords: ${data?.card_data?.query || ''}`}</Button>
          <Flex align="center">
            <CheckOutlined className="check-icon" />
            {items.length} results
          </Flex>
        </Flex>
      )}
      <div className="border-box">
        <Flex className="cardbox">
          {cardItems.map((item: ItemInterface, index: number) => (
            <Card title={item.title} key={index} className="card-item" onClick={() => item.link && window.open(item.link, '_blank', 'noopener,noreferrer')}>
              <p>{item.snippet}</p>
              {/* 提取域名展示 */}
              <p style={{ maxWidth: '125px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.link}</p>
            </Card>
          ))}
        </Flex>
      </div>
    </div>
  );
};

export default cardLinkList;
