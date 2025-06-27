import { Flex, Typography } from 'antd';
import React from 'react';
import './index.less';
const { Text } = Typography;

interface WorkspaceProps {
  sessionId: string;
}

const Workspace: React.FC<WorkspaceProps> = ({ sessionId }) => {
  const [currentTab, setCurrentTab] = React.useState('1');
  const tabs = [
    {
      key: '1',
      name: 'Agent 1的电脑',
      desc: '正在使用搜索工作'
    },
    {
      key: '2',
      name: 'Agent 2的电脑',
      desc: '正在使用搜索工作'
    },
    {
      key: '3',
      name: 'Agent 3的电脑',
      desc: '正在使用搜索工作'
    }
  ];
  const lists = [
    {
      key: '1',
      title: '深度解析特斯拉新款人形机器人—擎天柱第二代 (Optimus Gen-2)',
      desc: '深度解析特斯拉新款人形机器人—擎天柱第二代 (Optimus Gen-2)深度解析特斯拉新款人形机器人—擎天柱第二代 (Optimus Gen-2)'
    },
    {
      key: '2',
      title: '深度解析特斯拉新款人形机器人—擎天柱第二代 (Optimus Gen-2)',
      desc: '深度解析特斯拉新款人形机器人—擎天柱第二代 (Optimus Gen-2)深度解析特斯拉新款人形机器人—擎天柱第二代 (Optimus Gen-2)'
    },
    {
      key: '3',
      title: '深度解析特斯拉新款人形机器人—擎天柱第二代 (Optimus Gen-2)',
      desc: '深度解析特斯拉新款人形机器人—擎天柱第二代 (Optimus Gen-2)深度解析特斯拉新款人形机器人—擎天柱第二代 (Optimus Gen-2)'
    },
    {
      key: '4',
      title: '深度解析特斯拉新款人形机器人—擎天柱第二代 (Optimus Gen-2)',
      desc: '深度解析特斯拉新款人形机器人—擎天柱第二代 (Optimus Gen-2)深度解析特斯拉新款人形机器人—擎天柱第二代 (Optimus Gen-2)'
    }
  ];
  return (
    <>
      <div className="border workspacebox">
        <div>从 api/workspaces/{sessionId}/tree 获取</div>
        <Flex className="tabbox" justify="space-between">
          {tabs.map((item) => (
            <Flex className={`border tab ${item.key === currentTab ? 'active' : ''}`} key={item.key} align="center" onClick={() => setCurrentTab(item.key)}>
              <div className="num">{item.key}</div>
              <div>
                <div className="name">{item.name}</div>
                <div className="desc">{item.desc}</div>
              </div>
            </Flex>
          ))}
        </Flex>
        <div className="border listwrap">
          <div className="title">Google Search</div>
          <div className="listbox">
            {lists.map((item) => (
              <div className="list" key={item.key}>
                <div className="name">{item.title}</div>
                <Text ellipsis className="desc">
                  {item.desc}
                </Text>
              </div>
            ))}
          </div>
        </div>
      </div>
      <p>Session ID: {sessionId}</p>
    </>
  );
};

export default Workspace;
