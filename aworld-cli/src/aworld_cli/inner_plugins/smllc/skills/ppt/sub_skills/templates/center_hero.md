# 模版名称：center_hero（居中金句/视觉页）

## 核心规则
* 字数限制：主视觉文字（Hero Text）禁止超过 20 个汉字。
* 禁止事项：禁止添加任何列表点、图片或复杂的装饰物，保持极简视觉冲击力。
  
## 代码框架
```html
<head>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body { 
            overflow: hidden; 
            justify-content: center;  /* 水平居中 */
            align-items: center;      /* 垂直居中 */
            display: flex;
        }
        .slide-container {
            display: flex;
            flex-direction: column;
        }
        .content-container {
            flex: 1;            /* 占据 header 之外的所有剩余高度 */
            display: flex;      /* 开启内部 Flex */
            flex-direction: column;
            justify-content: center; /* 垂直居中核心内容 */
            align-items: center;     /* 水平居中核心内容 */
            text-align: center;
            padding: 0;         /* 移除固定 padding，让居中更精准 */
            max-width: 100%;    /* 适配容器宽度 */
            margin: 0 auto;
        }
    </style>
</head>
<div class="content-container">

</div>
```