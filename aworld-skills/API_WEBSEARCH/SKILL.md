---
description: 灵光小程序 API：API_WEBSEARCH。与 PRD「所需 API 技能」对齐后按需激活。全网检索
---

## 强制使用约束（必须遵守）

- **严厉警告（WebSearch）**：一旦使用了 WEB_SEARCH，展示层必须以 WEB_SEARCH 返回结果作为唯一数据源，禁止继续渲染本地 mock/假数据。
- 建议采用单一数据流：`请求 -> 解析 -> 状态 -> 渲染`，避免本地重复缓存或二次派生状态导致展示与真实结果不一致。

<skill_overview caption="技能概述与边界">

**技术本质**

WEB_SEARCH = 全网搜索 + LLM 总结。

## 适用场景
适用场景包括但不限于以下场景
1. **时间序列**（中国GDP变化、上海房租趋势）
2. **产品对比**（车型对比、手机参数对比）
3. **实时资讯**（AI新闻、电动车购买咨询）
4. **实时知识**（旅游攻略、电影评分、演出信息）
5. **静态知识**（菜谱做法、历史事件）
6. **基于位置的数据**（附近餐厅、本地天气、周边景点）—— ⚠️ **必须传 location 参数**


</skill_overview>

<api_reference caption="接口说明">

**函数签名**
```javascript
async window.lingguang.data.fetch(query, schema, location?)
```

**参数**

- `query` (string, 必填): 自然语言查询。对于时效性信息，query 中应包含具体日期
- `schema` (object, 必填): JSON Schema，定义返回数据结构。最外层 type 必须是 "object"
- `location` (object, 位置相关查询必传): 位置信息，用于"附近/周边"类查询

**schema 参数规范**（严格遵守）
- 最外层 type 必须是 `"object"`，不能是 `"array"`（部分 LLM 结构化输出不支持顶层数组）
- 使用 `required` 指定必需字段
- 使用 `additionalProperties: false` 限制额外字段
- 数值字段务必在 `description` 中标明单位和格式，后端会据此过滤不匹配的搜索结果
- 示例结构：
```javascript
{
    type: "object",
    properties: {
        items: {
            type: "array",
            items: {
                type: "object",
                properties: {
                    name: { type: "string" },
                    price: { type: "number", description: "价格（元/克）" },
                    time: { type: "string", description: "时间点（格式：HH:mm）" }
                },
                required: ["name", "price", "time"],
                additionalProperties: false
            }
        }
    },
    required: ["items"],
    additionalProperties: false
}
```

**location 参数结构**（位置相关查询必传）
```javascript
{
    longitude: number,      // 经度
    latitude: number,       // 纬度
    province: string,       // 省份
    city: string,           // 城市
    district: string,       // 区县
    pois: Array<{name, address}>  // ⚠️ 必传！附近地标列表
}
```

**返回值**
- 成功：符合 schema 定义的 JSON 对象
- 失败或无数据：`null`

</api_reference>

<usage_guidelines caption="使用限制与技巧">

**禁止用于以下场景**

- 获取当前时间/时区：网络延迟导致不准确，应使用 `new Date()` 或 `toLocaleString()`
- 简单计算/单位换算：无需联网，应使用 JavaScript 本地计算
- AI 生成/创作内容：这是 CALLLLM 的职责，应使用 `window.callLLM()`

**调用频率控制**

- 定时刷新间隔 ≥ 3 秒，禁止每秒调用
- 避免将 data.fetch 放入高频变化的 useEffect 依赖中
- 避免同时发起多个并发请求（容易触发限流或部分失败）
- ❌ 错误：时钟每秒更新触发 data.fetch
- ❌ 错误：`Promise.all([data.fetch(q1), data.fetch(q2), data.fetch(q3)])`
- ✅ 正确：仅在日期变化或用户主动操作时调用
- ✅ 正确：合并为一次查询，或串行调用多个请求

**⚠️ React useCallback/useEffect 依赖陷阱**

这是最常见的错误：将会频繁变化的状态放入 useCallback 依赖，导致每次状态更新都触发 data.fetch。

```tsx
// ❌ 错误示例：useCallback 依赖了 learnedWords，形成死循环
function App() {
  const [learnedWords, setLearnedWords] = useState<string[]>([]);

  const fetchWords = useCallback(async () => {
    const excludeList = learnedWords.join('、');
    const result = await window.lingguang.data.fetch(query, schema);
    // ...
  }, [learnedWords]); // ❌ 依赖 learnedWords

  useEffect(() => {
    fetchWords();
  }, [fetchWords]); // ❌ fetchWords 每次 learnedWords 变化都会重建，触发 useEffect

  const handleLearn = (word: string) => {
    setLearnedWords(prev => [...prev, word]); // 这会触发 fetchWords 重建 → useEffect 执行 → data.fetch 调用
  };
}

// ✅ 正确示例：useCallback 不依赖频繁变化的状态
function App() {
  const [learnedWords, setLearnedWords] = useState<string[]>([]);
  const learnedWordsRef = useRef<string[]>([]);

  // 同步 ref
  useEffect(() => {
    learnedWordsRef.current = learnedWords;
  }, [learnedWords]);

  const fetchWords = useCallback(async () => {
    // 从 ref 获取最新值，而不是依赖 state
    const excludeList = learnedWordsRef.current.slice(-30).join('、');
    const result = await window.lingguang.data.fetch(query, schema);
    // ...
  }, []); // ✅ 空依赖，不会因为 learnedWords 变化而重建

  // 仅在初始化时调用一次
  useEffect(() => {
    fetchWords();
  }, []); // ✅ 空依赖，只执行一次

  const handleLearn = (word: string) => {
    setLearnedWords(prev => [...prev, word]); // 不会触发 data.fetch
  };

  // 需要刷新时，由用户主动触发
  const handleRefresh = () => {
    fetchWords(); // ✅ 用户主动点击按钮时调用
  };
}
```

**位置相关查询**

涉及"附近"、"周边"、"本地"等查询时：
- 必须传入 location 参数，且必须包含 pois 字段
- 位置信息通过第三个参数传递，禁止写在 query 字符串里
- ❌ `data.fetch("附近餐厅，位置：经度121，纬度31", schema)`
- ✅ `data.fetch("附近餐厅", schema, location)`

**数据量与性能优化**

接口基于「全网搜索 + LLM 总结」，耗时通常 2-5 秒。请注意：

1. **控制单次返回数据量**
   - 列表类查询默认限制条数（如 5-10 条），避免一次请求过多
   - ❌ "获取所有 AI 新闻"
   - ✅ "获取最近 5 条 AI 新闻"

2. **避免单次请求返回大段内容（分级调用模式）**
   - 每条数据内容过长会导致接口超时或返回不完整
   - 需要详情时，**必须采用「分级调用」模式**：
     - **第一轮**：获取列表（标题、摘要），schema 中 `title` 字段作为"语义锚点"
     - **第二轮**：用户点击后，用 `${item.title} 详细介绍` 作为 query 获取详情
   - 适用场景：新闻正文、商品详情、攻略全文、电影剧情等
   - 详见下方「示例 4：分级调用」

3. **避免单次查询多个独立主题（分主题调用模式）**
   - ⚠️ **这是最常见的错误**：多主题合并查询容易导致数据遗漏或只返回部分结果
   - 搜索引擎对多个独立实体的查询效果很差
   - ❌ "查询贵州茅台、比亚迪、中芯国际的股价" → 可能只返回其中一个
   - ❌ "北京、上海、广州的天气" → 可能只返回一个城市
   - ✅ **必须拆分为串行独立查询**：
   ```javascript
   // 正确做法：串行调用，每次查一个
   const stocks = ['贵州茅台', '比亚迪', '中芯国际'];
   const results = [];
   for (const stock of stocks) {
       const result = await window.lingguang.data.fetch(
           `${stock} 股票行情`,
           singleStockSchema
       );
       if (result) results.push(result);
   }
   ```
   - 适用场景：多只股票、多个城市天气、多款产品、多个人物

**加载状态**

由于接口耗时较长（2-5 秒），必须在 UI 上展示加载状态，避免用户以为无响应。

**避免硬编码大量静态数据**

当应用需要词库、题库、食谱库、知识库等大量数据时：
- ❌ 在代码里硬编码数组（浪费生成 token，数据量有限，无法扩展）
- ✅ 运行时通过 WEB_SEARCH 动态获取，并与本地已有数据去重

```javascript
// 获取新单词，排除已学过的
const learnedWords = await lingguang.storage.getItem('learned_words') || [];
const excludeList = learnedWords.slice(-20).map(w => w.word).join('、');
const query = `CET4高频单词10个，排除这些词：${excludeList}`;
const result = await window.lingguang.data.fetch(query, wordSchema);
```

</usage_guidelines>

<examples caption="使用示例">

**示例 1：获取新闻列表**
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
        // 用适配当前项目的UI呈现 result.news
    } else {
        // 用适配当前项目的UI呈现 空白列表
    }
} catch (error) {
    // 用适配当前项目的UI呈现 接口调用错误
}
```

**示例 2：产品参数对比**
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

const result = await window.lingguang.data.fetch(query, schema);
```

**示例 3：附近餐厅（位置相关）**
```javascript
// 1. 先获取用户位置
const locationResult = await window.lingguang.getLocation({ poi: true });

// 2. 构建 location 参数（必须包含 pois）
const location = {
    longitude: locationResult.longitude,
    latitude: locationResult.latitude,
    province: locationResult.province,
    city: locationResult.city,
    district: locationResult.district,
    pois: locationResult.pois  // ⚠️ 必传！
};

// 3. 查询附近信息
const query = "获取附近5公里内的热门餐厅";
const schema = {
    type: "object",
    properties: {
        restaurants: {
            type: "array",
            items: {
                type: "object",
                properties: {
                    name: { type: "string" },
                    distance: { type: "number", description: "距离（米）" },
                    rating: { type: "number" },
                    category: { type: "string" }
                },
                required: ["name", "distance", "rating"],
                additionalProperties: false
            }
        }
    },
    required: ["restaurants"],
    additionalProperties: false
};

// ⚠️ 位置相关查询必须传第三个参数
const result = await window.lingguang.data.fetch(query, schema, location);
```

**示例 4：分级调用（列表 → 详情）**

适用场景：新闻浏览、搜索结果、攻略列表等需要"先看摘要列表，点击查看详情"的场景。

**原理**：第一轮返回的 `title` 等字段作为"语义锚点"，第二轮用它构造精准查询，获取更详细的内容。

```javascript
// ========== 第一轮：获取摘要列表 ==========
const listSchema = {
    type: "object",
    properties: {
        items: {
            type: "array",
            items: {
                type: "object",
                properties: {
                    title: { type: "string" },              // 作为二轮查询的"锚点"
                    summary: { type: "string" },            // 简短摘要（控制在100字以内）
                    source: { type: "string" },             // 来源
                    publishDate: { type: "string" }
                },
                required: ["title", "summary"],
                additionalProperties: false
            }
        }
    },
    required: ["items"],
    additionalProperties: false
};

const listResult = await window.lingguang.data.fetch(
    "2025年1月AI领域重大进展，返回5条",
    listSchema
);

// 渲染列表 UI
listResult.items.forEach((item, index) => {
    // 显示标题和摘要，点击时触发详情查询
});

// ========== 第二轮：用户点击后获取详情 ==========
async function handleItemClick(item) {
    const detailSchema = {
        type: "object",
        properties: {
            title: { type: "string" },
            content: { type: "string" },           // 这次可以要完整内容
            keyPoints: {                           // 或结构化的要点
                type: "array",
                items: { type: "string" }
            },
            relatedInfo: { type: "string" }
        },
        required: ["title", "content"],
        additionalProperties: false
    };

    // 用第一轮返回的 title 作为精准提示词
    const detailResult = await window.lingguang.data.fetch(
        `${item.title} 详细介绍`,
        detailSchema
    );

    // 渲染详情 UI
    showDetailModal(detailResult);
}
```

**要点**：
- 第一轮 schema 中 `title` 字段是关键，它作为二轮查询的"语义锚点"
- 第一轮的 `summary` 控制字数，避免返回过长内容
- 第二轮 query 格式：`${item.title} 详细介绍` 或 `${item.title} 完整内容`
- 两轮调用都需要展示 loading 状态

**示例 5：词库/题库类应用（替代硬编码）**

适用场景：背单词、刷题、食谱推荐等需要大量数据的应用。**禁止在代码中硬编码数据数组**。

```javascript
// 定义单词数据结构
const wordSchema = {
    type: "object",
    properties: {
        words: {
            type: "array",
            items: {
                type: "object",
                properties: {
                    word: { type: "string" },
                    phonetic: { type: "string" },
                    meaning: { type: "string" },
                    partOfSpeech: { type: "string" },
                    example: { type: "string" }
                },
                required: ["word", "meaning"],
                additionalProperties: false
            }
        }
    },
    required: ["words"],
    additionalProperties: false
};

// 获取新单词（自动去重已学过的）
async function fetchNewWords(difficulty: string, count: number) {
    setLoading(true);
    try {
        // 1. 读取已学单词用于去重
        const learned = await lingguang.storage.getItem('learned_words') || [];
        const excludeWords = learned.slice(-30).map(w => w.word).join('、');

        // 2. 构造查询，包含去重条件
        const query = `${difficulty}英语单词${count}个，包含音标、词性、中文释义、例句，排除：${excludeWords}`;

        // 3. 调用接口获取
        const result = await window.lingguang.data.fetch(query, wordSchema);
        return result?.words || [];
    } finally {
        setLoading(false);
    }
}

// 使用示例
const newWords = await fetchNewWords('CET4高频', 10);
```

**要点**：
- 词库/词典是标准数据，必须用 WEB_SEARCH 搜索获取，禁止用 callLLM 生成（LLM 生成的单词可能有拼写错误、释义不准）
- 数据通过搜索获取，不在代码中硬编码
- 用 `slice(-30)` 限制去重列表长度，避免 query 过长
- 去重逻辑放在 query 中，让搜索引擎排除已学单词

</examples>
