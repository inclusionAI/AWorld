
import os

from aworld.logs.util import logger

GAIA_SYSTEM_PROMPT = os.getenv("GAIA_SYSTEM_PROMPT")
logger.info("GAIA_SYSTEM_PROMPT", GAIA_SYSTEM_PROMPT)

SUMMARY_TEMPLATE = """
You are presented with a user task, a conversion that may contain the answer, and a previous conversation summary. 
Please read the conversation carefully and extract new information from the conversation that helps to solve user task

<guide>
{summary_rule}
</guide>

<output_schema>
{summary_schema}
</output_schema>

<user_task> {user_task} </user_task>
<existed_summary> {existed_summary} </existed_summary>
<conversation> {to_be_summary} </conversation>

## output new summary: 
"""

episode_memory_summary_rule="""
1. Identify major milestones, subgoal completions, and strategic decisions
2. Extract only the most critical events that provide experience for long-term goals
"""

episode_memory_summary_schema="""
```json
{{
  "task_description": "A general summary of what the reasoning history has been doing and the overall goals it has been striving for.",
  "key_events": [
    {{
      "step": "step number",
      "description": "A detailed description of the specific action taken, decision made, or milestone achieved at this step, including relevant context and reasoning behind the choice.",
      "outcome": "A detailed account of the direct result, observation, or feedback received from this action or decision, including any new information gained or changes in the task state."
    }},
    ...
  ],
  "current_progress": "A general summary of the current progress of the task, including what has been completed and what is left to be done."
}}
```
"""

working_memory_summary_rule="""
1. Extract ONLY immediate goals, current challenges, and next steps
2. Ignore completed/historical information
"""

working_memory_summary_schema="""
```json
{{
  "immediate_goal": "A clear summary of the current subgoal—what you are actively working toward at this moment.",
  "current_challenges": "A concise summary of the main obstacles or difficulties you are presently encountering.",
  "next_actions": [
    {{
      "type": "tool_call/planning/decision",
      "description": "Anticipate and describe the next concrete action you intend to take to advance the task."
    }},
    ...
  ]
}}
```
"""

tool_memory_summary_rule = """
# Playwright Element Extraction

## Task
Extract all page elements from Playwright YAML snapshot to JSON array. **Preserve ALL refs, including [unchanged] markers.**

## Rules
1. Extract every element with `[ref=xxx]` or `ref=xxx`
2. Maintain hierarchy: record `children_refs` and `parent_ref`
3. Extract direct text to `text` field (null if no direct text)
4. Parse attributes: `[cursor=pointer]` → `attributes.cursor: "pointer"`, `[active]` → `attributes.active: true`, `[unchanged]` → `attributes.unchanged: true`, `<changed>` → `attributes.changed: true`
5. Handle `/url: https://...` → `attributes.url: "https://..."`
6. Element types: `button`, `link`, `textbox`, `paragraph`, `generic`, `combobox`, etc. For `ref=xxx [unchanged]` without type, use `type: "generic"`
7. Keep ref uniqueness and parent-child relationships

## Example Input
```yaml
- generic [active] [ref=e1]:
  - generic [ref=e4] [cursor=pointer]:
    - generic [ref=e6]:
      - textbox "目的地/酒店/景点/签证等" [ref=e8]
      - button "搜索" [ref=e9]
  - list [ref=e10]:
    - listitem [ref=e11]:
      - link [ref=e12]:
        - /url: //cart.taobao.com/cart.htm
    - listitem [ref=e16]:
      - link [ref=e17]:
        - /url: https://web.m.taobao.com/app/mtb/pc-itaotool/collect
  - combobox [ref=e100]: 北京
  - ref=e4 [unchanged]
```
"""

tool_memory_summary_schema = """
## Output Format
JSON array with fields: `ref`, `type`, `text`, `attributes`, `children_refs`, `parent_ref`, `role`, `properties`

```json
[
  {
    "ref": "e1",
    "type": "generic",
    "text": null,
    "attributes": {"active": true},
    "children_refs": ["e4", "e10"],
    "parent_ref": null,
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e8",
    "type": "textbox",
    "text": "目的地/酒店/景点/签证等",
    "attributes": {},
    "children_refs": [],
    "parent_ref": "e6",
    "role": "textbox",
    "properties": {}
  },
  {
    "ref": "e12",
    "type": "link",
    "text": null,
    "attributes": {"url": "//cart.taobao.com/cart.htm"},
    "children_refs": [],
    "parent_ref": "e11",
    "role": "link",
    "properties": {}
  }
]
```

## Few-Shot Example

### Input
```yaml
- generic [active] [ref=e1]:
  - generic [ref=e4] [cursor=pointer]:
    - generic [ref=e6]:
      - textbox "目的地/酒店/景点/签证等" [ref=e8]
      - button "搜索" [ref=e9]
    - list [ref=e10]:
      - listitem [ref=e11]:
        - link [ref=e12]:
          - /url: //cart.taobao.com/cart.htm?from=mini&ad_id=&am_id=&cm_id=&pm_id=1501036000a02c5c3739
      - listitem [ref=e16]:
        - link [ref=e17]:
          - /url: https://web.m.taobao.com/app/mtb/pc-itaotool/collect
      - listitem [ref=e21]:
        - link "我的订单" [ref=e22]:
          - /url: https://buyertrade.taobao.com/trade/itemlist/list_bought_items.htm
  - generic [ref=e68]:
    - generic [ref=e69]:
      - generic [ref=e73]:
        - generic [ref=e74]:
          - generic [ref=e75] [cursor=pointer]: 国内
          - generic [ref=e77] [cursor=pointer]: 国际/中国港澳台
        - generic [ref=e78]:
          - generic [ref=e79]:
            - generic [ref=e80] [cursor=pointer]:
              - img [ref=e82]
              - generic [ref=e83]: 单程
            - generic [ref=e86] [cursor=pointer]: 往返
          - generic [ref=e87]:
            - generic [ref=e94]:
              - generic [ref=e95]: 出发城市
              - combobox [ref=e100]: 北京
            - img [ref=e101]
            - generic [ref=e108]:
              - generic [ref=e109]: 到达城市
              - combobox [ref=e114]: 杭州
          - generic [ref=e117]:
            - generic [ref=e118]:
              - generic [ref=e119]: 出发日期
              - textbox "请选择" [ref=e127]: 2025年11月25日
            - textbox "添加返程" [ref=e132]
        - button "搜索机票" [ref=e134] [cursor=pointer]
```

### Output
```json
[
  {
    "ref": "e1",
    "type": "generic",
    "text": null,
    "attributes": {"active": true},
    "children_refs": ["e4", "e68"],
    "parent_ref": null,
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e4",
    "type": "generic",
    "text": null,
    "attributes": {"cursor": "pointer"},
    "children_refs": ["e6", "e10"],
    "parent_ref": "e1",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e6",
    "type": "generic",
    "text": null,
    "attributes": {},
    "children_refs": ["e8", "e9"],
    "parent_ref": "e4",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e8",
    "type": "textbox",
    "text": "目的地/酒店/景点/签证等",
    "attributes": {},
    "children_refs": [],
    "parent_ref": "e6",
    "role": "textbox",
    "properties": {}
  },
  {
    "ref": "e9",
    "type": "button",
    "text": "搜索",
    "attributes": {},
    "children_refs": [],
    "parent_ref": "e6",
    "role": "button",
    "properties": {}
  },
  {
    "ref": "e10",
    "type": "list",
    "text": null,
    "attributes": {},
    "children_refs": ["e11", "e16", "e21"],
    "parent_ref": "e4",
    "role": "list",
    "properties": {}
  },
  {
    "ref": "e11",
    "type": "listitem",
    "text": null,
    "attributes": {},
    "children_refs": ["e12"],
    "parent_ref": "e10",
    "role": "listitem",
    "properties": {}
  },
  {
    "ref": "e12",
    "type": "link",
    "text": null,
    "attributes": {"url": "//cart.taobao.com/cart.htm?from=mini&ad_id=&am_id=&cm_id=&pm_id=1501036000a02c5c3739"},
    "children_refs": [],
    "parent_ref": "e11",
    "role": "link",
    "properties": {}
  },
  {
    "ref": "e16",
    "type": "listitem",
    "text": null,
    "attributes": {},
    "children_refs": ["e17"],
    "parent_ref": "e10",
    "role": "listitem",
    "properties": {}
  },
  {
    "ref": "e17",
    "type": "link",
    "text": null,
    "attributes": {"url": "https://web.m.taobao.com/app/mtb/pc-itaotool/collect"},
    "children_refs": [],
    "parent_ref": "e16",
    "role": "link",
    "properties": {}
  },
  {
    "ref": "e21",
    "type": "listitem",
    "text": null,
    "attributes": {},
    "children_refs": ["e22"],
    "parent_ref": "e10",
    "role": "listitem",
    "properties": {}
  },
  {
    "ref": "e22",
    "type": "link",
    "text": "我的订单",
    "attributes": {"url": "https://buyertrade.taobao.com/trade/itemlist/list_bought_items.htm"},
    "children_refs": [],
    "parent_ref": "e21",
    "role": "link",
    "properties": {}
  },
  {
    "ref": "e68",
    "type": "generic",
    "text": null,
    "attributes": {},
    "children_refs": ["e69"],
    "parent_ref": "e1",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e69",
    "type": "generic",
    "text": null,
    "attributes": {},
    "children_refs": ["e73"],
    "parent_ref": "e68",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e73",
    "type": "generic",
    "text": null,
    "attributes": {},
    "children_refs": ["e74", "e78"],
    "parent_ref": "e69",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e74",
    "type": "generic",
    "text": null,
    "attributes": {},
    "children_refs": ["e75", "e77"],
    "parent_ref": "e73",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e75",
    "type": "generic",
    "text": "国内",
    "attributes": {"cursor": "pointer"},
    "children_refs": [],
    "parent_ref": "e74",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e77",
    "type": "generic",
    "text": "国际/中国港澳台",
    "attributes": {"cursor": "pointer"},
    "children_refs": [],
    "parent_ref": "e74",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e78",
    "type": "generic",
    "text": null,
    "attributes": {},
    "children_refs": ["e79", "e87", "e117"],
    "parent_ref": "e73",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e79",
    "type": "generic",
    "text": null,
    "attributes": {},
    "children_refs": ["e80", "e86"],
    "parent_ref": "e78",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e80",
    "type": "generic",
    "text": null,
    "attributes": {"cursor": "pointer"},
    "children_refs": ["e82", "e83"],
    "parent_ref": "e79",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e82",
    "type": "img",
    "text": null,
    "attributes": {},
    "children_refs": [],
    "parent_ref": "e80",
    "role": "img",
    "properties": {}
  },
  {
    "ref": "e83",
    "type": "generic",
    "text": "单程",
    "attributes": {},
    "children_refs": [],
    "parent_ref": "e80",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e86",
    "type": "generic",
    "text": "往返",
    "attributes": {"cursor": "pointer"},
    "children_refs": [],
    "parent_ref": "e79",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e87",
    "type": "generic",
    "text": null,
    "attributes": {},
    "children_refs": ["e94", "e101", "e108"],
    "parent_ref": "e78",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e94",
    "type": "generic",
    "text": null,
    "attributes": {},
    "children_refs": ["e95", "e100"],
    "parent_ref": "e87",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e95",
    "type": "generic",
    "text": "出发城市",
    "attributes": {},
    "children_refs": [],
    "parent_ref": "e94",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e100",
    "type": "combobox",
    "text": "北京",
    "attributes": {},
    "children_refs": [],
    "parent_ref": "e94",
    "role": "combobox",
    "properties": {}
  },
  {
    "ref": "e101",
    "type": "img",
    "text": null,
    "attributes": {},
    "children_refs": [],
    "parent_ref": "e87",
    "role": "img",
    "properties": {}
  },
  {
    "ref": "e108",
    "type": "generic",
    "text": null,
    "attributes": {},
    "children_refs": ["e109", "e114"],
    "parent_ref": "e87",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e109",
    "type": "generic",
    "text": "到达城市",
    "attributes": {},
    "children_refs": [],
    "parent_ref": "e108",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e114",
    "type": "combobox",
    "text": "杭州",
    "attributes": {},
    "children_refs": [],
    "parent_ref": "e108",
    "role": "combobox",
    "properties": {}
  },
  {
    "ref": "e117",
    "type": "generic",
    "text": null,
    "attributes": {},
    "children_refs": ["e118"],
    "parent_ref": "e78",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e118",
    "type": "generic",
    "text": null,
    "attributes": {},
    "children_refs": ["e119", "e127"],
    "parent_ref": "e117",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e119",
    "type": "generic",
    "text": "出发日期",
    "attributes": {},
    "children_refs": [],
    "parent_ref": "e118",
    "role": "generic",
    "properties": {}
  },
  {
    "ref": "e127",
    "type": "textbox",
    "text": "2025年11月25日",
    "attributes": {"placeholder": "请选择"},
    "children_refs": [],
    "parent_ref": "e118",
    "role": "textbox",
    "properties": {}
  },
  {
    "ref": "e132",
    "type": "textbox",
    "text": null,
    "attributes": {"placeholder": "添加返程"},
    "children_refs": [],
    "parent_ref": "e78",
    "role": "textbox",
    "properties": {}
  },
  {
    "ref": "e134",
    "type": "button",
    "text": "搜索机票",
    "attributes": {"cursor": "pointer"},
    "children_refs": [],
    "parent_ref": "e73",
    "role": "button",
    "properties": {}
  }
]
```
"""