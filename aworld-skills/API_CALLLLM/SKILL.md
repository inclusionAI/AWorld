---
description: 灵光小程序 API：API_CALLLLM。与 PRD「所需 API 技能」对齐后按需激活。LLM 调用
---

- 如果你想要调用llm的能力，你可以直接使用window.callLLM(message, system_prompt, timeout)，获得llm的返回（js代码已经预置在了mini_app_base js中）
  - message: 用户输入的问题，约等于user_prompt（必传）
  - system_prompt: 自定义系统提示词（可选，用于设定AI角色和行为）
  - timeout: 超时时间，单位毫秒（可选，默认60000ms）
  - 返回Promise: resolve为{content: "AI回答内容", extInfo: {...}}，reject为错误信息
  - 基本使用示例：
    ```javascript
    // 基本调用
    const result = await window.callLLM("你好，请介绍一下自己");
    
    // 自定义系统提示词
    const result = await window.callLLM("用户问题", "你是一个专业的编程助手");
    
    // 设置超时时间
    const result = await window.callLLM("问题", null, 30000);
    ```

- **⚠️ 重要：callLLM() 的调用时机规范**：
  
  **情况1：明确需要调用的时机（✅ 可以调用）**
  - 用户主动触发的发送事件：
    - 按钮的 click 事件（用户点击"分析"、"生成"、"提交"等按钮）
    - 输入框的 keydown 事件（用户按 Enter 键发送）
    - 表单的 submit 事件（用户提交表单）
  - 应用到达明确可发送的状态：
    - 应用内状态机的某个状态已经明确ready，需要调用llm接口获取llm的返回
  
  **情况2：不明确需要调用的时机（❌ 不应该调用）**
  - 用户还在操作中，没有明确的"发送意图"：
    - input 事件（用户还在输入，可能还在思考）
    - keydown/keyup 事件（非 Enter 的普通按键，用户还在打字）
    - scroll 事件（用户还在滚动）
    - mousemove 事件（用户还在移动鼠标）
    - change 事件（如果变化频繁）
  - ⚠️ **绝对禁止**：不应该在这些事件上调用 callLLM()接口

{% if use_react_scaffold -%}
- **使用示例（React 版本）**：
  ```tsx
  // ✅ 正确示例1：绑定到按钮 onClick 事件（明确发送意图）
  function App() {
    const handleAnalyze = async () => {
      try {
        const result = await window.callLLM("分析用户需求");
        // 处理结果
      } catch (error: unknown) {
        const msg = error instanceof Error ? error.message : 'AI 调用失败，请稍后重试';
        showError(msg); // （showError：请按当前项目的提示风格实现，禁止直接console.error）
      }
    };
    
    return (
      <button onClick={handleAnalyze}>分析</button>
    );
  }

  // ✅ 正确示例2：绑定到 Enter 键（明确发送意图）
  function App() {
    const [inputValue, setInputValue] = useState<string>('');
    
    const handleKeyDown = async (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        const message = inputValue.trim();
        if (!message) return;
        
        try {
          const result = await window.callLLM(message);
          // 处理结果
        } catch (error: unknown) {
          const msg = error instanceof Error ? error.message : 'AI 调用失败，请稍后重试';
          showError(msg); // （showError：请按当前项目的提示风格实现，禁止直接console.error）
        }
      }
    };

    return (
      <input
        value={inputValue}
        onChange={(e: React.ChangeEvent<HTMLInputElement>) => setInputValue(e.target.value)}
        onKeyDown={handleKeyDown}
      />
    );
  }

  // ❌ 错误示例1：在 onChange 事件中调用（用户还在输入，不是发送意图）
  function App() {
    const [inputValue, setInputValue] = useState<string>('');
    
    return (
      <input 
        value={inputValue}
        onChange={async (e: React.ChangeEvent<HTMLInputElement>) => {
          setInputValue(e.target.value);
          // ❌ 错误：onChange 事件 = input 事件，不应该调用 callLLM
          await window.callLLM(e.target.value);
        }}
      />
    );
  }

  // ❌ 错误示例2：使用 useEffect 监听输入变化（等同于监听 input 事件）
  function App() {
    const [inputText, setInputText] = useState<string>('');
    
    useEffect(() => {
      if (inputText.trim()) {
        // ❌ 错误：useEffect 监听 inputText 变化 = 监听用户输入 = input 事件
        // 不应该在用户还在输入时调用 callLLM
        window.callLLM(inputText);
      }
    }, [inputText]); // ❌ 依赖 inputText，会在用户每次输入时触发
    
    return (
      <input 
        value={inputText}
        onChange={(e: React.ChangeEvent<HTMLInputElement>) => setInputText(e.target.value)}
      />
    );
  }

  // ❌ 错误示例3：使用 useEffect + setTimeout 监听输入变化（仍然是监听用户输入）
  function App() {
    const [inputText, setInputText] = useState<string>('');
    
    useEffect(() => {
      if (inputText.trim()) {
        const timer = setTimeout(() => {
          // ❌ 错误：即使加了延迟，仍然是监听用户输入变化
          // useEffect([inputText]) = 监听 input 事件，不应该调用 callLLM
          window.callLLM(inputText);
        }, 500);
        return () => clearTimeout(timer);
      }
    }, [inputText]); // ❌ 依赖 inputText，会在用户每次输入时触发
    
    return (
      <input 
        value={inputText}
        onChange={(e: React.ChangeEvent<HTMLInputElement>) => setInputText(e.target.value)}
      />
    );
  }
  ```

- **⚠️ 重要：React 中的等价关系**：
  - `onChange` 事件 = 原生 JS 的 `input` 事件（不应该调用 callLLM）
  - `useEffect(() => {...}, [inputText])` = 监听用户输入变化 = 监听 `input` 事件（不应该调用 callLLM）
  - `onClick` 事件 = 原生 JS 的 `click` 事件（可以调用 callLLM）
  - `onKeyDown` 事件（Enter键）= 原生 JS 的 `keydown` 事件（可以调用 callLLM）
{% else -%}
- **使用示例（原生 JavaScript 版本）**：
  ```javascript
  // ✅ 正确示例1：绑定到按钮 click 事件（明确发送意图）
  const analyzeBtn = document.getElementById('analyzeBtn');
  analyzeBtn.addEventListener('click', async () => {
    try {
      const result = await window.callLLM("分析用户需求");
      // 处理结果
    } catch (error) {
      const msg = error?.message || 'AI 调用失败，请稍后重试';
      showError(msg); // （showError：请按当前项目的提示风格实现，禁止直接console.error）
    }
  });

  // ✅ 正确示例2：绑定到 Enter 键（明确发送意图）
  const input = document.getElementById('input');
  input.addEventListener('keydown', async (e) => {
    if (e.key === 'Enter') {
      const message = input.value.trim();
      if (!message) return;
      
      try {
        const result = await window.callLLM(message);
        // 处理结果
      } catch (error) {
        const msg = error?.message || 'AI 调用失败，请稍后重试';
        showError(msg); // （showError：请按当前项目的提示风格实现，禁止直接console.error）
      }
    }
  });

  // ✅ 正确示例3：应用状态稳定变化（明确可发送状态）
  // 当从"未上传"状态变为"已上传"状态时
  if (uploadedImages.length > 0 && previousState === '未上传') {
    const result = await window.callLLM("分析上传的照片");
  }

  // ❌ 错误示例1：绑定到 input 事件（用户还在输入，不是发送意图）
  input.addEventListener('input', (e) => {
    window.callLLM(e.target.value); // ❌ 错误
  });

  // ❌ 错误示例2：绑定到 scroll 事件（用户还在滚动）
  window.addEventListener('scroll', () => {
    window.callLLM("分析页面内容"); // ❌ 错误
  });
  ```
{% endif -%}

- **错误处理**：建议使用try-catch包装调用，网络错误、超时等会自动reject
- **技术特性**：支持多个并发进行LLM调用互不干扰；不支持llm流式调用，只能一次性返回llm的输出。
