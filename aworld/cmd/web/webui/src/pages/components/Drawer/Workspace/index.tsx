import { getWorkspaceArtifacts } from '@/api/workspace';
import { Tabs, Typography } from 'antd';
import React, { useEffect, useState } from 'react';
import type { ToolCardData } from '../../BubbleItem/utils';
import './index.less';

interface ArtifactItem {
  snippet: string;
  link: string;
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
  useEffect(() => {
    console.log('Workspace中的toolCardData:', toolCardData);
    const fetchWorkspaceArtifacts = async () => {
      try {
        const data = await getWorkspaceArtifacts(sessionId, {
          artifact_types: [toolCardData?.artifacts[0]?.artifact_type],
          artifact_ids: [toolCardData?.artifacts[0]?.artifact_id]
        });
        const list = data?.data?.[0]?.content;
        setArtifacts(Array.isArray(list) ? list : []);
        console.log('工作空间数据: ', data, list);
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
          {toolCardData?.artifacts[0]?.artifact_type === 'WEB_PAGES' && (
            <Tabs.TabPane tab="Web Pages" key="WEB_PAGES">
              <div className="border listwrap">
                <div className="title">Search Results</div>
                <div className="listbox">
                  {artifacts.map((item, index) => (
                    <div className="list" key={index}>
                      <Typography.Link href={item.link} target="_blank">
                        <Typography.Paragraph className="name">{item.title}</Typography.Paragraph >
                        <Typography.Paragraph className="desc" ellipsis={{ rows: 3 }}>{item.snippet}</Typography.Paragraph>
                        <Typography.Paragraph className="link">  {item.link}</Typography.Paragraph>
                      </Typography.Link>
                    </div>
                  ))}
                </div>
              </div>
            </Tabs.TabPane>
          )}
          {toolCardData?.artifacts[0]?.artifact_type === 'IMAGES' && (
            <Tabs.TabPane tab="Images" key="IMAGES">
              <div className="border listwrap">
                <div className="title">Search Results</div>
              </div>
            </Tabs.TabPane>
          )}
        </Tabs>
      </div>
    </>
  );
};

export default Workspace;
