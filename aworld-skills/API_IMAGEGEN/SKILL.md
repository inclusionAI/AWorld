---
description: 灵光小程序 API：API_IMAGEGEN。与 PRD「所需 API 技能」对齐后按需激活。AI图像生成
---

# window.lingguang.ai.imageGeneration

AI图像生成能力，根据文本描述生成指定尺寸的图像。

## 重要：运行时生成 vs 生成阶段预置

- 如果需求是“用户在应用里点按钮生成图片/插画/封面”，使用本页的 `window.lingguang.ai.imageGeneration`（运行时 JS 接口）。
- 如果需求是“应用一打开就要有默认头像/默认封面/默认插画（内置素材）”，且用户没有提供现成 URL，你必须在写代码前先用 `CapabilityCall(mode="invoke", capability="asset.image_generate", arguments={...})` 在生成阶段生成图片 URL，然后把 URL 写进代码作为默认资源。
- 不要用“示例图片/占位图/预置图库”去替代“内置素材”的要求，除非需求明确允许。
- 两者可以同时存在：默认资源用 CapabilityCall 预生成；用户交互生成用运行时 JS 接口。

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
  showError(error?.message || '图像生成失败，请稍后重试'); // showError：请按当前项目的提示风格实现，禁止直接console.error
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

4. **与生成阶段 `asset.image_generate` 能力的区别**：
   - `imageGeneration` API：应用运行时由用户主动触发的 JS 接口（IMAGEGEN skill）
   - `asset.image_generate`：应用生成阶段预置图片资源，通过 `CapabilityCall` 生成图片 URL，适合将返回的 URL 直接写入应用代码进行展示

## 生成阶段预置示例（CapabilityCall）

当你需要“默认头像/默认封面/默认插画”时，按以下流程：

1) 先取帮助（可选，但推荐）：

```text
CapabilityCall(mode="help", capability="asset.image_generate", arguments={})
```

2) 再执行生成：

```text
CapabilityCall(mode="invoke", capability="asset.image_generate", arguments={"prompt":"奥特曼风格头像，正面半身，扁平插画，透明背景","width":512,"height":512})
```

返回的 `data.url` 写进代码（例如 `const DEFAULT_AVATAR_URL = "..."`）。
