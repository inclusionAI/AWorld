# 模版名称：grid_icon_txt（四宫格/六宫格/矩阵）

## 核心规则
* 矩阵限制：固定为 2x2 布局 或 2x3 布局，禁止动态增加行列。
* 视觉重心：每个格子必须包含：一个图标 + 一个短标题 + 一句极简描述。
* 空间防溢：单个格子内的垂直高度总和禁止超过 120pt。

## 代码框架
```html
<head>
<style>
  .content-container {
    display: grid;
    grid-template-columns: 1fr 1fr;
    grid-template-rows: 1fr 1fr;
    gap: 16.875pt;
    padding-left: 45pt;
    padding-right: 45pt;
    /* 不要添加height相关属性 */
  }
  .exchange-box {
    min-height: 123.75pt;
    background-color: var(--color-card-bg);
    border-radius: 8pt;
    padding-top: 16.875pt;
    padding-left: 22.5pt;
    padding-right: 22.5pt;
    display: flex;
    flex-direction: column;
    transition: all 0.3s ease; /* 增加平滑感 */
  }
</style>
</head>
<div class="content-container">
    <!-- **严格遵守**四宫格对应4个exchange-box，六宫格对应6个exchange-box -->
    <div class="exchange-box">

    </div>
</div>
```