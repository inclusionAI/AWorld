import { getWorkspaceTree } from '@/api/workspace';
import { Flex, Tree } from 'antd';
import React, { useEffect, useState } from 'react';
import './index.less';

interface WorkspaceProps {
  sessionId: string;
}

const Workspace: React.FC<WorkspaceProps> = ({ sessionId }) => {
  const [currentTab, setCurrentTab] = React.useState('1');
  const [treeData, setTreeData] = useState<any>(null);
  console.log(treeData)
  useEffect(() => {
    const mapTreeNode = (node: any) => {
      return {
        title: node.name,
        key: node.id,
        children: node.children ? node.children.map(mapTreeNode) : []
      }
    }

    const fetchWorkspaceTree = async () => {
      try {
        const data = await getWorkspaceTree(sessionId);
        console.log("data: ", data)
        const mapTreeData = [mapTreeNode(data)];
        setTreeData(mapTreeData);
      } catch (error) {
        console.error('获取工作空间树失败:', error);
      }
    };

    fetchWorkspaceTree();
  }, [sessionId]);
  const tabs = [
    {
      key: '1',
      name: 'Agent 1',
      desc: '正在使用搜索工作'
    },
    {
      key: '2',
      name: 'Agent 2',
      desc: '正在使用搜索工作'
    },
    {
      key: '3',
      name: 'Agent 3',
      desc: '正在使用搜索工作'
    }
  ];
  return (
    <>
      <div className="border workspacebox">
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
          <div className="title">Agent1 Workspace</div>
          <div className="listbox">
            <Tree
              checkable
              treeData={treeData}
            />
          </div>
        </div>
      </div>
    </>
  );
};

export default Workspace;
