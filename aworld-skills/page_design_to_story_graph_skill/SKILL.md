---
name: page_design_to_story_graph_skill
description: 本指南总结了如何将基于文本的剧情设计文档（比如`page_design.md` 或者 用户指定文件）稳定、高效地转换为支持交互的可视化 HTML 剧情分支图。
---

# 剧情设计文档到可视化分支图转换指南 (Page Design to Story Graph Skill)

本指南总结了如何将基于文本的剧情设计文档（`page_design.md`）稳定、高效地转换为支持交互的可视化 HTML 剧情分支图。

## 1. 核心结论：是否需要中间步骤？

**最佳方案**是：直接使用 Python 脚本解析 `page_design.md`，提取节点（Nodes）和连线（Edges）数据，然后通过 `json.dumps` 将数据直接注入到包含 `vis.js` 渲染引擎的 HTML 模板中。

**为什么放弃 Mermaid 而选择 Vis.js？**
- Mermaid 在处理大量中文节点、复杂网状交叉连线时，容易出现语法冲突（Syntax Error）或渲染崩溃。
- Vis.js 支持无极缩放、节点拖拽、点击高亮上下游分支，且对复杂网状拓扑图的自动排版（Hierarchical Layout）支持极佳，非常适合人类直观梳理多分支剧情。

---

## 2. 最佳实践方案与关键注意事项

在编写 Python 脚本生成 `graph.html` 时，必须严格遵守以下避坑要点：

### ⚠️ 致命踩坑点（导致白屏/不显示的原因）
1. **容器必须有明确高度**：`vis.js` 的画布容器（如 `<div id="mynetwork"></div>`）**必须**在 CSS 中指定明确的高度（例如 `height: 800px;` 或 `min-height: 600px;`）。如果仅使用 `flex: 1` 或不设置高度，画布初始化时高度为 0，会导致页面完全空白。
2. **数据注入安全**：将 Python 字典/列表注入到 JS 代码中时，**必须**使用 `json.dumps(data, ensure_ascii=False)`。这能完美处理中文和文本中可能出现的单双引号，防止 JS 语法截断报错。
3. **DOM 加载顺序**：`vis.Network` 的初始化代码必须包裹在 `window.onload = function() { ... }` 中，确保 HTML 容器渲染完毕后再挂载画布。
4. **稳定的 CDN 或本地化**：强烈推荐使用全球稳定的 CDN（如 `https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.2/standalone/umd/vis-network.min.js`）。如果用户的网络环境拦截了外部 CDN，请务必通过 `curl` 将该 JS 文件下载到本地，并在 HTML 中使用相对路径引用。**切勿使用已失效的 baomitu 等小众 CDN**。

### 🎨 视觉与交互设计要点
- **节点分组 (Groups)**：为起点（start）、普通分支（branch）、结局（ending）设置不同的 `group`，并在 `vis.js` 的 `options.groups` 中为它们配置不同的颜色和形状（如结局用红色椭圆，分支用黄色方框），一目了然。
- **层级排版 (Hierarchical Layout)**：在 `options.layout` 中开启 `hierarchical: { direction: 'UD' }`（从上到下），这会让剧情树像瀑布一样整齐排列，而不是乱作一团。

---

## 3. 进阶技巧：点击节点显示详细剧情 (Interactive Details Panel)

为了让可视化图表不仅能看“结构”，还能看“内容”，我们推荐使用左右分栏的交互布局。

### 核心实现思路
1. **Python 解析层增强**：在遍历 `page_design.md` 时，不仅要提取节点标题，还要将节点标题下方的所有正文文本（包括选项）收集起来，作为 `title` 属性存入节点字典中。
2. **HTML 布局调整**：使用 Flexbox 布局，将页面分为左右两部分。左侧（如 `flex: 7`）放置 `vis.js` 画布，右侧（如 `flex: 3`）放置一个详情面板（Details Panel）。**注意：左侧画布必须加上 `min-height: 600px;` 防止高度塌陷。**
3. **JS 事件监听**：利用 `vis.js` 提供的 `network.on("click", function(params) {...})` 事件，获取当前点击的节点 ID，从数据集中取出对应的 `title`，并渲染到右侧的详情面板中。

### 完整 Python 转换脚本标准模板（包含进阶交互）

以下是经过验证的、可直接复用的 Python 脚本模板：

```python
import re
import json

def generate_story_graph(input_md_path='page_design.md', output_html_path='story_graph.html'):
    with open(input_md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    nodes = []
    edges = []
    node_ids = set()
    current_node = None
    current_text = []

    # 1. 解析 Markdown 提取节点和边
    for line in content.split('\n'):
        # 匹配节点：【页面_xxx：标题】 或 【结局 1：标题】
        node_match = re.search(r'【(页面_[a-zA-Z0-9_]+|结局\s*\d+)：(.*?)】', line)
        if node_match:
            # 保存上一个节点的正文内容
            if current_node:
                for node in nodes:
                    if node['id'] == current_node:
                        node['title'] = '<br>'.join(current_text)
                        break
            
            raw_id = node_match.group(1).replace(' ', '_').replace('结局_', 'ending_').replace('页面_', 'page_')
            title = node_match.group(2).strip()
            current_node = raw_id
            current_text = []
            
            # 划分节点组别
            group = 'branch'
            if current_node.startswith('ending_'): group = 'ending'
            elif current_node in ['page_cover', 'page_epilogue']: group = 'start'
                
            nodes.append({"id": current_node, "label": title, "group": group})
            node_ids.add(current_node)
            continue
            
        if current_node:
            current_text.append(line.strip())
            # 匹配连线：[选项A]：动作 -> [跳转至 页面_2A]
            trans_match = re.search(r'\[(.*?)\]：(.*?)->\s*\[(?:跳转至\s*)?(页面_[a-zA-Z0-9_]+|结局\s*\d+)\]', line)
            if trans_match:
                option_label = trans_match.group(1).strip()
                target_raw = trans_match.group(3).replace(' ', '_').replace('结局_', 'ending_').replace('页面_', 'page_')
                edges.append({"from": current_node, "to": target_raw, "label": option_label})

    # 保存最后一个节点的正文内容
    if current_node:
        for node in nodes:
            if node['id'] == current_node:
                node['title'] = '<br>'.join(current_text)
                break

    # 2. 补充隐式连线 (如封面到第一页，结局到寄语)
    if 'page_cover' in node_ids and 'page_0' in node_ids:
        edges.append({"from": 'page_cover', "to": 'page_0', "label": '开始'})
    for node in node_ids:
        if node.startswith('ending_'):
            edges.append({"from": node, "to": 'page_epilogue', "label": '结束'})

    # 3. 构建 HTML 模板并注入 JSON 数据
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>剧情分支图</title>
    <!-- 推荐使用 cdnjs，如果网络不通，请下载到本地并改为相对路径 src="./vis-network.min.js" -->
    <script type="text/javascript" src="https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.2/standalone/umd/vis-network.min.js"></script>
    <style>
        body {{ font-family: sans-serif; margin: 0; padding: 20px; background-color: #f5f7fa; display: flex; height: 100vh; box-sizing: border-box; }}
        .left-panel {{ flex: 7; display: flex; flex-direction: column; padding-right: 20px; }}
        .right-panel {{ flex: 3; background: white; padding: 20px; border-radius: 8px; border: 1px solid #ddd; overflow-y: auto; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        .header {{ background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        /* ⚠️ 极其重要：Flex 布局下必须指定 min-height，否则画布高度会塌陷为 0 */
        #mynetwork {{ flex: 1; min-height: 600px; width: 100%; background-color: #ffffff; border-radius: 8px; border: 1px solid #ddd; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        .option-line {{ color: #e67e22; font-weight: bold; margin-top: 10px; }}
        h2 {{ margin-top: 0; color: #2c3e50; }}
        p {{ line-height: 1.6; color: #34495e; }}
    </style>
</head>
<body>
    <div class="left-panel">
        <div class="header">
            <h1>剧情分支图</h1>
            <p>支持鼠标滚轮缩放、拖拽节点。点击节点可在右侧查看详细剧情。</p>
        </div>
        <div id="mynetwork"></div>
    </div>
    <div class="right-panel" id="details-panel">
        <h2>节点详情</h2>
        <p>请点击左侧图表中的节点查看详细剧情内容。</p>
    </div>

    <script type="text/javascript">
        window.onload = function() {{
            // ⚠️ 极其重要：使用 json.dumps 确保数据安全注入
            var nodesData = {json.dumps(nodes, ensure_ascii=False)};
            var edgesData = {json.dumps(edges, ensure_ascii=False)};
            
            var nodes = new vis.DataSet(nodesData);
            var edges = new vis.DataSet(edgesData);

            var container = document.getElementById('mynetwork');
            var data = {{ nodes: nodes, edges: edges }};
            var options = {{
                nodes: {{ shape: 'box', margin: 10, font: {{ size: 16 }} }},
                edges: {{ arrows: 'to', font: {{ size: 12, align: 'middle' }}, smooth: {{ type: 'cubicBezier', roundness: 0.4 }} }},
                groups: {{
                    start: {{ color: {{ background: '#e3f2fd', border: '#0984e3' }}, borderWidth: 2 }},
                    branch: {{ color: {{ background: '#fff3e0', border: '#e67e22' }}, borderWidth: 1 }},
                    ending: {{ color: {{ background: '#ffe6e6', border: '#d63031' }}, borderWidth: 2, shape: 'ellipse' }}
                }},
                layout: {{
                    hierarchical: {{ direction: 'UD', sortMethod: 'directed', nodeSpacing: 150, levelSeparation: 150 }}
                }},
                physics: false // 关闭物理引擎以保持层级稳定
            }};
            var network = new vis.Network(container, data, options);
            
            // 点击事件监听
            network.on("click", function(params) {{
                if (params.nodes.length > 0) {{
                    var nodeId = params.nodes[0];
                    var node = nodes.get(nodeId);
                    var detailsPanel = document.getElementById('details-panel');
                    
                    var titleHtml = "<h2>" + node.label + " (" + node.id + ")</h2>";
                    var contentHtml = "<p>" + (node.title || "暂无详细内容") + "</p>";
                    
                    // 高亮选项
                    contentHtml = contentHtml.replace(/(\[选项.*?\]：.*?->.*)/g, '<div class="option-line">$1</div>');
                    
                    detailsPanel.innerHTML = titleHtml + contentHtml;
                }}
            }});
        }};
    </script>
</body>
</html>"""

    with open(output_html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"Graph successfully generated at {output_html_path}")

if __name__ == "__main__":
    generate_story_graph()
```
