---
description: 灵光小程序 API：API_HTML2CANVAS。与 PRD「所需 API 技能」对齐后按需激活。html2canvas
---

# html-to-image

当前项目中，截图导出能力统一建议使用 `html-to-image`。

原因：在 `iframe sandbox="allow-scripts"` 且缺少 `allow-same-origin` 的环境下，`html2canvas` 常见实现路径会触发跨域访问限制，导致截图失败。`html-to-image` 更适合当前运行环境。

## 引入方式

```typescript
import { toPng, toJpeg } from 'html-to-image';
```

## 常用 API

```typescript
toPng(node: HTMLElement, options?): Promise<string>      // 返回 data:image/png;base64,...
toJpeg(node: HTMLElement, options?): Promise<string>     // 返回 data:image/jpeg;base64,...
```

## 常用参数（options）

- `backgroundColor`: 导出背景色
- `pixelRatio`: 导出分辨率倍率（建议 2）
- `quality`: JPEG 质量（0-1，仅 `toJpeg` 有效）
- `cacheBust`: 是否为资源 URL 附加缓存破坏参数（建议 `true`）
- `skipFonts`: 跳过字体内联（跨域字体问题时可兜底）
- `filter`: 过滤不需要导出的 DOM 节点

## 示例 1：导出 PNG 并保存到系统相册（推荐）

```typescript
import { toPng } from 'html-to-image';

async function saveElementToAlbum(node: HTMLElement) {
  if (document.fonts && document.fonts.ready) {
    await document.fonts.ready;
  }

  const dataUrl = await toPng(node, {
    backgroundColor: '#ffffff',
    pixelRatio: 2,
    cacheBust: true,
    filter: (domNode) => {
      const tag = domNode.tagName?.toUpperCase?.();
      return tag !== 'IFRAME' && tag !== 'FRAME';
    },
  });

  await window.lingguang.saveImageToPhotosAlbum({
    filePath: dataUrl,
  });
}
```
