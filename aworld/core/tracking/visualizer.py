import json
import os
from typing import Dict, Any, List, Optional

from aworld.core.tracking.agent_call_tracker import AgentCallTracker


class AgentCallVisualizer:
    """Agent调用关系可视化工具"""
    
    def __init__(self, tracker: AgentCallTracker):
        """
        初始化可视化工具
        
        Args:
            tracker: AgentCallTracker实例
        """
        self.tracker = tracker
    
    def generate_mermaid_diagram(self) -> str:
        """
        生成Mermaid格式的调用关系图
        
        Returns:
            str: Mermaid图表代码
        """
        mermaid_code = ["graph TD;"]
        
        # 添加节点
        for agent_id, level in self.tracker.agent_levels.items():
            node_style = "class" if level == 0 else "rect"
            mermaid_code.append(f'    {agent_id}["{agent_id}"] {node_style};')
        
        # 添加边
        for caller_id, calls in self.tracker.direct_calls.items():
            for call in calls:
                mermaid_code.append(f'    {caller_id} -->|"agent_direct_call"| {call.callee_id};')
        
        for caller_id, callees in self.tracker.as_tool_calls.items():
            for callee_id, calls in callees.items():
                if calls:  # 确保有调用记录
                    mermaid_code.append(f'    {caller_id} -.->|"as_tool"| {callee_id};')
        
        # 添加样式
        mermaid_code.append("    classDef class fill:#f9f,stroke:#333,stroke-width:2px;")
        mermaid_code.append("    classDef rect fill:#bbf,stroke:#333,stroke-width:1px;")
        
        return "\n".join(mermaid_code)
    
    def generate_html_visualization(self, output_path: str, title: str = "Agent调用关系图"):
        """
        生成HTML格式的可视化文件
        
        Args:
            output_path: 输出文件路径
            title: 图表标题
        """
        mermaid_code = self.generate_mermaid_diagram()
        
        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>{title}</title>
            <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 20px;
                }}
                .mermaid {{
                    margin: 20px 0;
                }}
                .level-info {{
                    margin: 20px 0;
                    padding: 10px;
                    background-color: #f5f5f5;
                    border-radius: 5px;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin-top: 20px;
                }}
                th, td {{
                    border: 1px solid #ddd;
                    padding: 8px;
                    text-align: left;
                }}
                th {{
                    background-color: #f2f2f2;
                }}
                tr:nth-child(even) {{
                    background-color: #f9f9f9;
                }}
            </style>
        </head>
        <body>
            <h1>{title}</h1>
            
            <div class="level-info">
                <h2>Agent级别信息</h2>
                <table>
                    <tr>
                        <th>Agent ID</th>
                        <th>级别</th>
                    </tr>
                    {"".join(f'<tr><td>{agent_id}</td><td>{level}</td></tr>' for agent_id, level in self.tracker.agent_levels.items())}
                </table>
            </div>
            
            <h2>调用关系图</h2>
            <div class="mermaid">
            {mermaid_code}
            </div>
            
            <script>
                mermaid.initialize({{ startOnLoad: true }});
            </script>
        </body>
        </html>
        """
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_template)
    
    def export_visualization(self, output_dir: str, task_id: str):
        """
        导出调用关系可视化
        
        Args:
            output_dir: 输出目录
            task_id: 任务ID
        """
        os.makedirs(output_dir, exist_ok=True)
        
        # 导出JSON数据
        json_path = os.path.join(output_dir, f"agent_calls_{task_id}.json")
        self.tracker.export_to_json(json_path)
        
        # 导出HTML可视化
        html_path = os.path.join(output_dir, f"agent_calls_{task_id}.html")
        self.generate_html_visualization(html_path, f"Agent调用关系图 - {task_id}")
        
        return {
            "json_path": json_path,
            "html_path": html_path
        } 