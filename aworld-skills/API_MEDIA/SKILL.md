---
description: 灵光小程序 API：API_MEDIA。与 PRD「所需 API 技能」对齐后按需激活。媒体能力
---

# 强制规范（容器/嵌入式 H5 环境）

- 选择/拍照后返回的是本地/容器路径，不能直接用于 `<img src>`。
- 必须无条件调用 `window.lingguang.uploadImage({ filePath })` 换取可访问的 `url`，再用于渲染与后续处理（不要做基于前缀的分支判断）。
- 推荐选择参数：`chooseImage({ count: 1, sourceType: ['album','camera'], sizeType: ['original'] })`。
- 禁止保留 `<input type="file">` 回退，禁止使用 `URL.createObjectURL()`、`blob:` URL、`FileReader` 等浏览器原生路径/URL 直接渲染。
- 严禁通用浏览器上传链路（拖拽上传、`URL.createObjectURL`、`blob:`、`FileReader`）；必须走 `chooseImage/takePhoto -> uploadImage`。
- 保存到相册时，`saveImageToPhotosAlbum({ filePath })` 的 `filePath` 应为可访问 URL 或 Data URL（如 `canvas.toDataURL()`），不要传 `blob:` 或未上传的本地路径。

# 统一“上传换URL”范式（示例）

```ts
const handleImageSelected = useCallback(async (filePath: string) => {
  try {
    const { url } = await window.lingguang.uploadImage({
      filePath,
      compress: 'none', // 或按需：'medium'
    });
    setImageUrl(url);
    setStep('select');
  } catch (e) {
    console.error('上传失败:', e);
  }
}, []);
```

# window.lingguang.chooseImage

打开手机相册，允许用户选择一张或多张图片。

## 函数签名

```typescript
window.lingguang.chooseImage(options?: {
  count?: number;
  sourceType?: ('album' | 'camera')[];
  sizeType?: ('original' | 'compressed')[];
}): Promise<{
  tempFiles: Array<{
    path: string;
    size: number;
  }>;
}>
```

## 参数

**options** (Object): 配置对象

- **count** (number, 可选): 最多选择的图片数量，范围 1-9，默认值为 1
- **sourceType** (Array.<string>, 可选): 图片来源，默认值为 `['album', 'camera']`
  - `'album'`: 相册
  - `'camera'`: 相机
  - 可单独指定一个或两个都传。两个都传时让用户自行选择图片是拍摄的还是相册选择的
- **sizeType** (Array.<string>, 可选): 所选的图片的尺寸，默认值为 `['original', 'compressed']`
  - `'original'`: 原图
  - `'compressed'`: 压缩图

## 返回值

返回 Promise，成功时 resolve，失败时 reject。

**成功时（resolve）返回：**

```javascript
{
  tempFiles: [
    {
      path: 'file:///path/to/image1.jpg',  // 本地文件路径（仅用于原生端，不能直接用于 Web）
      size: 1024000  // 文件大小（字节）
    },
    // ... 更多图片
  ]
}
```

**失败时（reject）返回：**

```javascript
{
  name: 'USER_CANCEL',  // 错误类型枚举（string）
  message: '用户取消了选择'  // 错误信息（string）
}
```

## 示例

```javascript
try {
  const result = await window.lingguang.chooseImage(options);
  console.log('选择的图片:', result);
} catch (error) {
  console.log('选择失败:', error.message);
}
```

## 示例 从相册选择图片显示到页面（统一上传换URL）
```javascript
try {
  // 先选择图片
  const chooseResult = await window.lingguang.chooseImage({
    count: 1,
    sourceType: ['album', 'camera'],
    sizeType: ['original']
  });

  const imagePath = chooseResult.tempFiles[0].path;
  
  // 上传图片
  const uploadResult = await window.lingguang.uploadImage({
    filePath: imagePath  // 使用本地路径
  });

  console.log('上传成功，图片 URL可用于<img src>:', uploadResult.url);
} catch (error) {
  console.log('上传失败:', error.message);
}
```

## 注意事项

1. **图片路径的使用**：
   - `path` 字段返回的是原生文件系统路径，不能直接用于 `<img src>` 中；必须先 `uploadImage` 换 URL 后再回显
   - `path` 主要用于原生端文件操作（上传）、传递给其他原生 API、调试和日志记录

2. 选择图片数量限制为 1-9 张

3. 避免图片通过canvas加工后出现 `Canvas 污染（Tainted Canvas）错误`
  - 当你从相册选择图片并上传图片(uploadImage)获取到URL，并准备通过canvas处理图片时
  - 使用 new Image() 创建图片对象
  - 设置 crossOrigin = 'anonymous'
  - 在 onload 回调中绘制到 canvas
  - 然后调用 toDataURL()
  - 这种方式可避免Canvas 污染（Tainted Canvas）错误

# window.lingguang.takePhoto

打开手机相机，允许用户拍摄照片。

## 函数签名

```typescript
window.lingguang.takePhoto(options?: {
  sizeType?: ('original' | 'compressed')[];
}): Promise<{
  tempFiles: Array<{
    path: string;
    size: number;
  }>;
}>
```

## 参数

**options** (Object): 配置对象

- **sizeType** (Array.<string>, 可选): 所选的图片的尺寸，默认值为 `['original', 'compressed']`
  - `'original'`: 原图
  - `'compressed'`: 压缩图

## 返回值

返回 Promise，成功时 resolve，失败时 reject。

**成功时（resolve）返回：**

```javascript
{
  tempFiles: [
    {
      path: 'file:///path/to/photo.jpg',  // 本地文件路径（仅用于原生端，不能直接用于 Web）
      size: 1024000  // 文件大小（字节）
    }
  ]
}
```

**失败时（reject）返回：**

```javascript
{
  name: 'USER_CANCEL',  // 错误类型枚举（string）
  message: '用户取消了拍摄'  // 错误信息（string）
}
```

## 示例

```javascript
try {
  const result = await window.lingguang.takePhoto(options);
  console.log('拍摄的照片:', result);
} catch (error) {
  console.log('拍摄失败:', error.message);
}
```

## 注意事项

1. **图片路径的使用**：
   - `path` 字段返回的是原生文件系统路径，不能直接用于 `<img src>` 中
      - 如需回显选择的图片到页面，请使用`window.lingguang.uploadImage` 上传获取URL之后回显
   - `path` 主要用于原生端文件操作（上传）、传递给其他原生 API、调试和日志记录

2. 需要相机权限，用户可能会拒绝权限请求

# window.lingguang.saveImageToPhotosAlbum

将H5应用中的图片保存到手机系统相册。支持 Base64 格式、本地文件路径或网络 URL。

## 函数签名

```typescript
window.lingguang.saveImageToPhotosAlbum(options: {
  filePath: string;
}): Promise<void>
```

## 参数

**options** (Object): 配置对象

- **filePath** (string, 必填): 图片数据，支持以下格式：
  - Base64: `data:image/jpeg;base64,...`（完整的 Data URL，推荐）
  - 本地文件路径: `file:///path/to/image.jpg`（由 chooseImage/takePhoto 返回的 tempFiles[].path）
  - 网络 URL: `http://...` 或 `https://...`

## 返回值

返回 Promise，成功时 resolve，失败时 reject。

**成功时（resolve）返回：**

```javascript
undefined  // 成功时不返回任何内容，没有异常即表示成功
```

**失败时（reject）返回：**

```javascript
{
  name: 'PERMISSION_DENIED',  // 错误类型枚举（string）
  message: '权限被拒绝'  // 错误信息（string）
}
```

## 示例

**示例 1：保存 Base64 图片**

```javascript
// 从 canvas 获取 base64
const canvas = document.getElementById('myCanvas');
const base64 = canvas.toDataURL('image/jpeg', 0.9);

try {
  await window.lingguang.saveImageToPhotosAlbum({
    filePath: base64
  });
  console.log('图片已保存到相册');
} catch (error) {
  console.log('保存失败:', error.message);
}
```

**示例 2：保存从 chooseImage/takePhoto 获取的图片**

```javascript
try {
  // 从相册选择图片
  const chooseResult = await window.lingguang.chooseImage({ count: 1 });
  const imagePath = chooseResult.tempFiles[0].path;
  
  // 保存到相册
  await window.lingguang.saveImageToPhotosAlbum({
    filePath: imagePath
  });
  console.log('保存成功');
} catch (error) {
  console.log('保存失败:', error.message);
}
```

**示例 3：保存网络图片**

```javascript
try {
  await window.lingguang.saveImageToPhotosAlbum({
    filePath: 'https://example.com/photo.jpg'
  });
  console.log('保存成功');
} catch (error) {
  console.log('保存失败:', error.message);
}
```

## 注意事项

1. 需要相册写入权限，用户可能会拒绝权限请求

2. **filePath 参数格式**：
   - Base64 格式必须是完整的 Data URL（如 `data:image/jpeg;base64,/9j/4AAQ...`）
   - 本地文件路径可以使用 `chooseImage` 或 `takePhoto` 返回的 `path`
   - 网络 URL 会先下载图片再保存到相册

# window.lingguang.uploadImage

将图片上传到服务器。支持多种图片来源（本地路径、Base64、URL）。

## 函数签名

```typescript
window.lingguang.uploadImage(options: {
  filePath: string;
  compress?: 'low' | 'medium' | 'high' | 'none' | 'auto';
}): Promise<{
  url: string;
}>
```

## 参数

**options** (Object): 配置对象

- **filePath** (string, 必填): 图片数据，支持以下格式：
  - 本地路径: `file:///path/to/image.jpg`（由 chooseImage/takePhoto 返回的 path，推荐）
  - Base64: `data:image/jpeg;base64,...`（完整的 Data URL）
  - URL: `http://...` 或 `https://...`（网络图片 URL，会先下载再上传）
- **compress** (string, 可选): 压缩级别，默认值为 `'auto'`
  - `'low'`: 低质量（文件较小，质量较低）
  - `'medium'`: 中质量（平衡文件大小和质量）
  - `'high'`: 高质量（文件较大，质量较高）
  - `'none'`: 不压缩（保持原图）
  - `'auto'`: 自动选择（默认值）

## 返回值

返回 Promise，成功时 resolve，失败时 reject。

**成功时（resolve）返回：**

```javascript
{
  url: 'https://example.com/uploads/image.jpg'  // 服务器返回的图片 URL
}
```

**失败时（reject）返回：**

```javascript
{
  name: 'NETWORK_ERROR',  // 错误类型枚举（string）
  message: '网络错误'  // 错误信息（string）
}
```

## 示例

**示例 1：上传本地图片（推荐）**

```javascript
try {
  // 先选择图片
  const chooseResult = await window.lingguang.chooseImage({
    count: 1,
    sourceType: ['album']
  });

  const imagePath = chooseResult.tempFiles[0].path;
  
  // 上传图片
  const uploadResult = await window.lingguang.uploadImage({
    filePath: imagePath  // 使用本地路径
  });

  console.log('上传成功，图片 URL:', uploadResult.url);
} catch (error) {
  console.log('上传失败:', error.message);
}
```

**示例 2：上传 Base64 图片**

```javascript
const base64Image = 'data:image/jpeg;base64,/9j/4AAQSkZJRg==...';

try {
  const result = await window.lingguang.uploadImage({
    filePath: base64Image
  });
  console.log('上传成功:', result.url);
} catch (error) {
  console.log('上传失败:', error.message);
}
```

## 注意事项

1. **filePath 参数格式**：
   - 本地路径（推荐）：使用 `chooseImage` 或 `takePhoto` 返回的 `path`
   - Base64 格式必须是完整的 Data URL（如 `data:image/jpeg;base64,...`）

2. **压缩参数**：
   - `compress` 参数控制上传前图片的压缩程度
   - 默认值为 `'auto'`，系统会自动选择合适的压缩级别
   - 如果图片已经很小或需要保持原图质量，可以使用 `'none'` 或 `'high'`
