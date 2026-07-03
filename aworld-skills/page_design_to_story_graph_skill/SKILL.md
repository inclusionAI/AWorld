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
1. **容器必须有明确高度**：`vis.js` 的画布容器（如 `<div id="mynetwork"></div>`）**必须**在 CSS 中指定明确的高度（例如 `height: 800px;` 或 `height: 100vh;`）。如果仅使用 `flex: 1` 或不设置高度，画布初始化时高度为 0，会导致页面完全空白。
2. **数据注入安全**：将 Python 字典/列表注入到 JS 代码中时，**必须**使用 `json.dumps(data, ensure_ascii=False)`。这能完美处理中文和文本中可能出现的单双引号，防止 JS 语法截断报错。
3. **DOM 加载顺序**：`vis.Network` 的初始化代码必须包裹在 `window.onload = function() { ... }` 中，确保 HTML 容器渲染完毕后再挂载画布。
4. **稳定的 CDN**：推荐使用国内稳定的 CDN（如 `https://lib.baomitu.com/vis-network/9.1.2/standalone/umd/vis-network.min.js`），避免因网络问题导致库加载失败。

### 🎨 视觉与交互设计要点
- **节点分组 (Groups)**：为起点（start）、普通分支（branch）、结局（ending）设置不同的 `group`，并在 `vis.js` 的 `options.groups` 中为它们配置不同的颜色和形状（如结局用红色椭圆，分支用黄色方框），一目了然。
- **层级排版 (Hierarchical Layout)**：在 `options.layout` 中开启 `hierarchical: { direction: 'UD' }`（从上到下），这会让剧情树像瀑布一样整齐排列，而不是乱作一团。

---

## 3. Python 转换脚本标准模板

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

    # 1. 解析 Markdown 提取节点和边
    for line in content.split('\n'):
        # 匹配节点：【页面_xxx：标题】 或 【结局 1：标题】
        node_match = re.search(r'【(页面_[a-zA-Z0-9_]+|结局\s*\d+)：(.*?)】', line)
        if node_match:
            raw_id = node_match.group(1).replace(' ', '_').replace('结局_', 'ending_').replace('页面_', 'page_')
            title = node_match.group(2).strip()
            current_node = raw_id
            
            # 划分节点组别
            group = 'branch'
            if current_node.startswith('ending_'): group = 'ending'
            elif current_node in ['page_cover', 'page_epilogue']: group = 'start'
                
            nodes.append({"id": current_node, "label": title, "group": group})
            node_ids.add(current_node)
            continue
            
        # 匹配连线：[选项A]：动作 -> [跳转至 页面_2A]
        if current_node:
            trans_match = re.search(r'\[(.*?)\]：(.*?)->\s*\[(?:跳转至\s*)?(页面_[a-zA-Z0-9_]+|结局\s*\d+)\]', line)
            if trans_match:
                option_label = trans_match.group(1).strip()
                target_raw = trans_match.group(3).replace(' ', '_').replace('结局_', 'ending_').replace('页面_', 'page_')
                edges.append({"from": current_node, "to": target_raw, "label": option_label})

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
    <script type="text/javascript" src="https://lib.baomitu.com/vis-network/9.1.2/standalone/umd/vis-network.min.js"></script>
    <style>
        body {{ font-family: sans-serif; margin: 0; padding: 20px; background-color: #f5f7fa; }}
        .header {{ background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; text-align: center; }}
        /* ⚠️ 极其重要：必须指定明确的高度 */
        #mynetwork {{ width: 100%; height: 800px; background-color: #ffffff; border-radius: 8px; border: 1px solid #ddd; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>剧情分支可视化</h1>
        <p>支持鼠标滚轮缩放、拖拽节点。点击节点可高亮相关分支。</p>
    </div>
    <div id="mynetwork"></div>

    <script type="text/javascript">
        window.onload = function() {{
            // ⚠️ 极其重要：使用 json.dumps 确保数据安全注入
            var nodes = new vis.DataSet({json.dumps(nodes, ensure_ascii=False)});
            var edges = new vis.DataSet({json.dumps(edges, ensure_ascii=False)});

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
            new vis.Network(container, data, options);
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

## 4. 进阶技巧：点击节点显示详细剧情 (Interactive Details Panel)

为了让可视化图表不仅能看“结构”，还能看“内容”，我们可以通过以下方式实现点击节点展示详细剧情的交互功能：

### 核心实现思路
1. **Python 解析层增强**：在遍历 `page_design.md` 时，不仅要提取节点标题，还要将节点标题下方的所有正文文本（包括选项）收集起来，作为 `story_text` 属性存入节点字典中。
2. **HTML 布局调整**：使用 Flexbox 布局，将页面分为左右两部分。左侧（如 `flex: 7`）放置 `vis.js` 画布，右侧（如 `flex: 3`）放置一个详情面板（Details Panel）。
3. **JS 事件监听**：利用 `vis.js` 提供的 `network.on("click", function(params) {...})` 事件，获取当前点击的节点 ID，从数据集中取出对应的 `story_text`，并渲染到右侧的详情面板中。
4. **文本格式化**：在 JS 中，可以使用正则表达式（如 `replace(/(\[选项.*?\]：.*?->.*)/g, '<div class="option-line">$1</div>')`）将剧情中的“选项”行高亮显示，增强阅读体验。

通过这种方式，人类策划或开发者可以在左侧梳理宏观的网状结构，在右侧沉浸式阅读微观的剧情文案，极大地提升了剧本审查的效率。
