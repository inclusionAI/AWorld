---
name: html2pptx
description: 从 HTML 生成 PPTX。HTML 必须符合特定的格式要求，包括正确的主体尺寸、支持的元素、文本规则、样式指南和布局规范，详情如下所述。
active: True
---

# HTML 生成指南

## 📐 规则 1: HTML Body 尺寸要求（强制）

### 要求
HTML `<body>` 元素的尺寸必须与演示文稿布局完全匹配，容差为 ±0.1 英寸。

### 标准尺寸

```html
<style>
body {
  width: 720pt;   /* 必须使用 pt 单位 */
  height: 405pt;  /* 16:9 布局 */
  margin: 0;
  padding: 0;
  display: flex;  /* 必须设置，防止 margin collapse */
}
</style>
```

**支持的布局尺寸：**
- **16:9** (默认): `width: 720pt; height: 405pt;`
- **4:3**: `width: 720pt; height: 540pt;`
- **16:10**: `width: 720pt; height: 450pt;`

### ✅ 正确示例
```html
<body style="width: 720pt; height: 405pt; margin: 0; padding: 0; display: flex;">
  <!-- 内容 -->
</body>
```

### ❌ 错误示例
```html
<!-- 错误 1: 尺寸不匹配 -->
<body style="width: 800px; height: 600px;">  <!-- 使用 px 或尺寸错误 -->

<!-- 错误 2: 缺少 display: flex -->
<body style="width: 720pt; height: 405pt;">  <!-- 缺少 display: flex -->

<!-- 错误 3: 尺寸与布局不匹配 -->
<body style="width: 720pt; height: 500pt;">  <!-- 16:9 应该是 405pt -->
```

---

## 📝 规则 2: 文本标签要求（强制）

### 要求
**所有文本内容必须包装在以下标签之一：**
- `<p>` - 段落
- `<h1>` 到 `<h6>` - 标题
- `<ul>`, `<ol>`, `<li>` - 列表

**不在这些标签中的文本将被忽略，不会出现在 PowerPoint 中！**

### ✅ 正确示例
```html
<!-- 正确 1: 段落文本 -->
<p>这是段落文本</p>

<!-- 正确 2: 标题 -->
<h1>主标题</h1>
<h2>副标题</h2>

<!-- 正确 3: DIV 中的文本必须包装 -->
<div style="background: #f0f0f0;">
  <p>文本必须包装在 p 标签中</p>
</div>

<!-- 正确 4: 列表 -->
<ul>
  <li>第一项</li>
  <li>第二项</li>
</ul>
```

### ❌ 错误示例
```html
<!-- 错误 1: DIV 中直接放文本 -->
<div>这段文本不会出现在 PowerPoint 中</div>

<!-- 错误 2: SPAN 单独使用 -->
<span>这段文本也不会出现</span>

<!-- 错误 3: DIV 中有未包装的文本节点 -->
<div style="background: #f0f0f0;">
  直接文本内容  <!-- 会被检测并报错！ -->
</div>
```

---

## 🔤 规则 3: 字体限制（强制）

### 要求
**只能使用 Web 安全字体**，其他字体会导致渲染问题。

### 允许的字体列表
```css
font-family: Arial, Helvetica, Times New Roman, Georgia, 
             Courier New, Verdana, Tahoma, Trebuchet MS, 
             Impact, Comic Sans MS;
```

### ✅ 正确示例
```html
<p style="font-family: Arial, sans-serif;">文本</p>
<h1 style="font-family: 'Times New Roman', serif;">标题</h1>
```

### ❌ 错误示例
```html
<!-- 错误: 使用非 Web 安全字体 -->
<p style="font-family: 'Segoe UI';">文本</p>
<p style="font-family: 'SF Pro';">文本</p>
<p style="font-family: 'Roboto';">文本</p>
<p style="font-family: 'Microsoft YaHei';">文本</p>
```

---

## 🎨 规则 4: 样式限制（强制）

### 4.1 文本元素的样式限制

**文本元素（`<p>`, `<h1>`-`<h6>`, `<ul>`, `<ol>`）不能有以下样式：**
- ❌ `background` 或 `background-color`
- ❌ `border` 或任何边框样式
- ❌ `box-shadow`

### ✅ 正确示例
```html
<!-- 文本元素只能有文字样式 -->
<p style="color: #000000; font-size: 14pt;">文本</p>
<h1 style="font-weight: bold; text-align: center;">标题</h1>
```

### ❌ 错误示例
```html
<!-- 错误: 文本元素不能有背景 -->
<p style="background: #f0f0f0;">文本</p>

<!-- 错误: 文本元素不能有边框 -->
<p style="border: 1px solid #000;">文本</p>

<!-- 错误: 文本元素不能有阴影 -->
<p style="box-shadow: 2px 2px 4px #000;">文本</p>
```

### 4.2 形状元素（DIV）的样式支持

**只有 `<div>` 元素可以有以下样式：**
- ✅ `background` 或 `background-color`
- ✅ `border`（统一或部分边框）
- ✅ `border-radius`
- ✅ `box-shadow`（仅外部阴影）

### ✅ 正确示例
```html
<!-- 正确: DIV 可以有背景、边框、阴影 -->
<div style="background: #f0f0f0; border: 2px solid #000; border-radius: 8pt; box-shadow: 2px 2px 4px rgba(0,0,0,0.3);">
  <p>文本必须包装在 p 标签中</p>
</div>
```

### ❌ 错误示例
```html
<!-- 错误: DIV 不能有背景图片 -->
<div style="background-image: url('bg.png');">
  <p>文本</p>
</div>
```

### 4.3 内联元素的样式限制

**内联元素（`<span>`, `<b>`, `<i>`, `<u>`）支持：**
- ✅ `font-weight: bold`
- ✅ `font-style: italic`
- ✅ `text-decoration: underline`
- ✅ `color: #rrggbb`
- ✅ `font-size`

**不支持：**
- ❌ `margin`（任何方向）
- ❌ `padding`（任何方向）

### ✅ 正确示例
```html
<p>这是<span style="font-weight: bold; color: #ff0000;">粗体红色</span>文本</p>
```

### ❌ 错误示例
```html
<!-- 错误: 内联元素不能有 margin -->
<p>这是<span style="margin: 10px;">文本</span></p>
```

---

## 📋 规则 5: 项目符号要求（强制）

### 要求
**禁止手动添加项目符号**，必须使用 `<ul>` 或 `<ol>` 列表标签。

### ✅ 正确示例
```html
<ul>
  <li>第一项</li>
  <li>第二项</li>
  <li>第三项</li>
</ul>

<ol>
  <li>第一项</li>
  <li>第二项</li>
</ol>
```

### ❌ 错误示例
```html
<!-- 错误: 手动添加项目符号 -->
<p>• 第一项</p>
<p>- 第二项</p>
<p>* 第三项</p>
<p>▪ 第四项</p>
<p>▸ 第五项</p>
```

---

## 🎨 规则 6: CSS 渐变限制（强制）

### 要求
**禁止使用 CSS 渐变**（`linear-gradient`, `radial-gradient`）。

### 解决方案
如果需要渐变效果，必须：
1. 使用 Sharp 将渐变转换为 PNG 图片
2. 使用 `background-image: url('gradient.png')` 引用

### ❌ 错误示例
```html
<!-- 错误: 不能使用 CSS 渐变 -->
<body style="background: linear-gradient(to right, #ff0000, #0000ff);">
<div style="background: radial-gradient(circle, #ff0000, #0000ff);">
```

### ✅ 正确示例
```html
<!-- 正确: 使用 PNG 图片 -->
<body style="background-image: url('gradient-bg.png'); background-size: cover;">
```

---

## 📏 规则 7: 内容溢出检查（强制）

### 要求
**所有内容必须在 body 边界内**，不能溢出。

### 验证规则
- 水平方向：`scrollWidth` 不能超过 `width`
- 垂直方向：`scrollHeight` 不能超过 `height`
- 大字体文本（>12pt）距离底部至少 0.5 英寸

### ✅ 正确示例
```html
<body style="width: 720pt; height: 405pt; display: flex;">
  <div style="margin: 20pt; padding: 20pt;">
    <h1>标题</h1>
    <p>内容在边界内</p>
  </div>
</body>
```

### ❌ 错误示例
```html
<!-- 错误: 内容超出边界 -->
<body style="width: 720pt; height: 405pt;">
  <div style="width: 800pt; height: 500pt;">
    <!-- 内容超出 -->
  </div>
</body>

<!-- 错误: 大字体文本太靠近底部 -->
<body style="width: 720pt; height: 405pt;">
  <h1 style="font-size: 48pt; position: absolute; top: 380pt;">
    太靠近底部了
  </h1>
</body>
```

---

## 🎯 规则 8: 占位符要求（可选）

### 要求
如果使用占位符（用于后续添加图表），必须：
- 使用 `class="placeholder"`
- 必须有有效的 `id` 属性
- 宽度和高度不能为 0

### ✅ 正确示例
```html
<div id="chart1" class="placeholder" style="width: 350pt; height: 200pt; background: #f0f0f0;"></div>
```

### ❌ 错误示例
```html
<!-- 错误: 尺寸为 0 -->
<div class="placeholder" style="width: 0; height: 0;"></div>

<!-- 错误: 缺少 id -->
<div class="placeholder" style="width: 350pt; height: 200pt;"></div>
```

---

## 🌑 规则 9: 阴影限制

### 要求
**只支持外部阴影**，内部阴影（`inset`）会被忽略。

### ✅ 正确示例
```html
<div style="box-shadow: 2px 2px 8px rgba(0, 0, 0, 0.3);">
  <p>内容</p>
</div>
```

### ❌ 错误示例
```html
<!-- 错误: 内部阴影会被忽略 -->
<div style="box-shadow: inset 2px 2px 8px rgba(0, 0, 0, 0.3);">
  <p>内容</p>
</div>
```

---

## 🖼️ 规则 10: 图片要求

### 要求
- 使用 `<img>` 标签
- 必须有有效的 `src` 属性
- 宽度和高度不能为 0

### ✅ 正确示例
```html
<img src="image.png" style="width: 300pt; height: 200pt;" alt="描述">
```

---

## 📐 规则 11: 边框处理

### 统一边框
如果所有边的宽度相同，会转换为 PowerPoint 形状边框：
```html
<div style="border: 2px solid #000000;">
  <p>内容</p>
</div>
```

### 部分边框
如果边的宽度不同，会转换为线条对象：
```html
<div style="border-left: 8pt solid #E76F51;">
  <p>内容</p>
</div>
```

---

## ✅ 完整示例模板

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
html { background: #ffffff; }
body {
  width: 720pt; 
  height: 405pt; 
  margin: 0; 
  padding: 0;
  background: #f5f5f5; 
  font-family: Arial, sans-serif;
  display: flex;  /* 必须 */
}
.content { 
  margin: 30pt; 
  padding: 40pt; 
  background: #ffffff; 
  border-radius: 8pt; 
}
h1 { 
  color: #2d3748; 
  font-size: 32pt; 
}
.box {
  background: #70ad47; 
  padding: 20pt; 
  border: 3px solid #5a8f37;
  border-radius: 12pt; 
  box-shadow: 3px 3px 10px rgba(0, 0, 0, 0.25);
}
</style>
</head>
<body>
<div class="content">
  <h1>标题</h1>
  <ul>
    <li><b>第一项:</b> 描述</li>
    <li><b>第二项:</b> 描述</li>
  </ul>
  <p>文本内容，支持 <b>粗体</b>、<i>斜体</i>、<u>下划线</u>。</p>
  
  <!-- 占位符用于图表 -->
  <div id="chart1" class="placeholder" style="width: 350pt; height: 200pt; background: #f0f0f0;"></div>
  
  <!-- 形状中的文本必须包装 -->
  <div class="box">
    <p>形状中的文本</p>
  </div>
</div>
</body>
</html>
```

---

## 🚨 验证错误说明

如果违反规则，转换时会收到以下类型的错误：

1. **尺寸不匹配错误**
   ```
   HTML dimensions (10.0" × 5.6") don't match presentation layout (10.0" × 5.6")
   ```

2. **内容溢出错误**
   ```
   HTML content overflows body by 15.2pt horizontally and 8.5pt vertically
   ```

3. **文本位置错误**
   ```
   Text box "标题文本..." ends too close to bottom edge (0.3" from bottom, minimum 0.5" required)
   ```

4. **样式错误**
   ```
   Text element <p> has background. Backgrounds, borders, and shadows are only supported on <div> elements, not text elements.
   ```

5. **CSS 渐变错误**
   ```
   CSS gradients are not supported. Use Sharp to rasterize gradients as PNG images first
   ```

---

## 📋 快速检查清单

在生成 HTML 之前，请检查：

- [ ] Body 尺寸是否正确（720pt × 405pt for 16:9）
- [ ] Body 是否设置了 `display: flex`
- [ ] 所有文本是否包装在 `<p>`, `<h1>`-`<h6>`, `<ul>`, `<ol>` 中
- [ ] 是否只使用了 Web 安全字体
- [ ] 文本元素是否没有背景/边框/阴影
- [ ] 是否没有手动添加项目符号
- [ ] 是否没有使用 CSS 渐变
- [ ] 内容是否在边界内
- [ ] DIV 中的文本是否包装在 `<p>` 标签中
- [ ] 内联元素是否没有 margin/padding

---

## 💡 最佳实践

1. **使用模板**: 从上面的完整示例模板开始
2. **测试尺寸**: 确保所有元素都在 body 边界内
3. **使用 Flexbox**: 利用 `display: flex` 进行布局
4. **预渲染效果**: 渐变和图标先转换为 PNG
5. **验证字体**: 只使用 Web 安全字体列表中的字体

---

**重要提示**: 违反任何规则都会导致转换失败。请严格按照以上规则生成 HTML 代码。