import React, { useEffect, useRef } from 'react';
import mermaid from 'mermaid';
import './index.less'

interface TraceProps {
  sessionId: string;
}

const Trace: React.FC<TraceProps> = ({ sessionId }) => {
  const diagramRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const initializeMermaid = async () => {
      try {
        await mermaid.initialize({
          startOnLoad: false,
          theme: 'default',
          fontFamily: 'Arial',
          securityLevel: 'loose'
        });

        if (diagramRef.current) {
          await mermaid.run({
            nodes: [diagramRef.current]
          });
        }
      } catch (err) {
        console.error('Mermaid initialization error:', err);
      }
    };

    initializeMermaid();
  }, []);

  return (
    <div className='tracebox'>
      <div ref={diagramRef} className="mermaid">
        {`graph TD
          A[会话开始] --> B[初始化环境]
          B --> C[加载配置]
          C --> D[执行任务]
          D --> E{成功?}
          E -->|是| F[保存结果]
          E -->|否| G[错误处理]
          F --> H[会话结束]
          G --> H`}
      </div>
      <p style={{textAlign:'center'}}>Session ID: {sessionId}</p>
    </div>
  );
};

export default Trace;
