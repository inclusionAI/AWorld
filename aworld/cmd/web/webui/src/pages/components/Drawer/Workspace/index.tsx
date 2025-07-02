import { getWorkspaceArtifacts } from '@/api/workspace';
import { Tabs, Typography, Image } from 'antd';
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
  const [imgUrl, setImgUrl] = useState<string | undefined>();

  useEffect(() => {
    const fetchWorkspaceArtifacts = async () => {
      try {
        const data = await getWorkspaceArtifacts(sessionId, {
          artifact_types: [toolCardData?.artifacts[0]?.artifact_type],
          artifact_ids: [toolCardData?.artifacts[0]?.artifact_id],
        });
        const content = data?.data?.[0]?.content;
        if (toolCardData?.card_type === 'tool_call_card_link_list') {
          setArtifacts(Array.isArray(content) ? content : []);
        } else {
          setImgUrl(content);
        }
      } catch (error) {
        console.error('Failed to get workspace tree:', error);
      }
    };

    fetchWorkspaceArtifacts();
  }, [sessionId, toolCardData]);

  const tabItems = [
    toolCardData?.card_type === 'tool_call_card_link_list' && {
      key: 'WEB_PAGES',
      label: 'Web Pages',
      children: (
        <div className="border listwrap">
          <div className="title">Search Results</div>
          <div className="listbox">
            {artifacts.map((item, index) => (
              <div className="list" key={index}>
                <Typography.Link href={item.link} target="_blank">
                  <Typography.Paragraph className="name" ellipsis={{ rows: 1 }}>
                    {item.title}
                  </Typography.Paragraph>
                  <Typography.Paragraph className="desc" ellipsis={{ rows: 3 }}>
                    {item.snippet}
                  </Typography.Paragraph>
                  <Typography.Paragraph className="link" ellipsis={{ rows: 1 }}>
                    {item.link}
                  </Typography.Paragraph>
                </Typography.Link>
              </div>
            ))}
          </div>
        </div>
      ),
    },
    toolCardData?.card_type === 'tool_call_card_default' && {
      key: 'IMAGES',
      label: 'Images',
      children: (
        <div className="border listwrap">
          <div className="title">Search Results</div>
          <Image src={imgUrl} />
        </div>
      ),
    },
  ].filter(Boolean) as { key: string; label: string; children: React.ReactNode }[];

  return (
    <div className="border workspacebox">
      <Tabs defaultActiveKey="1" type="card" items={tabItems} />
    </div>
  );
};

export default Workspace;