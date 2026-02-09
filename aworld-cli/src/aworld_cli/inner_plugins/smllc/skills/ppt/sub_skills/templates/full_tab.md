# 模版名称：full_table（全宽数据表）

## 核心规则
* 行数限制：总行数（含表头）禁止超过 7 行。
* 列数限制：禁止超过 5 列。
* 排版约束：强制设置 table-layout: fixed。单元格内容禁止换行，超出部分必须使用 ellipsis 截断。
* 禁止事项：禁止在单元格内放入长句子，仅允许放置数值或短词。
  
## 代码框架
```html
<head>
  <style>
    .content-container {
        flex: 1;
        min-height: 0;
        display: flex;
        justify-content: center;
        align-items: center;
    }
    .table-wrapper {
        width: 100%;
        max-width: 660pt;
        overflow: hidden;
        background: var(--bg-card);
        border-radius: var(--border-radius);
    }
    .scholars-table {
        width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
        border-radius: 8pt;
        overflow: hidden;
        box-shadow: 0 2pt 8pt;
    }
    /* 高密度模式下的垂直压缩 */
    .scholars-table tr {
        height: auto;
    }
    .scholars-table td, .scholars-table th {
        /* 缩小上下内边距，释放垂直空间 */
        padding: 6pt 8pt; 
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap; /* 防止多行换行撑高行高 */
    }
    .scholars-table th {
        background: var(--color-primary);
        color: var(--color-secondary);
        font-size: 11pt;
        text-transform: uppercase;
    }
  </style>
</head>
<div class="content-container">
    <div class="table-wrapper">
        <table class="scholars-table">
        </table>
    </div>
</div>