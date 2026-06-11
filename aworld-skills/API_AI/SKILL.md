---
description: 灵光小程序 API：API_AI。与 PRD「所需 API 技能」对齐后按需激活。AI能力
---

# window.lingguang.ai.imageGeneration

AI图像生成能力，根据文本描述生成指定尺寸的图像。

## 函数签名

```typescript
window.lingguang.ai.imageGeneration(params: {
  query: string;
  width: number;
  height: number;
}): Promise<{
  url: string;
}>
```

## 参数

**params** (Object): 请求参数对象

- **query** (string, 必需): 图像生成的文本描述
- **width** (number, 必需): 生成图像的宽度（像素）
- **height** (number, 必需): 生成图像的高度（像素）

## 返回值

返回 Promise，成功时 resolve，失败时 reject。

**成功时（resolve）返回：**

```javascript
{
  url: 'https://example.com/generated-image.jpg'  // 生成图像的URL（string）
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

**示例 1：生成AI图像**

```javascript
try {
  const result = await window.lingguang.ai.imageGeneration({
    query: '一只可爱的小猫坐在窗台上',
    width: 1024,
    height: 1024
  });
  
  console.log('生成的图像URL:', result.url);
} catch (error) {
  console.log('生成图像失败:', error.message);
}
```

## 注意事项

1. **参数要求**：
   - `query` 参数为必填，用于描述要生成的图像内容
   - `width` 和 `height` 必须为正整数，建议使用常见的图像尺寸（如 512、1024、2048 等）

2. **返回值说明**：
   - 成功时返回包含图像 URL 的对象，URL 可用于直接显示或下载图像
   - 图像生成可能需要一定时间，请确保有适当的加载提示

3. **使用限制**：
   - 图像生成可能需要消耗一定的资源，请根据实际需求合理使用

---

# window.lingguang.ai.vllm

多模态理解能力的大语言模型，主要用于图像理解，支持图像和文本的混合输入。

## 函数签名

```typescript
window.lingguang.ai.vllm(params: {
  content: Array<{
    type: 'image' | 'text';
    content: string;
  }>;
}): Promise<{
  content: string;
}>
```

## 参数

**params** (Object): 请求参数对象

  - **content** (Array, 必需): 内容数组，可包含图像和文本
  - **type** (string, 必需): 内容类型，可选值为 `'image'` 或 `'text'`
  - **content** (string, 必需): 内容值
    - 当 `type` 为 `'image'` 时，为图像的 URL 地址（如：`https://example.com/image.jpg`）
    - 当 `type` 为 `'text'` 时，为文本内容

## 返回值

返回 Promise，成功时 resolve，失败时 reject。

**成功时（resolve）返回：**

```javascript
{
  content: '这是一张图片的描述内容...'  // 模型返回的文本内容（string）
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

**示例 1：理解图像内容**

```javascript
try {
  const result = await window.lingguang.ai.vllm({
    content: [
      {
        type: 'image',
        content: 'https://example.com/image.jpg'
      },
      {
        type: 'text',
        content: '请描述这个图片'
      }
    ]
  });
  
  console.log('图像理解结果:', result.content);
} catch (error) {
  console.log('图像理解失败:', error.message);
}
```

## 注意事项

1. **参数要求**：
   - `content` 数组必须至少包含一个元素
   - 每个元素必须包含 `type` 和 `content` 字段
   - 当 `type` 为 `'image'` 时，`content` 字段应为图像的 URL 地址
   - 当 `type` 为 `'text'` 时，`content` 字段应为文本内容
   - 图像 URL 必须是可访问的有效地址（如：`https://example.com/image.jpg`）

2. **返回值说明**：
   - 成功时返回包含 `content` 字段的对象，其中包含模型对输入内容的理解结果
   - 返回的文本内容为模型生成的描述或回答

3. **使用限制**：
   - 图像大小和分辨率可能影响处理速度和结果质量
   - 多模态理解可能需要一定处理时间，建议添加适当的加载提示
