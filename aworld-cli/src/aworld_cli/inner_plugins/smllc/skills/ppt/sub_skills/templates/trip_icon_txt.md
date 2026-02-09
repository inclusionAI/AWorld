# 模版名称：trip_icon_txt（时间轴/步骤页）

## 核心规则
* 强制分栏：必须严格遵循 grid-cols-3 布局。
* 等高处理：三个卡片容器必须高度对齐。
* 文本限制：每栏下方的描述文字严禁超过 2 行。

## 代码框架
```html
<head>
  <style>
      .content-container {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 20pt;
        margin-top: 20pt;
      }
      .exchange-box {
        min-height: 280pt;
        padding-top: 8pt;
        padding-left: 15pt;
        padding-right: 8pt;
        position: relative;
        text-align: center;
        border-radius: 8pt;
        background-color: var(--color-card-bg);
        border-top: var(--color-icon);
      }
  </style>
</head>
<div class="content-container">
    <div class="exchange-box">
    
    </div>
</div>
```