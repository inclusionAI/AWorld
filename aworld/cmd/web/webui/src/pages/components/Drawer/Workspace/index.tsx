import { getWorkspaceArtifacts } from '@/api/workspace';
import { Tabs } from 'antd';
import React, { useEffect, useState } from 'react';
import './index.less';
import type { ToolCardData } from '../../BubbleItem/utils';

interface ArtifactItem {
  key: string;
  title: string;
  content: string;
}

interface WorkspaceProps {
  sessionId: string;
  toolCardData?: ToolCardData;
}

const Workspace: React.FC<WorkspaceProps> = ({ sessionId, toolCardData }) => {
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  // console.log(artifacts);
  useEffect(() => {
    console.log('Workspace中的toolCardData:', toolCardData);
    const fetchWorkspaceArtifacts = async () => {
      try {
        const data = await getWorkspaceArtifacts(sessionId, {
          artifact_types: [toolCardData?.artifacts[0]?.artifact_type],
          artifact_ids: [toolCardData?.artifacts[0]?.artifact_id]
        });
        setArtifacts(Array.isArray(data) ? data : []);
        console.log('工作空间数据: ', data);
      } catch (error) {
        console.error('获取工作空间树失败:', error);
      }
    };

    fetchWorkspaceArtifacts();
  }, [sessionId, toolCardData]);

  return (
    <>
      <div className="border workspacebox">
        <Tabs defaultActiveKey="1" type="card">
          <Tabs.TabPane tab="Web Pages" key="WEB_PAGES">
            <div className="border listwrap">
              <div className="title">Google Search</div>
              <div className="listbox">
                {artifacts.map((item) => (
                  <div className="list" key={item.key}>
                    <div className="name">{item.title}</div>
                    <div>{item.content}</div>
                  </div>
                ))}
              </div>
            </div>
          </Tabs.TabPane>
          <Tabs.TabPane tab="Images" key="IMAGES">

          </Tabs.TabPane>
        </Tabs>
      </div>
    </>
  );
};

export default Workspace;
