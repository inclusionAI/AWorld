import React, { useState, useCallback } from 'react';
import { Collapse, Space, message } from 'antd';
import type { ToolCardData } from '../utils';
import './index.less';

const { Panel } = Collapse;

interface PanelContent {
  arguments: string;
  results: string;
}

interface DownloadData extends PanelContent {
  timestamp: string;
}

interface Props {
  data: ToolCardData;
}

//下载JSON文件工具函数
const downloadJsonFile = (data: DownloadData) => {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `tool_data_${new Date().getTime()}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

const CardDefault: React.FC<Props> = ({ data }) => {
  // 当前展开的面板keys
  const [activeKeys, setActiveKeys] = useState<string[]>([]);
  const togglePanel = useCallback((panelKey: string) => {
    setActiveKeys(
      (prev) =>
        prev.includes(panelKey)
          ? prev.filter((key) => key !== panelKey) // 如果已展开则折叠
          : [...prev, panelKey] // 如果已折叠则展开
    );
  }, []);

  // 处理下载操作
  const handleDownload = useCallback(() => {
    try {
      downloadJsonFile({
        arguments: data.arguments,
        results: data.results,
        timestamp: new Date().toISOString()
      });
      message.success('Download Successful');
    } catch (error) {
      message.error('Download Failed');
    }
  }, [data]);

  // 处理复制
  const handleCopy = useCallback(
    async (panelKey: string) => {
      try {
        const content = panelKey === '1' ? data.arguments : data.results;
        await navigator.clipboard.writeText(content);
        message.success('Copy Successful');
      } catch (error) {
        message.error('Copy Failed');
      }
    },
    [data]
  );

  //操作按钮
  const renderExtra = useCallback(
    (panelKey: string) => (
      <Space size="small" onClick={(e) => e.stopPropagation()}>
        <span className="action-btn" onClick={() => togglePanel(panelKey)}>
          {activeKeys.includes(panelKey) ? 'Collapsed' : 'Expanded'}
        </span>
        <span className="action-btn" onClick={handleDownload}>
          Save
        </span>
        <span className="action-btn" onClick={() => handleCopy(panelKey)}>
          Copy
        </span>
      </Space>
    ),
    [activeKeys, togglePanel, handleDownload, handleCopy]
  );

  return (
    <div className="defaultbox">
      <Collapse activeKey={activeKeys} onChange={(keys) => setActiveKeys(Array.isArray(keys) ? keys : [keys])}>
        <Panel header="tool_call_arguments" key="1" extra={renderExtra('1')}>
          <pre className="pre-wrap">
            <code>{data.arguments}</code>
          </pre>
        </Panel>
        <Panel header="tool_call_result" key="2" extra={renderExtra('2')}>
          <pre className="pre-wrap">
            <code>{data.results}</code>
          </pre>
        </Panel>
      </Collapse>
    </div>
  );
};

export default React.memo(CardDefault);
