---
description: 灵光小程序 API：API_DATAFETCH。与 PRD「所需 API 技能」对齐后按需激活。数据获取
---

<data_fetch_api caption="数据获取 API 使用说明">
# lingguang.data.fetch API 使用说明

## 概述
`lingguang.data.fetch` 是统一聚合的数据获取接口，通过自然语言查询和 JSON Schema 定义返回结构来获取数据。

## 适用场景
适用场景包括但不限于以下场景
1. **时间序列**（中国GDP变化、上海房租趋势）
2. **产品对比**（车型对比、手机参数对比）
3. **实时资讯**（AI新闻、电动车购买咨询）
4. **实时知识**（旅游攻略、电影评分、演出信息）
5. **静态知识**（菜谱做法、历史事件）

## 函数签名

```javascript
async window.lingguang.data.fetch(query, schema)
```

## 参数说明

- `query` (`string`, 必填): 自然语言查询，例如："获取2025-12-01到2025-12-07的头条新闻列表"
  - 对于事实信息，query中必须添加当前日期
- `schema` (`object`, 必填): JSON Schema 对象，定义返回数据的结构和类型，必须遵守 JSON Schema 规范。**注意：schema 的最外层 type 必须是 "object"**，因为有些 LLM 结构化输出不支持顶层 array

## 返回值

- 成功：返回解析后的 JSON 对象（符合 schema 定义）
- 失败或数据为空：返回 null

## 使用示例

### 示例 1：获取近期新闻列表

```javascript
const query = "获取2025-12-01到2025-12-07的头条新闻列表";
const schema = {
    type: "object",
    properties: {
        news: {
            type: "array",
            items: {
                type: "object",
                properties: {
                    title: { type: "string" },
                    publishTime: { type: "string" },
                    source: { type: "string" },
                    summary: { type: "string" }
                },
                required: ["title", "publishTime"],
                additionalProperties: false
            }
        }
    },
    required: ["news"],
    additionalProperties: false
};

try {
    const result = await window.lingguang.data.fetch(query, schema);
    if (result && result.news) {
        console.log("获取到的新闻列表:", result.news);
    } else {
        console.log("未获取到数据");
    }
} catch (error) {
    console.error("请求失败:", error);
}
```

### 示例 2：手机参数对比

```javascript
const query = "对比iPhone 15 Pro和小米 15 Pro的参数";
const schema = {
    type: "object",
    properties: {
        phones: {
            type: "array",
            items: {
                type: "object",
                properties: {
                    name: { type: "string" },
                    brand: { type: "string" },
                    screen: { type: "string" },
                    processor: { type: "string" },
                    memory: { type: "string" },
                    storage: { type: "string" },
                    camera: { type: "string" },
                    battery: { type: "string" },
                    price: { type: "string" }
                },
                required: ["name", "brand", "screen", "processor"],
                additionalProperties: false
            }
        }
    },
    required: ["phones"],
    additionalProperties: false
};

try {
    const result = await window.lingguang.data.fetch(query, schema);
    if (result && result.phones) {
        console.log("手机参数对比:", result.phones);
        // 可以用于渲染对比表格或列表
    } else {
        console.log("未获取到数据");
    }
} catch (error) {
    console.error("请求失败:", error);
}
```

## 性能与数据量注意事项

- **数据量控制**：`lingguang.data.fetch` 的吞吐能力有限，请求大量数据会导致耗时过长，影响用户体验。请根据实际需求合理控制数据量：
  - **时间序列数据**：优先获取用户需要的时间范围，如用户要求"最近一周"就不要请求一个月的数据
  - **列表数据**：如果用户没有明确要求全部数据，建议限制返回条数（如新闻列表默认 5-10 条）
  - **只有用户明确要求"全部"或"所有"时**，才请求完整数据
  - **示例**：用户问"最近有什么AI新闻"，query 应该是"获取最近5条AI新闻"而不是"获取所有AI新闻"

- **耗时预期**：数据获取通常需要 2-5 秒，数据量越大耗时越长。请确保 UI 有合适的加载状态提示

## 交互提示

- **异步调用**：`lingguang.data.fetch` 是异步接口，返回 Promise，必须在 `async` 函数中使用 `await` 来等待结果，并使用 `try...catch` 来处理错误

{% if use_react_scaffold -%}
- **防抖处理（React 版本）**：当用户在输入框输入或频繁触发查询时，应该添加防抖机制（建议 500-1000ms），避免过于频繁的请求。使用 `useRef` 管理定时器：
  ```tsx
  import { useRef } from 'react';

  function SearchComponent() {
    const debounceTimerRef = useRef<number | null>(null);
    
    // 监听用户输入事件（如 input 事件）
    // 每次输入时清除之前的定时器，设置新的定时器
    const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      const query = e.target.value;
      
      // 清除之前的定时器
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
      
      // 设置新的定时器
      debounceTimerRef.current = setTimeout(async () => {
        if (query) {
          // 执行 fetch 调用
          const result = await window.lingguang.data.fetch(query, schema);
        }
      }, 800); // 800ms 防抖
    };

    return (
      <input onChange={handleInputChange} placeholder="输入查询内容" />
    );
  }
  ```

- **加载状态（React 版本）**：在 UI 层面必须设计加载状态，提升用户体验：
  ```tsx
  import { useState } from 'react';

  function DataFetchComponent() {
    const [loading, setLoading] = useState<boolean>(false);

    const fetchData = async (query: string, schema: object) => {
      // 显示加载状态：显示加载指示器，禁用提交按钮或输入框
      setLoading(true);
      
      try {
        const result = await window.lingguang.data.fetch(query, schema);
        if (result) {
          // 更新 UI 显示数据：将获取到的数据渲染到页面上
          // 例如：displayData(result);
        } else {
          // 显示提示信息：告知用户未获取到数据
          // 例如：showMessage('未获取到数据');
        }
      } catch (error: any) {
        console.error('请求失败:', error);
        // 显示错误提示：告知用户数据获取失败
        // 例如：showMessage('数据获取失败，请稍后重试');
      } finally {
        // 隐藏加载状态：隐藏加载指示器，恢复按钮或输入框可用状态
        setLoading(false);
      }
    };

    return (
      <button onClick={() => fetchData(query, schema)} disabled={loading}>
        {loading ? '加载中...' : '获取数据'}
      </button>
    );
  }
  ```
{% else -%}
- **防抖处理**：当用户在输入框输入或频繁触发查询时，应该添加防抖机制（建议 500-1000ms），避免过于频繁的请求。例如：
  ```javascript
  let debounceTimer = null;
  // 监听用户输入事件（如 input 事件）
  // 每次输入时清除之前的定时器，设置新的定时器
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(async () => {
    const query = /* 获取用户输入的查询内容 */;
    if (query) {
      // 执行 fetch 调用
      const result = await window.lingguang.data.fetch(query, schema);
    }
  }, 800); // 800ms 防抖
  ```

- **加载状态**：在 UI 层面必须设计加载状态，提升用户体验：
  - 发起请求时显示加载指示器（如 loading spinner、进度条或"加载中..."文字）
  - 请求完成后隐藏加载状态
  - 请求失败时显示错误提示
  - 建议禁用按钮或输入框，防止用户在请求进行中重复触发
  ```javascript
  async function fetchData(query, schema) {
    // 显示加载状态：显示加载指示器，禁用提交按钮或输入框
    
    try {
      const result = await window.lingguang.data.fetch(query, schema);
      if (result) {
        // 更新 UI 显示数据：将获取到的数据渲染到页面上
        // 例如：displayData(result);
      } else {
        // 显示提示信息：告知用户未获取到数据
        // 例如：showMessage('未获取到数据');
      }
    } catch (error) {
      console.error('请求失败:', error);
      // 显示错误提示：告知用户数据获取失败
      // 例如：showMessage('数据获取失败，请稍后重试');
    } finally {
      // 隐藏加载状态：隐藏加载指示器，恢复按钮或输入框可用状态
    }
  }
  ```
{% endif -%}


</data_fetch_api>
