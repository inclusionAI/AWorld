---
description: 灵光小程序 API：API_AGENTIC_DATAFETCH。与 PRD「所需 API 技能」对齐后按需激活。渐进式探索UI
---

<skill_overview>
## 渐进式探索UI (AGENTIC_DATAFETCH) - 逐层深入交互

渐进式交互API：点击按钮 → LLM生成HTML（可选WebSearch获取真实信息） → 包含新按钮 → 层层深入。

**适用场景：** 主题探索浏览、知识图谱导航、分支式推演/故事、交互式学习、无限展开内容导航
</skill_overview>

<critical_architecture>
## ⚠️ 强制架构（违反即产生严重bug，写代码前必读）

### 致命陷阱

你的第一直觉是创建 `DetailPage` 子组件，把 explore 状态放在里面。**这是错的**：子组件被条件渲染，用户回首页时组件卸载 → explore 历史全部丢失，再次进入从头开始。

```tsx
// ❌ 你的直觉（错误！）— DetailPage 卸载 = 历史丢失
function DetailPage({ topic, onBack }) {
  const [html, setHtml] = useState('');
  const [history, setHistory] = useState([]); // 卸载就没了
  const [historyIndex, setHistoryIndex] = useState(-1); // 卸载就没了
  ...
}
function App() {
  return page === 'detail' ? <DetailPage /> : <HomePage />;
  // 回首页 → DetailPage 卸载 → 再进入 → 历史全丢
}
```

### 正确架构：所有 explore 状态和函数必须在 App 中

以下是**强制骨架代码**，你必须在 App 组件中包含这些状态和函数，在此基础上添加业务UI。子组件只通过 props 接收数据和回调。

```tsx
{% raw %}
import { useState, useRef, useCallback, useEffect } from 'react';

function App() {
  // ===== explore 状态（全部在 App，禁止放子组件） =====
  const [activeKey, setActiveKey] = useState<string | null>(null); // null=首页
  const [html, setHtml] = useState('');
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [loading, setLoading] = useState(false);
  const loadingRef = useRef(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const cacheRef = useRef<Record<string, { html: string; history: string[]; historyIndex: number }>>({});
  const [pendingQuery, setPendingQuery] = useState<string | null>(null);

  // ===== explore 核心函数（支持流式渲染） =====
  const explore = useCallback(async (query: string) => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    let lastStreamedHtml = '';  // 记录最后一次流式渲染的 HTML
    try {
      // 获取页面上下文用于主题检测
      // 重要：第一次探索时 contentRef 为空，需要传入应用背景色提示让 explore 判断主题
      // 建议：为空时拼接一个背景色提示字符串，如 "背景色: #0F1419"（根据你的应用实际背景色）
      let currentHTML = contentRef.current?.innerHTML || '';
      if (!currentHTML) {
        // 首次探索时 contentRef 为空，传入应用背景色提示
        // 请根据你的应用实际背景色修改这个值（深色背景用深色值如 #0F1419/#121212，浅色背景用 #fff/#f5f5f5）
        currentHTML = '背景色: #0F1419';  // 示例：深色背景
      }
      const result = await (window as any).lingguang.data.explore(
        query,
        currentHTML,
        // 流式回调：实时更新UI，让用户看到渐进式内容
        (accumulatedHtml: string) => {
          lastStreamedHtml = accumulatedHtml;
          setHtml(accumulatedHtml);
        }
      );
      // 使用流式回调时，result 为 null（内容已通过回调渲染）
      // 不使用流式回调时，result 为 HTML 字符串
      const finalHtml = result || lastStreamedHtml || '<div style="padding:20px;color:#999;">暂无内容</div>';
      if (result) {
        // 只有非流式模式才需要 setHtml（流式模式已通过回调渲染）
        setHtml(finalHtml);
      }
      setHistory(prev => [...prev.slice(0, historyIndex + 1), finalHtml]);
      setHistoryIndex(prev => prev + 1);
    } catch (err: any) {
      setHtml(`<div style="padding:20px;color:#ff4d4f;">加载失败：${err.message || '未知错误'}</div>`);
    } finally {
      setLoading(false);
      loadingRef.current = false;
    }
  }, [historyIndex]);

  // pendingQuery 一次性触发（禁止用 useEffect 监听其他 state 调用 explore）
  useEffect(() => {
    if (pendingQuery) { explore(pendingQuery); setPendingQuery(null); }
  }, [pendingQuery, explore]);

  // 缓存：保存当前入口的 explore 状态
  const saveCache = useCallback(() => {
    if (activeKey && history.length > 0) {
      cacheRef.current[activeKey] = { html, history, historyIndex };
    }
  }, [activeKey, html, history, historyIndex]);

  // 入口点击：有缓存则恢复，无缓存才 explore
  const handleEntry = useCallback((key: string, query: string) => {
    if (loadingRef.current) return;
    saveCache(); // 先保存当前入口
    const cached = cacheRef.current[key];
    if (cached) {
      setHtml(cached.html); setHistory(cached.history); setHistoryIndex(cached.historyIndex);
    } else {
      setHtml(''); setHistory([]); setHistoryIndex(-1); setPendingQuery(query);
    }
    setActiveKey(key);
  }, [saveCache]);

  // 返回首页：只保存缓存+切换视图，禁止清空 history！
  const handleBackHome = useCallback(() => { saveCache(); setActiveKey(null); }, [saveCache]);

  // 后退/前进
  const handleBack = () => {
    if (historyIndex > 0) { setHistoryIndex(historyIndex - 1); setHtml(history[historyIndex - 1]); }
    else { handleBackHome(); }
  };
  const handleForward = () => {
    if (historyIndex < history.length - 1) { setHistoryIndex(historyIndex + 1); setHtml(history[historyIndex + 1]); }
  };

  // 事件委托：监听 explore 返回的 HTML 中的 data-query 按钮
  const handleContentClick = (e: React.MouseEvent) => {
    const btn = (e.target as HTMLElement).closest('button[data-query]');
    if (btn) { const q = btn.getAttribute('data-query'); if (q) explore(q); }
  };

  // ===== 首页视图 =====
  // 如果首页有搜索框，handleSearch 应该用 query 本身作为 key（保证不同搜索有不同缓存）
  const handleSearch = (query: string) => {
    handleEntry(query, query);  // key 用 query 本身，不要用固定字符串如 'initial'！
  };

  if (!activeKey) {
    return (
      <div style={{ padding: '20px' }}>
        {/* 固定入口：用固定 key，可以缓存该入口的历史 */}
        <button onClick={() => handleEntry('topic1', '主题1 详细介绍')}>主题1</button>
        <button onClick={() => handleEntry('topic2', '主题2 详细介绍')}>主题2</button>
        {/* 搜索入口：用 query 作为 key，不同搜索词有不同缓存！
            ❌ 错误：handleEntry('initial', query) — 所有搜索共用一个缓存，会显示错误内容
            ✅ 正确：handleEntry(query, query) — 每个搜索词独立缓存 */}
      </div>
    );
  }

  // ===== 详情视图 =====
  return (
    <div style={{ padding: '20px' }}>
      <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
        <button onClick={handleBack} disabled={loading}>← 返回</button>
        <button onClick={handleForward} disabled={historyIndex >= history.length - 1 || loading}>前进 →</button>
        {history.length > 0 && (
          <span style={{ fontSize: '12px', color: '#999' }}>{historyIndex + 1}/{history.length}</span>
        )}
      </div>
      {/* 流式渲染：loading 提示叠加在内容上方，内容始终可见！
          禁止用 display:none 隐藏内容，否则看不到流式更新

          下面是必须的结构，样式请根据你的应用主题自由设计 */}
      <div style={{ position: 'relative' }}>
        {/* 初次加载（无内容时）的 loading 提示 - 样式自由设计 */}
        {loading && !html && (
          <div>{/* 自行设计 loading 动画/提示，与应用主题一致 */}</div>
        )}
        {/* 流式加载中（有内容时）的提示条 - 样式自由设计，不要遮挡内容 */}
        {loading && html && (
          <div>{/* 自行设计加载提示，建议 position:absolute 叠加在内容上方 */}</div>
        )}
        {/* 内容容器 - 必须有 ref 和 onClick，样式自由设计 */}
        <div
          ref={contentRef}
          onClick={handleContentClick}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
    </div>
  );
}
{% endraw %}
```

你可以在此骨架基础上自由添加业务UI（首页布局、动画、子组件等），但 **explore 相关的 state 和函数必须留在 App 中**，子组件只通过 props 接收。
</critical_architecture>

<api_reference>
## API 签名

```typescript
window.lingguang.data.explore(
  query: string,
  currentHTML?: string,
  onStreamChunk?: (accumulatedHtml: string) => void
): Promise<string | null>
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `query` | string, 必填 | 查询内容，**必须包含完整上下文**（如"特斯拉公司 热门产品"而非"热门产品"） |
| `currentHTML` | string, 可选 | 当前显示的HTML，让LLM理解上下文生成更连贯内容 |
| `onStreamChunk` | function, 可选 | 流式回调，接收累积的HTML，用于实时渲染（不传则等完成后一次性返回） |

**返回值：** `Promise<string | null>` - 如果**使用了流式回调**，返回 `null`（内容已通过回调渲染，无需再次 setHtml）；否则返回包含内联样式的HTML字符串。

**性能：** 每次 3-30 秒，不支持并发调用。

### 流式渲染示例

```tsx
const explore = useCallback(async (query: string) => {
  if (loadingRef.current) return;
  loadingRef.current = true;
  setLoading(true);
  let lastStreamedHtml = '';  // 记录最后一次流式渲染的 HTML
  try {
    const currentHTML = contentRef.current?.innerHTML || '';
    const result = await (window as any).lingguang.data.explore(
      query,
      currentHTML,
      // 流式回调：实时更新UI（可选但推荐）
      (accumulatedHtml: string) => {
        lastStreamedHtml = accumulatedHtml;
        setHtml(accumulatedHtml);
      }
    );
    // 使用流式回调时，result 为 null（内容已通过回调渲染）
    // 不使用流式回调时，result 为 HTML 字符串
    const finalHtml = result || lastStreamedHtml || '<div style="padding:20px;color:#999;">暂无内容</div>';
    if (result) {
      // 只有非流式模式才需要 setHtml（流式模式已通过回调渲染）
      setHtml(finalHtml);
    }
    setHistory(prev => [...prev.slice(0, historyIndex + 1), finalHtml]);
    setHistoryIndex(prev => prev + 1);
  } catch (err: any) {
    setHtml(`<div style="padding:20px;color:#ff4d4f;">加载失败：${err.message || '未知错误'}</div>`);
  } finally {
    setLoading(false);
    loadingRef.current = false;
  }
}, [historyIndex]);
```

### 流式渲染 UI（重要！）

{% raw %}
```tsx
{/* 禁止用 display:none 隐藏内容！否则看不到流式更新 */}
<div style={{ position: 'relative' }}>
  {/* 初次加载的提示 - 样式自由设计 */}
  {loading && !html && <div>{/* 自行设计 loading 提示 */}</div>}
  {/* 流式加载中的提示 - 样式自由设计，建议 position:absolute 不遮挡内容 */}
  {loading && html && <div>{/* 自行设计加载中提示 */}</div>}
  {/* 内容容器必须始终存在 */}
  <div ref={contentRef} onClick={handleContentClick} dangerouslySetInnerHTML={{ __html: html }} />
</div>
```
{% endraw %}
</api_reference>

<theming>
## 主题适配（自动检测）

explore 会**自动分析 currentHTML 的视觉风格**，生成与你的应用主题和谐的 HTML：

1. **深色主题检测**：如果 currentHTML 包含深色背景（如 `#121212`、`bg-[#121212]`），会使用深色配色方案
2. **浅色主题检测**：如果 currentHTML 包含浅色背景，会使用浅色配色方案
3. **强调色提取**：会尝试从 currentHTML 中提取按钮、链接的颜色作为主色调

### 首次加载时的主题提示（重要！）

首次进入探索页面时，`contentRef.current` 为空，explore 无法检测主题。**必须手动传入背景色提示**：

```tsx
let currentHTML = contentRef.current?.innerHTML || '';
if (!currentHTML) {
  // 首次探索时传入应用背景色提示（根据你的应用实际背景色修改）
  currentHTML = '背景色: #0F1419';  // 深色背景示例
  // 或：currentHTML = '背景色: #ffffff';  // 浅色背景示例
}
const result = await window.lingguang.data.explore(query, currentHTML, callback);
```

### 后续加载

有内容后，直接传入 `contentRef.current?.innerHTML`，explore 会自动分析现有内容的风格。
</theming>

<usage_guidelines>
## 禁忌清单

1. **禁止**把 explore 状态（html/history/historyIndex/loadingRef/cacheRef）放在会被条件渲染卸载的子组件中 — 必须在 App 顶层。
2. **禁止**在 `useEffect([stateVar])` 中调用 `explore()` — 只允许 `pendingQuery` 一次性触发或用户事件（onClick/事件委托）直接调用。
3. **禁止**返回首页时清空 `history`/`historyIndex` — 只能 `saveCache()` + `setActiveKey(null)`。
4. **禁止**每次进入入口都重新 `explore()` — 必须先检查 `cacheRef` 缓存，有缓存则恢复。
5. **禁止** `data-query` 只写子话题 — 必须包含「主题 + 子话题」完整上下文（如 `data-query="特斯拉公司 热门产品"`）。
6. **禁止** loading 期间重复触发 — 用 `loadingRef.current`（同步 ref）守卫，不能用 `loading` state（异步）。
7. **禁止**用条件渲染卸载 content div — 必须始终保留在 DOM 中。否则动画库（如 AnimatePresence）会导致 loading 结束后内容不渲染。
8. **禁止**流式渲染时用 `display:none` 隐藏内容 — 这会导致用户看不到流式更新！应该让内容始终可见，loading 提示叠加在内容上方。
9. **禁止**在使用流式回调时对 `result` 调用 `setHtml` — 流式回调模式下 `result` 为 `null`，内容已通过回调渲染，只需用 `result || lastStreamedHtml` 更新 history（参考骨架代码）。
10. **禁止**搜索入口用固定 key — 如 `handleEntry('initial', query)` 会导致所有搜索共用一个缓存，返回首页再搜新词会显示旧内容！必须用 `handleEntry(query, query)`，让每个搜索词有独立缓存。
11. **禁止**在内容容器（`dangerouslySetInnerHTML` 所在的 div）上使用 `motion.div` 的 `initial/animate` — 每次 setHtml 会触发动画重播导致闪烁白屏。
</usage_guidelines>
