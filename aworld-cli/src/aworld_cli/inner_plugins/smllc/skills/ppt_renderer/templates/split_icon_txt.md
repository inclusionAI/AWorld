# 模版名称：split_icon_txt（左右图标文字）

## 核心规则
* 比例锁定：左右图标文字容器宽度比例必须为 1:1。
* 对齐方式：左右容器必须在垂直方向上居中对齐。

## 代码框架
```html
<head>
  <style>
    .content-container {
        display: flex;
        gap: 20pt;
      }
    .split-box {
      flex: 1;
      min-height: 280pt;
      padding: 10pt;
      background-color: var(--color-card-bg);
      border: var(--card-border);
      border-top: var(--color-icon);
      border-radius: 8pt;
      text-align: center;
    }
  </style>
</head>
<div class="content-container">
    <div class="split-box">

    </div>
</div>
```