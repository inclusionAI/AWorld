# 布局模版技能说明：`split_cht_txt`（左右图表文字）  
**版本：v2.4（彻底解决图表溢出与裁剪问题）**  
**最后更新：2026年1月27日**

---

## 模版用途  
适用于在 PPT、 PDF 或静态 HTML 页面中展示 **左侧嵌入 Chart.js 图表**、**右侧配以简要文字说明** 的信息布局场景。  
确保内容在标准 16:9 幻灯片尺寸（`720pt × 405pt`）内严格防溢出，且可无依赖渲染为静态图像。

---

## 📐 核心布局规则

| 项目 | 要求 |
|------|------|
| **整体尺寸** | 固定 `720pt × 405pt`（16:9 幻灯片比例） |
| **左右分区** | 左侧图表容器宽 `320pt`，右侧文字区域弹性填充剩余空间 |
| **文字区域限制** | 最多 **4 个列表项**，每项必须包含 `<h4>` + `<p>`，**禁止额外嵌套 `<div>` 或其他块级元素** |
| **图标来源** | 使用 Font Awesome（通过 CDN 或本地路径引入 `.css`） |
| **防溢出强制要求** | 所有子容器必须设置 `overflow: hidden`，且图表/文字内容不得超出其父容器边界 |
| **✅ 新增：图表容器高度硬限制** | **`.chart-wrapper` 必须显式设置固定高度（推荐 `280pt`）** |

> 💡 **为什么需要固定高度？**  
> Flex 布局在复杂嵌套中无法可靠约束 Chart.js 的内部绘图区域。**显式设置容器高度是最可靠的防溢出手段**。

---

## 📊 图表生成规范（使用 Chart.js）

### ✅ 允许的图表类型（全面覆盖）
Chart.js 支持 **8 种核心图表类型**，本模版均已适配。为保证在有限空间内的可读性，对每种类型设定了**数据点数量上限**：

| 图表类型 (Type) | 应用场景 | 复杂度限制 |
|-----------------|----------|------------|
| **柱状图 (`bar`)** | 类别对比、数量比较 | 最多 **6 根柱子** |
| **折线图 (`line`)** | 趋势分析、时间序列 | 最多 **7 个数据点** |
| **饼图 (`pie`)** | 展示分类占比 | 最多 **5 个扇区** |
| **环形图 (`doughnut`)** | 展示分类占比（带中心留白） | 最多 **5 个扇区** |
| **雷达图 (`radar`)** | 多维度数据对比、能力评估 | 最多 **6 个维度** |
| **极地图 (`polarArea`)** | 展示分布数据（角度=类别，半径=值） | 最多 **6 个扇区** |
| **散点图 (`scatter`)** | 显示两个变量间的关系 | 最多 **15 个数据点** |
| **气泡图 (`bubble`)** | 展示三维数据（X, Y, 半径） | 最多 **10 个气泡** |

> ⚠️ **重要提示**：  
> - **雷达图和极地图** 对标签长度敏感，**维度/类别名称必须简短（≤4个汉字或8个英文字符）**，否则会溢出画布。  
> - **散点图和气泡图** 不显示传统的 X/Y 轴标题，所有说明应通过图例或右侧文字区域传达。

---

### ✅ 数据标注要求
- 所有数据必须**真实、明确、可验证**
- 图表中**必须显示数值标签**（可通过 `tooltip` 显式呈现）
- **禁止虚构比例**（如“占比约三分之一”但无具体数字）

---

### ✅ HTML 结构约束
- **图表标题** 必须使用 `<h3 class="chart-title">...</h3>`，并且**必须作为 `.chart-wrapper` 的前一个兄弟元素**。
- **图表内容区域** 使用 `<canvas id="myChart"></canvas>`，并包裹在一个 **新的、无 `padding` 的容器 `.chart-wrapper`** 中。
- **右侧文字** 严格使用以下结构：
  ```html
  <ul class="bio-list">
    <li class="bio-item">
      <i class="fas fa-xxx"></i>
      <div class="bio-text">
        <h4>小标题</h4>
        <p>详情内容（≤25字）</p>
      </div>
    </li>
    <!-- 最多4项 -->
  </ul>
  ```
- **所有文字内容（包括标题、段落、图例）不得用 `<div>` 包裹**，应直接使用语义化标签（`<h3>`, `<h4>`, `<p>`）
- **图表初始化脚本必须包裹在 `DOMContentLoaded` 事件监听器内**，确保 DOM 元素（尤其是 `<canvas>`）已就绪再执行绘图。
- **禁止将 Chart.js 初始化代码置于 `<head>` 或未等待 DOM 加载的 `<script>` 中**，否则可能导致 `getContext('2d')` 失败或渲染空白。

#### 正确的 HTML 结构示例：
```html
<div class="content-container">
    <div class="chart-section">
        <h3 class="chart-title">图表标题</h3>
        <div class="chart-wrapper">
            <!-- 请在此处实现一个图表，禁止使用占位符 -->
            <canvas id="myChart"></canvas>
        </div>
    </div>
    
    <div class="right-content">
        <!-- ... -->
    </div>
</div>
```

---

### ✅ Chart.js 配置强制要求

#### 1. 响应式与尺寸
```js
options: {
  responsive: true,
  maintainAspectRatio: false,
}
```

#### 2. 动画完成后自动转为 PNG（**简化且健壮**）
**不再需要手动计算高度**。直接使用 `canvas` 的自然尺寸。
```js
animation: {
  duration: 1000,
  onComplete: function() {
    const canvas = document.getElementById('myChart');
    if (!canvas) return;

    const wrapper = canvas.parentElement;
    const img = new Image();
    img.src = canvas.toDataURL('image/png');
    img.style.width = '100%';
    img.style.height = 'auto'; // 👈 关键：让高度自适应
    img.style.display = 'block';

    wrapper.innerHTML = '';
    wrapper.appendChild(img);
  }
}
```

#### 3. 样式一致性
- 字体：使用 `--font-heading`（即 `'Inter', Arial, sans-serif`）
- 颜色：从 CSS 变量取值（如 `--color-icon`, `--color-primary`）
- 背景：图表容器必须为 `background-color: white`，**禁用 `linear-gradient`**
---

#### 4. 脚本执行时机
- 所有 Chart.js 初始化逻辑**必须位于 `document.addEventListener('DOMContentLoaded', ...)` 回调函数内部**。
- 此约束与“动画完成后转 PNG”机制配合，共同保障：
  1. 图表在 DOM 就绪后创建；
  2. 动画播放完毕后替换为静态 `<img>`；
  3. 最终输出为**无 JS 依赖的纯静态内容**，适用于 PPT、PDF 等环境。

### ✅ 防溢出布局约束

#### 图表区域 (`chart-section`)
- 宽度固定为 `320pt`
- **高度必须固定（推荐 `280pt`）**
- 设置 `display: flex; flex-direction: column;`
- **设置 `overflow: hidden`**
- **移除 `padding`**。内边距应由其子元素控制。

#### 图表标题 (`chart-title`)
- 通过 `margin` 控制与 `.chart-wrapper` 的间距。
- 示例：
  ```css
  .chart-title {
    font-size: 14pt;
    font-weight: 600;
    text-align: center;
    margin: 0 0 10pt 0; /* 下边距代替之前的 padding */
    color: #2d3748;
  }
  ```

#### 图表包装器 (`chart-wrapper`) (**关键**)
- **`height: 100%`**，占据 `.chart-section` 的全部剩余空间。
- **`padding: 0`**，提供一个干净的、无干扰的绘图环境给 Chart.js。
- **`overflow: hidden`**，防止任何意外溢出。
- 正确的 CSS 如下：
  ```css
  .chart-wrapper {
    width: 100%;
    height: 100%;
    overflow: hidden;
  }
  .chart-wrapper canvas {
    width: 100%;
    height: 100%;
    display: block;
  }
  ```

#### 右侧文字区域 (`right-content`)
- 使用 `flex: 1` 占据剩余空间
- 文字行高 (`line-height`) ≤ `1.5`
- 段落字体大小 ≤ `12pt`
- **总高度不得超过图表容器高度**

#### 特殊图表配置要求
- **饼图/环形图/极地图**：图例必须设为 `position: 'bottom'`，并**限制图例宽度防止换行**
  ```js
  plugins: {
    legend: {
      position: 'bottom',
      maxWidth: 300, // 👈 关键！防换行
      labels: { font: { size: 10 } }
    }
  }
  ```
- **柱状图/折线图/散点图/气泡图**：**禁用 Y/X 轴标题**（因其易导致溢出）
  ```js
  scales: {
    y: { title: { display: false } },
    x: { title: { display: false } }
  }
  ```
- **雷达图**：必须简化刻度和标签
  ```js
  scales: {
    r: {
      pointLabels: {
        font: { size: 9 }, // 小字号
        // 确保传入的 labels 本身就很短
      },
      ticks: { display: false } // 隐藏同心圆上的数值
    }
  }
  ```

> 🛠️ **溢出检查清单**：
> - [ ] `.slide-container` 设置 `overflow: hidden`
> - [ ] **`.chart-section` 设置固定高度（如 `280pt`）**
> - [ ] **`.chart-section` 设置 `overflow: hidden` 且无 `padding`**
> - [ ] **`.chart-wrapper` 设置 `height: 100%` 且 `padding: 0`**
> - [ ] **`canvas` 设置 `height: 100%`**
> - [ ] **未使用任何坐标轴标题**
> - [ ] 饼图/极地图图例设置了 `maxWidth` 防止换行
> - [ ] 雷达图的维度标签简短且字号小

---

### ❌ 禁止事项
| 行为 | 原因 |
|------|------|
| 在 `<script>` 外动态修改 DOM 结构 | 破坏静态渲染一致性 |
| **违反各图表类型的复杂度限制** | 导致视觉混乱或溢出 |
| **雷达图/极地图使用长标签** | 标签会伸出画布边界 |
| 坐标轴含长文本或复杂单位 | 违反“禁止大量文字解释坐标轴”规则 |
| 图表区域使用 `<div>` 替代 `<canvas>` | 无法触发 Chart.js 渲染 |
| 文字区域使用 `<div><p>...</p></div>` | 违反“文字不能用 div 包括”要求 |
| 在 `DOMContentLoaded` 之外初始化 Chart.js | 可能因元素未加载导致渲染失败 |
| 使用 `window.onload` 替代 `DOMContentLoaded` | 不必要地延迟执行（需等图片等资源），且无收益 |
| **图表容器未设固定高度** | 导致 Chart.js 内容溢出幻灯片边界 |
| **Canvas 使用 `height: calc(100% - Xpx)`** | **会导致轴标签被裁剪（v2.3 重点修复）** |
| **使用坐标轴标题 (`scales.x.title.text`)** | 标题文本极易导致垂直/水平溢出 |
| **饼图图例未限制宽度** | 多标签图例自动换行，撑高容器 |
| **在 `.chart-wrapper` 或其父容器上设置 `padding`** | **会导致 `onComplete` 静态化阶段尺寸计算错误，裁剪内容** |
| **将 `.chart-title` 放在 `.chart-wrapper` 内部** | **破坏了纯净的绘图容器，导致布局冲突** |
| **在 `onComplete` 中手动计算 `img` 的高度** | **v2.4 不再需要，直接使用 `height: auto`** |

---

## ✅ 输出示例结构（完整片段，v2.4）

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        html, body {
            width: 720pt;
            height: 405pt;
            overflow: hidden;
            margin: 0;
            padding: 0;
        }
        body {
            display: flex;
            justify-content: center;
            align-items: center;
        }
        .slide-container {
            width: 720pt;
            height: 405pt;
            padding: 30pt;
            overflow: hidden;
            font-family: var(--font-heading);
        }
        .header {
            margin-bottom: 15pt;
            border-bottom: 1.5pt solid var(--color-primary);
            width: fit-content;
        }
        .title {
            font-size: 24pt;
            font-weight: 600;
            color: var(--color-primary);
            margin-bottom: 5pt;
        }
        .content-container {
            display: flex;
            gap: 25pt;
            height: calc(100% - 60px);
        }
        /* v2.4: 新的图表区域结构 */
        .chart-section {
            width: 320pt;
            height: 280pt; /* 👈 固定高度在此处 */
            border-radius: 6pt;
            overflow: hidden; /* 👈 防溢出 */
            display: flex;
            flex-direction: column;
            /* 注意：这里没有 padding */
        }
        .chart-title {
            font-size: 14pt;
            font-weight: 600;
            text-align: center;
            margin: 15pt 15pt 10pt 15pt; /* 👈 用 margin 模拟 padding */
            color: #2d3748;
        }
        .chart-wrapper {
            flex: 1;
            overflow: hidden;
            padding: 0 15pt 15pt 15pt; /* 👈 padding 移到这里，不影响 canvas 高度计算 */
        }
        .chart-wrapper canvas {
            width: 100%;
            height: 100%;
            display: block;
        }
        .right-content {
            flex: 1;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }
    </style>
</head>
<body>
    <div class="slide-container">
        <div class="header">
            <h1 class="title">四大菜系分布</h1>
        </div>
        
        <div class="content-container">
            <div class="chart-section">
                <h3 class="chart-title">四大菜系地域分布占比</h3>
                <div class="chart-wrapper">
                    <!-- 请在此处实现一个图表，禁止使用占位符 -->
                    <canvas id="myChart"></canvas>
                </div>
            </div>
            
            <div class="right-content">
                
            </div>
        </div>
    </div>

    <script>
        document.addEventListener('DOMContentLoaded', () => {
            const ctx = document.getElementById('myChart').getContext('2d');
            const chart = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: ['川菜', '鲁菜', '粤菜', '苏菜'],
                    datasets: [{
                        data: [35, 28, 22, 15],
                        backgroundColor: ['#A67C52', '#D4B896', '#C4A484', '#8B4513'],
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: 'bottom',
                            maxWidth: 300,
                            labels: {
                                font: { family: 'Inter', size: 10 },
                                usePointStyle: true,
                                padding: 6
                            }
                        }
                    },
                    animation: {
                        duration: 1000,
                        onComplete: function() {
                            const canvas = document.getElementById('myChart');
                            if (!canvas) return;

                            const wrapper = canvas.parentElement;
                            const img = new Image();
                            img.src = canvas.toDataURL('image/png');
                            img.style.width = '100%';
                            img.style.height = 'auto'; // 👈 v2.4: 关键简化
                            img.style.display = 'block';

                            wrapper.innerHTML = '';
                            wrapper.appendChild(img);
                        }
                    }
                }
            });
        });
    </script>
</body>
</html>
```

> ✅ 符合此 **v2.4 规范** 的 HTML 通过**重构布局**和**简化静态化逻辑**，从根本上解决了图表内容（尤其是底部图例和X轴）被裁剪的问题，确保在任何环境下都能完美呈现。