---
description: 灵光小程序 API：API_ASR。与 PRD「所需 API 技能」对齐后按需激活。实时语音转文字
---

你可以使用ASR API来实现语音输入、会议记录、实时字幕、语音搜索和语音助手等应用场景。

# ASR（实时语音转文字）API

## 快速开始

```tsx
{% raw %}
import { useState } from 'react';

function SimpleASR() {
  const [text, setText] = useState('');

  const handleStart = async () => {
    try {
      await window.lingguang.asr.start({
        lang: "zh-CN",
        interim: true,
        onText: ({ text }) => {
          // ✅ 重要：text 是从开始识别到当前的全部完整文本，直接覆盖 UI 即可
          setText(text);
        },
        onError: (err) => {
          console.error("ASR error:", err);
          // toast(err.message ?? err.error);
        },
        onEnd: (reason) => {
          console.log("ASR ended:", reason);
        }
      }); // 若没有麦克风权限/设备不可用，这里直接 throw
    } catch (e) {
      console.error("启动失败:", e);
    }
  };

  return (
    <div>
      <input value={text} onChange={(e) => setText(e.target.value)} />
      <button onClick={handleStart}>开始识别</button>
    </div>
  );
}
{% endraw %}
```

---

## 设计原则

* `start()`：只负责“能否成功启动识别”。无法启动（如没权限）**直接抛错**。
* `onText(payload)`：持续推送“当前完整文本”，业务侧**每次覆盖 UI**即可。
* `onError(err)`：识别过程中出现异常并导致中断时触发（随后会 `onEnd('error')`）。
* `stop()`：优雅停止（尽量产出最终文本后结束）。
* `abort()`：立即停止（不保证最终文本）。

---

## 类型定义

```ts
{% raw %}
export type ASREndReason = "stop" | "abort" | "error";

export type ASRTextPayload = {
  /** 
   * 可直接覆盖 UI 的完整文本（从开始识别到当前的全部文本，包含已确认的 final + 当前 interim 的合成结果）
   * ⚠️ 重要：每次回调的 text 都是完整文本，请直接覆盖 UI，不要累加！
   * ✅ 正确：setText(payload.text);
   * ❌ 错误：setText(prev => prev + payload.text); // 会导致重复累加
   */
  text: string;

  /** 本次回调所属会话 id（用于调试/排障；一般业务不需要用） */
  sessionId: string;

  /** 单调递增序号（可选；用于丢弃乱序包；一般业务不需要用） */
  seq?: number;

  /** 可选：本次回调是否包含 final 进展（业务通常可忽略） */
  isFinal?: boolean;
};

export type ASRError = {
  /** 机器可读的错误类型 */
  error:
    | "not-allowed"     // 无权限 / 被拒绝
    | "audio-capture"   // 设备不可用/被占用
    | "no-speech"       // 未检测到有效语音
    | "network"         // 需要网络但不可用
    | "engine"          // 引擎内部错误
    | "aborted"         // 被中止（如系统打断）
    | "unknown";

  /** 人类可读的提示 */
  message?: string;

  /** 是否致命（通常为 true 时会结束会话） */
  fatal?: boolean;

  /** 本次错误所属会话 */
  sessionId: string;

  /** 可选：更细粒度错误码 */
  code?: string;
};

/**
 * start 的参数：配置项 + 回调
 */
interface ASRStartOptions {
  /** 识别语言，如 "zh-CN" */
  lang?: string;

  /** 是否输出临时结果（默认 true） */
  interim?: boolean;

  /** 是否连续识别（默认 false） */
  continuous?: boolean;

  /**
   * 必需：识别文本更新回调（可直接覆盖 UI）
   */
  onText: (payload: ASRTextPayload) => void;

  /**
   * 可选：识别过程异常回调（会导致会话结束）
   */
  onError?: (err: ASRError) => void;

  /**
   * 可选：会话结束通知回调
   */
  onEnd?: (reason: ASREndReason, sessionId: string) => void;
}
{% endraw %}
```

---

## API：`window.lingguang.asr`

```ts
{% raw %}
window.lingguang.asr.start(options: ASRStartOptions): Promise<void>
window.lingguang.asr.stop(): Promise<void>
window.lingguang.asr.abort(): Promise<void>
{% endraw %}
```

**lingguang.asr.start 方法失败时（reject）返回：**

```javascript
{% raw %}
{
  code: 'PERMISSION_REQUIRED',  // 错误类型枚举（string）
  message: '需要权限'  // 错误信息（string）
}
{% endraw %}
```

---

## 使用示例

### 1) 输入框语音输入（最常见）

```tsx
{% raw %}
import { useState } from 'react';

function VoiceInput() {
  const [text, setText] = useState('');
  const [isRecording, setIsRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleStart = async () => {
    try {
      setError(null);
      setIsRecording(true);
      await window.lingguang.asr.start({
        lang: "zh-CN",
        interim: true,
        onText: ({ text }) => {
          setText(text);
        },
        onError: (e) => {
          console.error(e);
          setError(e.message ?? `ASR error: ${e.error}`);
          setIsRecording(false);
        },
        onEnd: (reason) => {
          console.log("ended:", reason);
          setIsRecording(false);
        }
      }); // ✅ 没权限会直接 throw
    } catch (e) {
      setError("无法启动语音识别，请检查麦克风权限或设备状态。");
      setIsRecording(false);
    }
  };

  const handleStop = async () => {
    await window.lingguang.asr.stop();
  };

  const handleAbort = async () => {
    await window.lingguang.asr.abort();
  };

  return (
    <div>
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="语音输入或手动输入"
      />
      <div>
        <button onClick={handleStart} disabled={isRecording}>
          开始识别
        </button>
        <button onClick={handleStop} disabled={!isRecording}>
          停止
        </button>
        <button onClick={handleAbort} disabled={!isRecording}>
          中止
        </button>
      </div>
      {error && <div style={{ color: 'red' }}>{error}</div>}
      {isRecording && <div>正在录音...</div>}
    </div>
  );
}
{% endraw %}
```

---

### 2) “按住说话，松开结束”（移动端常见交互）

```tsx
{% raw %}
import { useState, useRef } from 'react';

function HoldToSpeak() {
  const [text, setText] = useState('');
  const [isRecording, setIsRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const handleStart = async () => {
    try {
      setError(null);
      setIsRecording(true);
      await window.lingguang.asr.start({
        lang: "zh-CN",
        interim: true,
        onText: ({ text }) => {
          // ✅ text 是完整文本，直接覆盖 UI
          setText(text);
        },
        onError: (e) => {
          console.error(e);
          setError(e.message ?? `录音失败: ${e.error}`);
          setIsRecording(false);
        },
        onEnd: () => {
          setIsRecording(false);
        }
      });
    } catch (e) {
      setError("无法启动语音识别，请检查麦克风权限或设备状态。");
      setIsRecording(false);
    }
  };

  const handleStop = async () => {
    await window.lingguang.asr.stop();
  };

  const handleAbort = async () => {
    await window.lingguang.asr.abort();
  };

  // 移动端：touch 事件
  const handleTouchStart = async (e: React.TouchEvent) => {
    e.preventDefault(); // 防止页面滚动
    e.stopPropagation(); // 阻止事件冒泡
    await handleStart();
  };

  const handleTouchEnd = async (e: React.TouchEvent) => {
    e.preventDefault();
    e.stopPropagation();
    await handleStop();
  };

  const handleTouchCancel = async (e: React.TouchEvent) => {
    e.preventDefault();
    e.stopPropagation();
    await handleAbort();
  };

  // 移动端：阻止长按手势（contextmenu 事件）
  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault(); // 阻止长按菜单
    e.stopPropagation();
  };

  // PC 端：pointer 事件（可选，用于鼠标操作）
  const handlePointerDown = async (e: React.PointerEvent) => {
    if (e.pointerType === "mouse") {
      await handleStart();
    }
  };

  const handlePointerUp = async (e: React.PointerEvent) => {
    if (e.pointerType === "mouse") {
      await handleStop();
    }
  };

  const handlePointerCancel = async (e: React.PointerEvent) => {
    if (e.pointerType === "mouse") {
      await handleAbort();
    }
  };

  return (
    <div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="按住按钮说话"
        rows={5}
      />
      <button
        ref={buttonRef}
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
        onTouchCancel={handleTouchCancel}
        onContextMenu={handleContextMenu}
        onPointerDown={handlePointerDown}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerCancel}
        style={{
          padding: '20px',
          fontSize: '16px',
          backgroundColor: isRecording ? '#ff4444' : '#4CAF50',
          color: 'white',
          border: 'none',
          borderRadius: '8px',
          cursor: 'pointer',
          // 屏蔽移动端长按手势和文本选择
          touchAction: 'none', // 禁用所有触摸手势（包括长按、双击缩放等）
          userSelect: 'none', // 禁用文本选择
          WebkitUserSelect: 'none', // Safari/Chrome
          WebkitTouchCallout: 'none', // iOS 禁用长按菜单
          WebkitTapHighlightColor: 'transparent' // 移除点击高亮
        }}
      >
        {isRecording ? '正在录音...' : '按住说话'}
      </button>
      {error && <div style={{ color: 'red', marginTop: '10px' }}>{error}</div>}
    </div>
  );
}
{% endraw %}
```

**移动端实现要点说明：**

1. **屏蔽长按手势**：
   - `touchAction: 'none'`：禁用所有触摸手势，包括长按菜单、双击缩放、拖拽等
   - `onContextMenu` 事件处理：额外阻止右键菜单（某些浏览器可能仍会触发）
   - `WebkitTouchCallout: 'none'`：专门针对 iOS Safari 禁用长按菜单

2. **禁用文本选择**：
   - `userSelect: 'none'`：标准属性，禁用文本选择
   - `WebkitUserSelect: 'none'`：Safari/Chrome 浏览器前缀

3. **事件处理优化**：
   - `touchstart` 事件中调用 `preventDefault()` 和 `stopPropagation()` 防止页面滚动和事件冒泡
   - `WebkitTapHighlightColor: 'transparent'` 移除移动端点击高亮效果

---

## 行为约定（调用者须知）

* **⚠️ 重要：`onText.text` 始终是"从开始识别到当前的全部完整文本"**
  * ✅ **正确用法**：直接覆盖 UI，如 `setText(payload.text)`
  * ❌ **错误用法**：不要累加文本，如 `setText(prev => prev + payload.text)` 会导致重复累加
  * `text` 参数已经包含了所有已确认的 final 文本和当前 interim 文本的完整合成结果，你不需要自己拼接或累加
* `start()` 必须由**用户手势触发**更稳（点击/触摸等），否则某些环境可能拒绝麦克风。
* `stop()/abort()` **幂等**：重复调用不会抛异常（推荐实现如此；调用者也不要依赖抛错来判断状态）。
* `onError` 表示“识别过程出错并中断”，随后会触发 `onEnd('error')`（如果你实现里如此约定）。

## 常见错误

### ❌ 错误：累加文本

```tsx
{% raw %}
// ❌ 错误示例：不要这样做！
onText: (payload: { text: string; isFinal?: boolean }) => {
  setRecognizedText(prev => prev + payload.text); // 会导致文本重复累加
},
{% endraw %}
```

**问题**：`payload.text` 已经是完整文本，累加会导致每次回调都重复添加，最终文本会变成：
- 第1次回调：`"你好"`
- 第2次回调：`"你好你好世界"`（错误！）
- 第3次回调：`"你好你好世界你好世界今天"`（错误！）

### ✅ 正确：直接覆盖

```tsx
{% raw %}
// ✅ 正确示例：直接覆盖即可
onText: ({ text }) => {
  setRecognizedText(text); // text 已经是完整文本，直接覆盖
},
{% endraw %}
```

**正确行为**：
- 第1次回调：`"你好"`
- 第2次回调：`"你好世界"`（正确！）
- 第3次回调：`"你好世界今天"`（正确！）
