---
description: 灵光小程序 API：API_MUSICGEN。与 PRD「所需 API 技能」对齐后按需激活。音乐生成
---

# window.lingguang.ai.musicGeneration

AI音乐生成能力，根据文本描述，在应用运行时，用户主动触发调用该js api，生成音乐。支持纯音乐和带歌词的歌曲的生成。

## 重要：运行时生成 vs 生成阶段预置

- 如果需求是“用户在应用里点按钮生成音乐”，使用本页的 `window.lingguang.ai.musicGeneration`（运行时 JS 接口）。
- 如果需求是“应用一打开就内置/默认/预置两首音乐（可直接播放）”，且用户没有提供现成 mp3 URL，你必须在写代码前先用 `CapabilityCall(mode="invoke", capability="asset.music_generate", arguments={...})` 在生成阶段生成 mp3 URL，然后把 URL 写进代码作为内置曲目。
- 不要用“预置音效库/示例音乐（例如 SOUND_LIB）”去替代“内置两首音乐”的要求，除非需求明确允许使用预置库。
- 两者可以同时存在：内置曲目用 CapabilityCall 预生成；用户交互生成用运行时 JS 接口。

## 函数签名

```typescript
window.lingguang.ai.musicGeneration(params: {
  prompt: string;
  lyrics?: string;
  duration?: number;
}): Promise<{
  url: string;
  duration: number;
}>
```

## 参数

**params** (Object): 请求参数对象

- **prompt** (string, 必需): 音乐风格描述（英文）
  - 描述音乐曲风、情绪等属性
  - 示例: "pop, electronic, cheerful and festive with bright synths and steady beat"
- **lyrics** (string, 可选): 歌词内容
  - 生成歌曲时：按照结构化格式输入歌词，如 "[intro]\\n[verse]\\n歌词内容\\n[chorus]\\n副歌内容\\n[outro]"
  - 生成纯音乐时：留空或传入空字符串 ""
  - 默认值: ""
- **duration** (number, 可选): 音乐时长（秒）
  - 范围: 30-240 秒
  - **建议设置为 30 秒左右**：生成耗时较短，用户体验更好；如需长时间播放，可通过 `<audio>` 标签的 `loop` 属性实现循环播放
  - 歌曲时长需要和歌词长度匹配，建议 16 句歌词对应时长 130s（但请注意，过长的音乐会导致生成耗时显著增加，可能影响用户体验）
  - 默认值: 30

## 返回值

返回 Promise，成功时 resolve，失败时 reject。

**成功时（resolve）返回：**

```javascript
{
  url: 'https://example.com/generated-music.mp3',  // 生成音乐的URL（string）
  duration: 29.9  // 实际生成音乐时长（秒，number）
}
```

**失败时（reject）返回：**

```javascript
{
  name: 'ERROR_TYPE',  // 错误类型枚举（string）
  message: '错误信息'   // 错误信息（string）
}
```

## 示例

{% if use_react_scaffold -%}
> **提示**：以下示例仅展示基础的 API 调用方法。在实际项目中，你可以根据需求添加更多功能，例如：
> - 使用 `useState` 管理加载状态（`isLoading`）、结果数据（`musicResult`）和错误信息（`error`）
> - 添加表单输入组件，让用户自定义 `prompt`、`lyrics`、`duration` 等参数
> - 使用 `useRef` 管理 `<audio>` 元素引用，实现播放/暂停控制、进度显示等功能
> - 添加 UI 反馈，如加载动画、进度条、错误提示等
> - 根据用户需求，按需实现音乐列表管理、历史记录、收藏等高级功能

**示例 1：生成纯音乐（React 版本）**

```tsx
function MusicGenerator() {
  const handleGenerate = async () => {
    try {
      const result = await window.lingguang.ai.musicGeneration({
        prompt: 'pop, electronic, cheerful and festive with bright synths and steady beat'
      });
      
      console.log('生成的音乐URL:', result.url);
      console.log('音乐时长:', result.duration, '秒');
      // 使用 <audio> 标签播放，设置 loop 属性实现循环播放
      // <audio src={result.url} controls loop />
    } catch (error: unknown) {
      // ⚠️ 必须告知用户：不能只打 console
      const msg = error instanceof Error ? error.message : '音乐生成失败，请稍后重试';
      showError(msg); // showError：请按当前项目的提示风格实现，禁止直接console.error
    }
  };

  return <button onClick={handleGenerate}>生成纯音乐</button>;
}
```

**示例 2：生成带歌词的歌曲（React 版本）**

```tsx
function SongGenerator() {
  const handleGenerate = async () => {
    try {
      const result = await window.lingguang.ai.musicGeneration({
        prompt: 'pop, electronic, cheerful and festive with bright synths and steady beat',
        lyrics: '[intro]\n[verse]\n夜色深处有道光在轻轻闪\n一步一印走过沉默的荒原\n[chorus]\n我要追那梦 不怕路遥远\n[outro]',
        duration: 130
      });
      
      console.log('生成的音乐URL:', result.url);
      console.log('音乐时长:', result.duration, '秒');
      // 使用 <audio> 标签播放，设置 loop 属性实现循环播放
      // <audio src={result.url} controls loop />
    } catch (error: unknown) {
      // ⚠️ 必须告知用户：不能只打 console
      const msg = error instanceof Error ? error.message : '音乐生成失败，请稍后重试';
      showError(msg); // showError：请按当前项目的提示风格实现，禁止直接console.error
    }
  };

  return <button onClick={handleGenerate}>生成歌曲</button>;
}
```
{% else -%}
**示例 1：生成纯音乐**

```javascript
try {
  const result = await window.lingguang.ai.musicGeneration({
    prompt: 'pop, electronic, cheerful and festive with bright synths and steady beat'
  });
  
  console.log('生成的音乐URL:', result.url);
  console.log('音乐时长:', result.duration, '秒');
  // 可以在 HTML 中使用 <audio> 标签播放，设置 loop 属性实现循环播放
  const audio = document.createElement('audio');
  audio.src = result.url;
  audio.controls = true;
  audio.loop = true;  // 循环播放，适合30秒以内的短音乐
  document.body.appendChild(audio);
} catch (error) {
  // ⚠️ 必须告知用户：不能只打 console
  const msg = error?.message || '音乐生成失败，请稍后重试';
  showError(msg); // showError：请按当前项目的提示风格实现，禁止直接console.error
}
```

**示例 2：生成带歌词的歌曲**

```javascript
try {
  const result = await window.lingguang.ai.musicGeneration({
    prompt: 'pop, electronic, cheerful and festive with bright synths and steady beat',
    lyrics: '[intro]\\n[verse]\\n夜色深处有道光在轻轻闪\\n一步一印走过沉默的荒原\\n[chorus]\\n我要追那梦 不怕路遥远\\n[outro]',
    duration: 130
  });
  
  console.log('生成的音乐URL:', result.url);
  console.log('音乐时长:', result.duration, '秒');
  // 可以在 HTML 中使用 <audio> 标签播放，设置 loop 属性实现循环播放
  const audio = document.createElement('audio');
  audio.src = result.url;
  audio.controls = true;
  audio.loop = true;  // 循环播放，适合30秒以内的短音乐
  document.body.appendChild(audio);
} catch (error) {
  // ⚠️ 必须告知用户：不能只打 console
  const msg = error?.message || '音乐生成失败，请稍后重试';
  showError(msg); // showError：请按当前项目的提示风格实现，禁止直接console.error
}
```
{% endif -%}

## 注意事项

1. **参数要求**：
   - `prompt` 参数为必填，用于描述要生成的音乐风格（英文）
   - `lyrics` 参数可选，空字符串表示生成纯音乐
   - `duration` 参数可选，范围 30-240 秒，**建议设置为 30 秒左右**（生成耗时较短，用户体验更好；如需长时间播放，可通过 `<audio>` 标签的 `loop` 属性实现循环播放）
   - 歌曲时长需要和歌词长度匹配，建议 16 句歌词对应时长 130s（但请注意，过长的音乐会导致生成耗时显著增加）

2. **返回值说明**：
   - 成功时返回包含音乐 URL 和时长的对象
   - `url` 字段：生成音乐的 URL，可用于直接播放
   - `duration` 字段：实际生成音乐时长（秒），可用于显示进度条、倒计时等
   - 音乐生成可能需要一定时间，请确保有适当的加载提示
   - 返回的 URL 可以直接在 HTML 中使用 `<audio>` 标签播放

3. **使用限制**：
   - 音乐生成可能需要消耗一定的资源，请根据实际需求合理使用
   - 建议在用户明确需要时才调用此 API

4. **与生成阶段 `asset.music_generate` 能力的区别**：
   - `musicGeneration` API：应用运行时由用户主动触发，实时生成音乐（JS API）
   - `asset.music_generate`：应用生成阶段预置音乐资源，通过 `CapabilityCall` 生成 mp3 URL，适合直接在应用内 `<audio>` 播放（可配合 `loop` 循环）
   - 用户想在应用内主动触发生成：用 `musicGeneration` API；只需要应用自带背景音乐/歌曲：用 `asset.music_generate`

5. **时长建议**：
   - 建议 `duration` 参数设置为 30 秒左右
   - 30 秒以内的音乐生成耗时较短，用户体验更好
   - 如果音乐时长较短，可以通过设置 `<audio>` 标签的 `loop` 属性实现循环播放，满足长时间播放需求
   - 过长的音乐（如 60 秒以上）会导致生成耗时显著增加，可能影响用户体验

## 生成阶段预置示例（CapabilityCall）

当你需要“内置两首可播放音乐”时，按以下流程：

1) 先取帮助（可选，但推荐）：

```text
CapabilityCall(mode="help", capability="asset.music_generate", arguments={})
```

2) 再分别生成两首（建议 duration=30，播放时 loop）：

```text
CapabilityCall(mode="invoke", capability="asset.music_generate", arguments={"prompt":"uplifting pop, bright synths, steady beat","lyrics":"","duration":30})
CapabilityCall(mode="invoke", capability="asset.music_generate", arguments={"prompt":"lofi chill, warm vinyl, relaxed tempo","lyrics":"","duration":30})
```

把返回的 `data.url` 写进代码（例如 `const PRESET_TRACKS = [{title,url,...}, ...]`），播放器即可做到“打开就能播”。
