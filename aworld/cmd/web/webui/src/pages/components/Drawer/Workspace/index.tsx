import { getWorkspaceArtifacts } from '@/api/workspace';
import { Tabs } from 'antd';
import React, { useEffect, useState } from 'react';
import './index.less';
import type { ToolCardData } from '../../BubbleItem/utils';

interface WorkspaceProps {
  sessionId: string;
  toolCardData?: ToolCardData;
}

const Workspace: React.FC<WorkspaceProps> = ({ sessionId, toolCardData }) => {
  const [artifacts, setArtifacts] = useState<any>(null);
  console.log(artifacts);
  useEffect(() => {
    const fetchWorkspaceArtifacts = async () => {
      try {
        const data = await getWorkspaceArtifacts(sessionId, {
          artifact_types: ['WEB_PAGES'],
          artifact_ids: []
        });
        setArtifacts(data);
        console.log('data: ', data);
      } catch (error) {
        console.error('获取工作空间树失败:', error);
      }
    };

    fetchWorkspaceArtifacts();
  }, [sessionId, toolCardData]);

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
        <Tabs defaultActiveKey="1" type="card">
          <Tabs.TabPane tab="Web Pages" key="WEB_PAGES">
            <div className="border listwrap">
              <div className="title">Google Search</div>
              <div className="listbox">
                {lists.map((item) => (
                  <div className="list" key={item.key}>
                    <div className="name">{item.title}</div>
                    <div>{item.desc}</div>
                  </div>
                ))}
              </div>
            </div>
          </Tabs.TabPane>
          <Tabs.TabPane tab="Images" key="IMAGES"></Tabs.TabPane>
        </Tabs>
      </div>
    </>
  );
};

export default Workspace;
