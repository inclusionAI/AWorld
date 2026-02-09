---
name: ppt_renderer
description: 解析当前PPT页大纲中的布局预判 JSON 数据，严格按照 JSON 中的 config 字段，选择最匹配的HTML模版，并将 data 数组中的内容精准注入到对应的 DOM 槽位。
active: True
---

# 模版管理决策流程
根据大纲的路由预判配置的 config 字段（布局标识）进行路由，自动识别并加载对应的模版。
1.  **页面类型识别：** 首先，基于用户提供的大纲内容，识别当前大纲是“封面页”还是“内容页”。
2.  **模版识别与加载：** 根据页面类型，对照下面的json数据，路由到不同的模版。（templates/）。
   - 如果大纲是封面页，则直接选择 `outline` 模版；
   - 如果大纲是内容页，则根据大纲中`layout_prediction`字段中的 `mode` 参数，路由到对应的布局模版中（例如：`outline`、`full_tab`、`center_hero`、`time_step`、`split_cht_txt`、`split_icon_txt`、`trip_icon_txt`、`split_txt_txt`、`grid_icon_txt`等）；
    ```json
    {
        "封面页": "outline",
        "内容页": {
            "FULL_TABLE": "full_tab",
            "CENTER_HERO": "center_hero",
            "TIME_STEP": "time_step",
            "SPLIT_CHT_TXT": "split_cht_txt",
            "SPLIT_ICON_TXT": "split_icon_txt",
            "TRIP_ICON_TXT": "trip_icon_txt",
            "SPLIT_TXT_TXT": "split_txt_txt",
            "GRID_ICON_TXT": "grid_icon_txt"
        }
    }
    ```
3.  **模版资源加载(必须)：** **必须使用 `context` 工具** 动态加载选定模版文件（如 `templates/outline.md`）中的html代码。
4.  **内容生成(必须)：** 在 PPT 生成过程中，**必须严格遵守**模版中的代码框架，只需要向框架里填充内容。**禁止添加其他的元素。**