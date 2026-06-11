---
description: 灵光小程序 API：API_LLM_STREAM。与 PRD「所需 API 技能」对齐后按需激活。流式大模型
---

你可以使用流式 LLM API 在生成过程中实时接收片段，并在 UI 中逐步展示结果。

# 流式 LLM API

```typescript
window.lingguang.ai.llmStream(options: LingguangLLMStreamOptions): Promise<void>

interface LingguangLLMStreamOptions {
  /** 用户输入 */
  prompt: string;

  /** 可选：系统提示词 */
  systemPrompt?: string;

  /** 必需：文本更新回调（会被多次触发） */
  onText: (payload: LingguangLLMStreamPayload) => void;

  /** 可选：错误回调 */
  onError?: (err: LingguangLLMStreamError) => void;

  /** 可选：取消控制 */
  signal?: AbortSignal;
}

interface LingguangLLMStreamPayload {
  /** 本次新增的文本片段 */
  delta: string;

  /** 当前完整文本（可直接覆盖 UI） */
  text: string;

  /** 是否为最终结果（最后一次回调为 true） */
  isFinal?: boolean;
}

interface LingguangLLMStreamError {
  /** 人类可读的错误信息 */
  message: string;

  /** 可选：错误码 */
  code?: string;
}
```

## 使用示例

```typescript
import { useState } from 'react';
import Markdown from 'react-markdown';

function StreamedAnswer() {
  const [text, setText] = useState('');

  const handleAsk = async () => {
    const controller = new AbortController();

    try {
      await window.lingguang.ai.llmStream({
        prompt: '请用三句话介绍量子计算。',
        systemPrompt: '你是一个简洁、准确的助手。',
        signal: controller.signal,
        onText: ({ text, delta, isFinal }) => {
          setText(text); // ✅ 直接覆盖 UI
          // 也可以用 delta 自行拼接
          if (isFinal) {
            console.log('done');
          }
        },
        onError: (err) => {
          showError(err?.message || '大模型调用失败，请稍后重试'); // showError：请按当前项目的提示风格实现，禁止直接console.error
        }
      });
    } catch (e) {
      showError(e?.message || '大模型调用失败，请稍后重试'); // showError：请按当前项目的提示风格实现，禁止直接console.error
    }
  };

  return (
    <div>
      <button onClick={handleAsk}>开始生成</button>
      <Markdown>{text}</Markdown>
    </div>
  );
}
```

> **提示**：对于流式输出的 markdown 文本，使用 markdown 组件渲染更友好。推荐使用 `react-markdown`：
> ```typescript
> import Markdown from 'react-markdown'
> ```

## 行为约定（调用者须知）

* `onText.text` 始终是“当前完整文本”，可以直接覆盖 UI。
* `onText.delta` 是本次流片段内容，方便自定义拼接或逐字渲染。
* `isFinal` 为 `true` 表示流结束（最后一次回调）。
* 需要中止生成时，可通过 `AbortController` 触发 `signal.abort()`。
* 对于流式输出的 markdown 文本，建议使用 markdown 组件（如 `react-markdown`）进行渲染，以获得更好的展示效果。
