---
description: 灵光小程序 API：API_FS。与 PRD「所需 API 技能」对齐后按需激活。文件读写
---

# window.lingguang.chooseFile

打开文件选择器，允许用户从设备中选择一个文件。

## 函数签名

```typescript
window.lingguang.chooseFile(options?: {}): Promise<{
  filePath: string;
  fileName: string;
}>
```

## 参数

**options** (Object, 可选): 配置对象，当前为空对象，保留用于未来扩展

## 返回值

返回 Promise，成功时 resolve，失败时 reject。

**成功时（resolve）返回：**

```javascript
{
  filePath: 'file:///path/to/file.pdf',  // 本地文件路径（仅用于原生端，不能直接用于 Web）
  fileName: 'document.pdf'  // 文件名（string）
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
  const result = await window.lingguang.chooseFile();
  console.log('选择的文件:', result.fileName);
  console.log('文件路径:', result.filePath);
} catch (error) {
  console.log('选择失败:', error.message);
}
```

## 示例 选择文件并上传

```javascript
try {
  // 先选择文件
  const chooseResult = await window.lingguang.chooseFile();
  
  // 上传文件
  const uploadResult = await window.lingguang.uploadFile({
    filePath: chooseResult.filePath
  });
  
  console.log('上传成功，文件 ID:', uploadResult.fileId);
} catch (error) {
  console.log('操作失败:', error.message);
}
```

## 注意事项

1. **文件路径的使用**：
   - `filePath` 字段返回的是原生文件系统路径，不能直接用于 Web 端
   - `filePath` 主要用于原生端文件操作（上传）、传递给其他原生 API、调试和日志记录

2. 用户可能会取消文件选择，需要妥善处理错误情况

# window.lingguang.uploadFile

将文件上传到服务器。支持本地文件路径。

## 函数签名

```typescript
window.lingguang.uploadFile(options: {
  filePath: string;
}): Promise<{
  fileId: string;
}>
```

## 参数

**options** (Object): 配置对象

- **filePath** (string, 必填): 文件路径，通常由 `chooseFile` 返回的 `filePath`

## 返回值

返回 Promise，成功时 resolve，失败时 reject。

**成功时（resolve）返回：**

```javascript
{
  fileId: 'file_1234567890'  // 服务器返回的文件 ID（string）
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

```javascript
try {
  // 先选择文件
  const chooseResult = await window.lingguang.chooseFile();
  
  // 上传文件
  const uploadResult = await window.lingguang.uploadFile({
    filePath: chooseResult.filePath
  });
  
  console.log('上传成功，文件 ID:', uploadResult.fileId);
} catch (error) {
  console.log('上传失败:', error.message);
}
```

## 注意事项

1. **filePath 参数**：
   - 通常使用 `chooseFile` 返回的 `filePath`
   - 确保文件路径有效且文件存在

2. 上传过程可能需要一些时间，特别是大文件，建议提供加载提示

# window.lingguang.saveFile

将文件保存到设备本地。支持 URL 或 Base64 格式的数据。调用此 API 时，系统会弹出文件保存对话框，用户可以选择保存路径并自定义文件名。

## 函数签名

```typescript
window.lingguang.saveFile(options: {
  data: string;
}): Promise<{
  success: boolean;
}>
```

## 参数

**options** (Object): 配置对象

- **data** (string, 必填): 文件数据，支持以下格式：
  - URL: `http://...` 或 `https://...`（网络文件 URL，会先下载再保存）
  - Base64: 纯 Base64 编码字符串（如 `JVBERi0xLjQKJeLjz9MKMy...`）

## 返回值

返回 Promise，成功时 resolve，失败时 reject。

**成功时（resolve）返回：**

```javascript
{
  success: true  // 保存成功标识（boolean）
}
```

**失败时（reject）返回：**

```javascript
{
  name: 'PERMISSION_DENIED',  // 错误类型枚举（string）
  message: '权限被拒绝'  // 错误信息（string）
}
```

## 示例

**示例 1：保存网络文件**

```javascript
try {
  const result = await window.lingguang.saveFile({
    data: 'https://example.com/document.pdf'
  });
  
  if (result.success) {
    console.log('文件已保存到本地');
  }
} catch (error) {
  console.log('保存失败:', error.message);
}
```

**示例 2：保存 Base64 文件**

```javascript
// 假设有一个 Base64 编码的文件数据（纯 Base64 字符串）
const base64File = 'JVBERi0xLjQKJeLjz9MKMy...';

try {
  const result = await window.lingguang.saveFile({
    data: base64File
  });
  
  if (result.success) {
    console.log('文件已保存到本地');
  }
} catch (error) {
  console.log('保存失败:', error.message);
}
```

**示例 3：从上传的文件 ID 下载并保存**

```javascript
try {
  // 假设已经上传了文件并获得了 fileId
  const fileId = 'file_1234567890';
  const fileUrl = `https://example.com/files/${fileId}`;
  
  // 保存文件到本地
  const result = await window.lingguang.saveFile({
    data: fileUrl
  });
  
  if (result.success) {
    console.log('文件已保存到本地');
  }
} catch (error) {
  console.log('保存失败:', error.message);
}
```

## 注意事项

1. **data 参数格式**：
   - URL 格式：必须是完整的 HTTP/HTTPS URL
   - Base64 格式：纯 Base64 编码字符串（不包含 Data URL 前缀，如 `JVBERi0xLjQKJeLjz9MKMy...`）

2. 需要文件写入权限，用户可能会拒绝权限请求

3. 网络 URL 会先下载文件再保存，大文件可能需要较长时间

# window.lingguang.readFile

读取本地文件内容。支持多种编码格式，可以读取文本文件或二进制文件。

## 函数签名

```typescript
window.lingguang.readFile(options: {
  filePath: string;
  encoding: string;
}): Promise<{
  data?: string | ArrayBuffer;
}>
```

## 参数

**options** (Object): 配置对象

- **filePath** (string, 必填): 文件路径，通常由 `chooseFile` 返回的 `filePath`
- **encoding** (string, 必填): 编码格式，支持以下值：
  - `'utf8'`: UTF-8 编码（默认，推荐用于文本文件）
  - `'ascii'`: ASCII 编码
  - `'base64'`: Base64 编码（推荐用于二进制文件，如图片、音频、视频、Excel、Word、PDF等）

## 返回值

返回 Promise，成功时 resolve，失败时 reject。

**成功时（resolve）返回：**

```javascript
{
  data: '文件内容'  // 文件数据（string | ArrayBuffer，根据编码格式决定）
}
```

**失败时（reject）返回：**

```javascript
{
  name: 'FILE_NOT_FOUND',  // 错误类型枚举（string）
  message: '文件不存在'  // 错误信息（string）
}
```

## 示例

**示例 1：读取文本文件（UTF-8）**

```javascript
try {
  // 先选择文件
  const chooseResult = await window.lingguang.chooseFile();
  
  // 读取文件内容（假设是文本文件）
  const readResult = await window.lingguang.readFile({
    filePath: chooseResult.filePath,
    encoding: 'utf8'
  });
  
  console.log('文件内容:', readResult.data);
} catch (error) {
  console.log('读取失败:', error.message);
}
```

**示例 2：读取二进制文件（Base64）**

```javascript
try {
  const chooseResult = await window.lingguang.chooseFile();
  
  // 以 Base64 格式读取文件
  const readResult = await window.lingguang.readFile({
    filePath: chooseResult.filePath,
    encoding: 'base64'
  });
  
  console.log('Base64 内容:', readResult.data);
} catch (error) {
  console.log('读取失败:', error.message);
}
```

## 注意事项

1. **filePath 参数**：
   - 通常使用 `chooseFile` 返回的 `filePath`
   - 确保文件路径有效且文件存在

2. **encoding 参数**：
   - 文本文件建议使用 `'utf8'` 编码（默认）
   - 二进制文件（如图片、音频、视频、Excel、Word、PDF等）建议使用 `'base64'` 编码
   - 根据文件类型选择合适的编码格式

3. **返回值类型**：
   - 当 encoding 为 `'utf8'` 或 `'ascii'` 时，`data` 为 `string` 类型
   - 当 encoding 为 `'base64'` 时，`data` 为 `string` 类型（Base64 编码的字符串）

4. 读取大文件时可能需要较长时间，建议提供加载提示
