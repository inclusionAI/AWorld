# 模版名称：split_txt_txt（左右双栏纯文字）

## 核心规则
* 对比逻辑：通常用于“现状 vs 目标”或“优势 vs 劣势”，两栏字数差异禁止超过 30%。
* 密度控制：每栏禁止超过 3 个列表项，严禁出现大段文字对垒。
  
## 代码框架
```html
<head>
  <style>
      .content-container {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 30pt;
      }
      .split-box {
        min-height: 280pt;
        padding-top: 8pt;
        padding-left: 10pt;
        position: relative;
      }
  </style>
</head>
<div class="content-container">
    <div class="split-box">
        
    </div>
</div>
```