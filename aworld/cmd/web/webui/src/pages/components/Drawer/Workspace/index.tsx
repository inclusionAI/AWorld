import { getWorkspaceTree } from '@/api/workspace';
import { Tree } from 'antd';
import React, { useEffect, useState } from 'react';
import './index.less';

interface WorkspaceProps {
  sessionId: string;
}

const Workspace: React.FC<WorkspaceProps> = ({ sessionId }) => {
  // const [currentTab, setCurrentTab] = React.useState('1');
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
  return (
    <>
      <div className="border listwrap workspacebox">
        <div className="title">Agent Workspace</div>
        <div className="listbox">
          <Tree
            checkable
            treeData={treeData}
          />
        </div>
      </div>
    </>
  );
};

export default Workspace;
