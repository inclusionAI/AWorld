import React, { useState, useMemo, useEffect, useCallback } from 'react';
import { ThoughtChain } from '@ant-design/x';
import type { ThoughtChainProps, ThoughtChainItem } from '@ant-design/x';
import { Card, Typography, message } from 'antd';
// import { fetchTraceData } from '@/api/trace';

const { Paragraph } = Typography;

interface TraceProps {
  traceId?: string;
  drawerVisible?: boolean;
}

interface TraceNode {
  id: string;
  show_name: string;
  status?: 'success' | 'pending' | 'error';
  children?: TraceNode[];
  description?: string;
}

const Trace: React.FC<TraceProps> = ({ traceId, drawerVisible }) => {
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [traceData, setTraceData] = useState<TraceNode[]>([]);

  const fetchData = useCallback(async () => {
    // if (!traceId || !drawerVisible) return;
    try {
      // const res = await fetchTraceData(traceId);
      const res = {
        data: [
          {
            show_name: '节点1',
            id: 'node1',
            description: '描述内容1-In the process of internal desktop applications development, many different design specs and implementations would be involved, which might cause designers and developers difficulties and duplication and reduce the efficiency of development.',
            children: [
              {
                show_name: '子节点1-1',
                description:
                  '描述内容1-1 In the process of internal desktop applications development, many different design specs and implementations would be involved, which might cause designers and developers difficulties and duplication and reduce the efficiency of development.',

                id: 'subNode1'
              }
            ],
            status: 'success'
          },
          {
            show_name: '节点2',
            id: 'node2',
            description: '描述内容2',
            children: [
              {
                show_name: '子节点2-1',
                description: '描述内容2-After massive project practice and summaries, Ant Design, a design language for background applications, is refined by Ant UED Team, which aims to uniform the user interface specs for internal background projects, lower the unnecessary cost of design differences and implementation and liberate the resources of design and front-end development',
                id: 'subNode1'
              }
            ],
            status: 'pending'
          },
          {
            show_name: '节点3',
            id: 'node3',
            description: '描述内容3 In the process of internal desktop applications development, many different design specs and implementations would be involved, which might cause designers and developers difficulties and duplication and reduce the efficiency of development.',
            children: []
          }
        ]
      };

      const validatedData = (res.data || []).map(item => ({
        ...item,
        status: item.status === 'success' || item.status === 'pending' || item.status === 'error' 
          ? item.status 
          : undefined
      })) as TraceNode[];
      setTraceData(validatedData);
      // 默认展开第一个节点
      if (validatedData?.[0]?.id) {
        setExpandedKeys([validatedData[0].id]);
      }
    } catch (err) {
      message.error('获取Trace数据失败');
      console.error(err);
    }
  }, [traceId, drawerVisible]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const convertToItems = useCallback((nodes: TraceNode[]): ThoughtChainItem[] => {
    return nodes.map((node) => ({
      key: node.id,
      title: node.show_name || node.id, // 使用 show_name 作为标题，没有则使用 id
      description: node.id,
      content: (
        <>
          <Typography>{node.description && <Paragraph>{node.description}</Paragraph>}</Typography>
          {node.children && node.children.length > 0 && <ThoughtChain items={convertToItems(node.children)} />}
        </>
      ),
      status: node.status || 'pending' // 默认状态为 pending
    }));
  }, []);

  const items = useMemo(() => convertToItems(traceData), [traceData, convertToItems]);

  const collapsible: ThoughtChainProps['collapsible'] = useMemo(
    () => ({
      expandedKeys,
      onExpand: (keys: string[]) => setExpandedKeys(keys)
    }),
    [expandedKeys]
  );

  return (
    <Card style={{ width: 650 }}>
      <ThoughtChain items={items} collapsible={collapsible} />
    </Card>
  );
};

export default Trace;
