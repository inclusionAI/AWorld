# 模版名称：time_step（时间轴/步骤页）

## 核心规则
* 节点限制：水平时间轴节点禁止超过 7 个；
* 文本量：每个节点下的描述文字禁止超过 20 个汉字。
* 间距策略：节点间距必须固定，防止在节点过少时出现大面积留白。
  
## 代码框架
```html
<head>
  <style>
    .slide-container {
        display: flex;
        flex-direction: column;
    }
    .content-container {
        flex: 1;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        min-height: 0;
    }
    .timeline-line {
        position: relative;
        margin: 0 auto;
        width: 600pt;
    }
   </style>
</head>
<div class="content-container">
    <div class="timeline-line">

    </div>
</div>
```