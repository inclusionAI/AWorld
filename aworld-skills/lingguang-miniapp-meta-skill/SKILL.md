---
description: 灵光小程序开发元规范 - 统一的标准规范、注意事项、技术约束与最高优先级规则。给定 PRD 后，遵循本规范开发符合标准的 H5 小程序（标准 workspace 文件夹结构）。
---

# 灵光小程序 · 开发元规范（Meta Skill）

> **适用场景**：当你拿到一个 PRD 文档，需要开发一个符合灵光规范的 H5 小程序时，请完整阅读本文档。
> 
> **交付物**：标准化的 workspace 文件夹结构，包含：
> - `App.tsx`（主组件代码）
> - `App.css`（样式文件）
> - `assets/`（图片等附件）
> - `data/`（小程序所需的必要数据）

---

## 📋 目录

1. [基础技术规范](#1-基础技术规范)
2. [构建硬约束（违反将导致构建失败）](#2-构建硬约束)
3. [开发规范与注意事项](#3-开发规范与注意事项)
4. [API 使用说明](#4-api-使用说明)
5. [需求简化与降级](#5-需求简化与降级)
6. [技术栈补充规则](#6-技术栈补充规则)
7. [数据库相关规范](#7-数据库相关规范)
8. [图片保存排障指南](#8-图片保存排障指南)
9. [React Scaffold 初始文件](#9-react-scaffold-初始文件)

---

## 1. 基础技术规范

### 1.1 角色定位
你是一个优秀的 WEB 轻应用设计和开发工程师，帮助用户完成移动端 H5 应用的开发。

### 1.2 技术栈
- **React 18 + TypeScript**
- **Vite 7**
- **Tailwind CSS 3.4**

### 1.3 项目结构
```
workspace/
├── App.tsx       # 主组件（核心业务代码）
├── App.css       # 样式文件
├── assets/       # 图片等静态资源
└── data/         # 小程序所需的数据文件（如 JSON、TS 数据）
```

### 1.4 编写原则
- 运用**创意编程思维**，结合动画、交互效果和视觉设计，打造富有创意和表现力的轻应用
- 你开发的 H5 轻应用会通过 **iframe 嵌入到移动端 APP**
- 优先使用 **Tailwind CSS** 的工具类（utility classes）实现样式，仅在 Tailwind 无法满足需求时再使用自定义 CSS
- 可以使用 **Material Icons**，加强显示效果

---

## 2. 构建硬约束（违反将导致构建失败）

> ⚠️ **以下规则由构建流程的 Vite 插件强制校验，违反任何一条都会导致构建失败，应用无法部署**

### 2.1 必须保留 `<div id="safe-area"></div>`
- 在 `<div id="container">` 内部必须存在一个 `<div id="safe-area"></div>` 空节点
- **不要删除、重命名或在其内部添加内容**
- 这是系统预留节点，须与 §2.6 / §2.6.1 的安全区 CSS 配置一并保留
- 示例：
  ```tsx
  <div id="container">
    <div id="safe-area"></div>
    {/* 你的业务代码放在这里 */}
  </div>
  ```

### 2.2 禁止使用原生音频 API
- **禁止使用**：`AudioContext`、`webkitAudioContext`、`OfflineAudioContext`、`new Audio()`、`HTMLAudioElement`、`<audio>` 标签、`document.createElement('audio')`
- **禁止在业务代码中直接使用**：`new AudioContext2()`（运行时虽提供该能力，但 Vite 构建插件会拦截 `workspace/` 中的直接调用）
- **禁止直接导入**：`import ... from 'howler'`、`import ... from 'tone'`、`require('howler')`、`import('howler')` 等绕过封装层的写法
- **必须使用**：
  - 播放音频文件（音效、背景音乐等）：从 `@/lib/audio` 导入 `Howl` / `Howler`（基于 howler.js）
  - 合成/乐器类程序化音效：从 `@/lib/tone` 导入 Tone.js 高层能力（如 `Synth`、`Sampler`、`Transport`）
- **Tone.js 额外构建限制**（`block-audio-apis` 插件强制）：
  - 禁止从 `@/lib/tone` 导入底层上下文能力：`getContext`、`setContext`、`context`、`Context`、`OfflineContext`、`rawContext`
  - 禁止业务代码直接修改 Tone 总输出静音状态（须由宿主 `window.app.mute/unmute` 统一管控）
- **运行时能力说明**：宿主环境提供 `AudioContext2` 用于振荡器、增益节点等（见 §4.3），但业务 `workspace/` 代码不得直接 `new AudioContext2()`，应改用上述 wrapper

### 2.3 禁止使用 `<input type="file">`
- 文件选择请使用 `window.lingguang.chooseFile`
- 构建插件会拦截以下等价写法，均视为违规：
  - JSX：`<input type="file" />`
  - 动态创建：`document.createElement('input')` 后将 `type` 设为 `'file'`
  - `input.setAttribute('type', 'file')` / `setAttributeNS(..., 'type', 'file')`
  - `Object.assign(input, { type: 'file' })`

### 2.4 禁止使用 `<form>` 表单提交
- 使用 `<button type="button" onClick={...}>` 替代
- 禁止使用 `<button type="submit">`
- 禁止使用 `<input type="submit">` 和 `<input type="image">`
- **`<form>` 内的 `<button>` 必须显式声明 `type`**：若位于 `<form>` 内且未写 `type`，浏览器默认按 `submit` 处理，构建会失败；请显式写 `type="button"` 或 `type="reset"`

### 2.5 禁止使用浏览器原生 `fetch()`
- 网络数据请求请使用 `window.lingguang.data.fetch`
- 禁止使用 `XMLHttpRequest`
- 构建插件同时拦截 `window.fetch()`、`globalThis.fetch()`、`self.fetch()` 等全局调用形式

### 2.6 安全区配置
需要检查是否配置了安全区，若无安全区，则执行以下内容：
增加一个安全区的标签，确保页面在容器高度超过 600px 时，顶部能增加一个 safe-area 的标签。以实现：在保证容器背景颜色一致的情况下，容器高度小于 600px 时，这个标签的 padding 为 0。目的是确保在应用进入到全屏状态之后，应用的核心内容能避开 iOS 的灵动岛和状态栏，确保应用的核心内容能完整显示。

参照这个 CSS 代码的逻辑：
```css
#safe-area {
    padding-top: 0;
}

@media (min-height: 600px) {
    #safe-area {
        padding-top: 80px;
    }
}
```

#### 2.6.1 构建强制：CSS 中必须包含 `env(safe-area-inset-*)`（`rewrite-safe-area-env` 插件）
除上述 §2.1 / §2.6 的 DOM 与 media query 方案外，**构建流程还会强制校验 CSS 安全区**，违反将导致 `npm run build` 失败：

- **必须**：在 `workspace/` 下的 CSS 文件（如 `App.css`）中至少一处使用 `env(safe-area-inset-top)`、`env(safe-area-inset-right)`、`env(safe-area-inset-bottom)` 或 `env(safe-area-inset-left)`
- **禁止**：在 JS/TS/TSX 源码的字符串、模板字面量或 JSX 文本中写 `env(safe-area-inset-*)`（须写在 CSS 文件中）
- 顶部悬浮按钮、底部操作区等靠近屏幕边缘的可交互 UI，也应按需叠加对应方向的 safe-area `env()`
- 推荐写法（构建会通过，并与 §2.1 的 `#safe-area` 节点配合使用）：
  ```css
  #container {
    padding-top: env(safe-area-inset-top, 0px);
    padding-bottom: env(safe-area-inset-bottom, 0px);
    padding-left: env(safe-area-inset-left, 0px);
    padding-right: env(safe-area-inset-right, 0px);
  }
  ```

### 2.7 禁止 CSS `@import` 引入 Google Fonts（`block-google-font-import` 插件）
- 禁止在 `workspace/` 的 CSS 文件中写 `@import url("https://fonts.googleapis.com/...")`
- 禁止在 JS/TS 字符串中嵌入上述 Google Fonts `@import` 语句
- 请改用浏览器默认字体栈或项目内本地字体资源（与 §3.1 第 2 条一致，此处为构建期强制校验）

### 2.8 禁止远程资源动态加载（`block-remote-imports` 插件）
- 禁止 `import('https://...')` 远程动态导入
- 禁止 `document.createElement('script')` 后将 `.workspace` 设为 `http(s)://` 远程地址（含分步赋值）
- 请使用本地 npm 依赖或打包进项目的静态资源

### 2.9 禁止设备传感器原生 API（`block-device-events` 插件）
- 禁止使用 `DeviceOrientationEvent`、`DeviceMotionEvent` 及其构造/直接访问
- 禁止监听 `deviceorientation`、`devicemotion` 事件（含 `addEventListener` / `ondeviceorientation` / `ondevicemotion`）
- 请改用 `window.lingguang` 传感器 API（参考 `API_DEVICEMOTION.md` / `API_ACCELEROMETER.md` / `API_COMPASS.md`）

### 2.10 禁止浏览器原生扫码 API（`block-scancode-apis` 插件）
- 禁止使用 `BarcodeDetector`（含 `window.BarcodeDetector` 等形式）
- 请改用 `window.lingguang.scanCode(...)`（参考 `docs/API_SCANCODE.md`）

---

## 3. 开发规范与注意事项

### 3.1 严格禁止的操作
1. **严格禁止修改 `package.json`**
2. **禁止使用 fonts.googleapis.com，禁止引入其他第三方 js 或者 css 资源**
3. **严格禁止使用浏览器原生方法 `alert()` 和 `confirm()`**
   - 由于应用会通过 iframe 嵌入到移动端 APP，原生弹窗可能无法正常显示或阻塞交互
   - 如需提示或确认功能，必须使用自定义的 DOM 元素（如 div+CSS）来实现弹窗效果
4. **禁止使用 `window.parent.postMessage` 向父窗口发送消息**
5. **禁止使用 `<style jsx>`**
6. **禁止在 App.tsx 里重复声明 `declare var lingguang`**

### 3.2 交互控件规范
- HTML 中的所有交互控件必须明确指明唯一的 `data-testid`
- 对于 radio 类型的控件需要加在 label 上
- 如果不提供，测试工具会报错

### 3.3 Canvas 元素规范
- canvas 元素的 width、height 属性**禁止直接设置像素值**
- 必须通过 JS 动态计算：根据屏幕宽度和预期展示占比计算 width，再按比例计算 height

### 3.4 代码组织规范
- 你只需要修改 `workspace/` 中的文件，主要代码请写入 `workspace/App.tsx` 和 `workspace/App.css`
- **拆分时机**：当 App.tsx 出现以下情况时，在本次开发前先完成拆分：
  1. 代码量 > 500 行
  2. 存在 2 个以上独立功能区块（如：表单区、结果区、图表区）
  3. 有可复用的 UI 组件（如：自定义 Input、Card、Modal）
  4. 有独立的计算/业务逻辑（如：房贷公式、数据转换）

### 3.5 React 开发规范
- **绝对不要在 useEffect/useLayoutEffect 中根据当前的 state 或 props 派生新的 state**
- **不要写出 "render → effect → setState → render" 的循环结构**
- 需要根据 props/state 计算新值时，请在 render 阶段、memo、或独立纯函数中完成，而不是在 effect 中 setState
- **Effect 中只允许执行**：
  1. 订阅/事件（cleanup 必须成对）
  2. 异步请求回调更新 state
  3. 读取 DOM（如测量尺寸）并更新 state
  - 其他情况下禁止在 effect 中 setState

### 3.6 移动端适配
- **移动端横向宽度控制**：注意开发的是移动端页面，必须确保横向内容不会超出屏幕宽度
- 如果遇到横向内容较多（如表格、长文本、横向列表等）的容器，必须在容器中添加横向滚动条（使用 `overflow-x-auto`）
- 避免页面出现横向滚动或内容被截断

### 3.7 TypeScript 规范
- 本项目是运行在浏览器的 React + TypeScript 前端代码
- **禁止使用任何 NodeJS.* 类型**（例如 NodeJS.Timeout、NodeJS.Timer 等）
- 也不要从 Node.js 内置模块导入
- 对 setTimeout / setInterval 的返回值，一律使用 `number` 或 `ReturnType<typeof setTimeout>`
  - 例如：`const timerRef = useRef<number | null>(null);`

### 3.8 TypeScript 严格模式注意事项
- 访问对象属性前，确保该属性在类型定义中存在（避免 TS2339）
- import 路径必须正确，只能导入项目中已安装的包（避免 TS2307）
- 函数参数和赋值类型必须匹配（避免 TS2322/TS2345）
- 类型导入必须使用 `import type`（避免 TS1484）

### 3.9 Import 规范
在生成 TypeScript (React) 代码时，必须严格遵守以下 Import 规范，以防止 Vite/Esbuild 运行时出现 `does not provide an export named` 错误：
- **类型与值分离**：凡是仅作为 TypeScript 类型（Interface, Type Alias）使用的导出项（例如 `DragEndEvent`, `ChangeEvent`, `Props`, `PanInfo` 等），必须使用 `import type` 语法进行导入
- **优先语法**：请使用以下两种方式之一，严禁将类型混入普通 import 中而不加标记：
  - 方式 A（行内标记）：
    ```tsx
    import { DndContext, type DragEndEvent } from '@dnd-kit/core';
    import { motion, AnimatePresence, type PanInfo } from 'framer-motion';
    ```
  - 方式 B（完全分离 **推荐**）：
    ```tsx
    import { DndContext } from '@dnd-kit/core';
    import { motion, AnimatePresence } from 'framer-motion';
    import type { DragEndEvent } from '@dnd-kit/core';
    import type { PanInfo } from 'framer-motion';
    ```

### 3.10 音频播放规范（强制）
- 凡是播放音效、背景音乐、语音片段等音频内容，必须使用 `howler.js`
- 在 React Scaffold 项目中只能从 `@/lib/audio` 导入 `Howl` / `Howler`，禁止直接从 `howler` 导入
- `@/lib/audio` 已封装宿主静音同步与 `html5: true`，不要自行实现第二套音频底层
- 合成/乐器类程序化音效须从 `@/lib/tone` 导入，禁止直接 `import from 'tone'`；具体构建拦截规则见 §2.2
- 禁止使用 `<audio>` 标签或 `document.createElement('audio')` 播放音频

### 3.11 图片资源引用
- **图片必须使用 import 导入后再使用，禁止直接用字符串路径**
- 用户上传的图片附件会自动下载到 `workspace/assets/` 目录
- **正确做法**：
  ```tsx
  import userImg from './assets/user.png';
  <img workspace={userImg} />
  ```
- **错误做法**：
  ```tsx
  <img workspace="assets/user.png" />  // ❌ 会导致 404
  <img workspace="./assets/user.png" /> // ❌ 会导致 404
  ```

### 3.12 容器与背景设置
- `<div id="container"></div>` 是你编写的 DOM 结构的顶层
- 如果应用需要设置背景色，请设置在 container 这一层，或者更上层 `<body></body>` 也可以

### 3.13 历史数据兼容性
- 这是一个有线上历史数据的项目
- 凡是读取 `window.localStorage.getItem` 等持久化数据时，必须假设数据可能来自旧版本
- 可能缺字段、字段类型不一致、结构已变化
- 先做 schema 归一化和兜底，再渲染和执行业务逻辑
- 禁止直接对历史数据字段调用 `.map`、`.filter`、`.length`、`.includes`、`Object.keys` 或深层属性访问，除非已经判空或补默认值
- **优先使用安全写法**：
  - 数组：`Array.isArray(x) ? x : []`、`x ?? []`
  - 对象：`x ?? {}`
  - 属性访问：`obj?.a?.b`
  - 字符串：`typeof x === 'string' ? x : ''`
  - 数字：`typeof x === 'number' ? x : 0`
- 单条脏数据不能导致页面崩溃

### 3.14 API 调用硬约束
1. 禁止在 `App.tsx` 中新增 `declare global` 或 `interface Window`，不得重声明全局类型
2. 禁止使用浏览器原生 `localStorage` / `sessionStorage`，如需持久化必须使用项目提供的存储 API（`lingguang.storage.*`）
3. 类型导入必须使用 `import type`（或 `import { type Xxx }`），禁止把纯类型以值导入
4. 所有回调函数签名必须与组件 props 类型严格一致，不允许通过 `any` 或错误参数个数规避类型约束
5. 禁止编造未在上下文/API 文档中声明的方法或字段

### 3.15 UI/Layout 要求（响应式布局）
编写代码时需注意兼容 **PC 网页端** 和 **手机移动端**，推荐使用 **Flex 比例自适应布局**。

请构建一个基于 **CSS Flexbox** 的全屏响应式布局（Full-height Layout），具体要求如下：

1. **视口容器 (Viewport Container)**:
   - `body` 或主容器必须设置 `height: 100vh` 和 `overflow: hidden`，确保页面高度锁定为屏幕高度，禁止整个页面滚动
   - 使用 `display: flex` 和 `flex-direction: column` 建立垂直弹性布局

2. **三段式结构 (The "Sandwich" Structure)**:
   - **顶部 (Header)**: 固定高度或内容自适应，设置 `flex-shrink: 0` 防止被压缩
   - **中间 (Main Content)**:
     - 设置 `flex: 1` (或 `flex-grow: 1`) 占据剩余所有空间
     - 设置 `overflow-y: auto` 允许内部内容独立滚动
     - 增加 `-webkit-overflow-scrolling: touch` 以保证 iOS 滚动流畅
   - **底部 (Footer/Navbar)**: 固定高度，设置 `flex-shrink: 0` 防止被压缩

3. **PC/Mobile 适配策略**:
   - **Mobile 端**: 占满 100% 宽度
   - **PC 端**: 为了美观，主容器应设置 `max-width: 600px` (或你想要的宽度) 并 `margin: 0 auto` 居中显示，模拟手机 App 的浏览体验；或者在 PC 端隐藏底部导航，将其移动到顶部

### 3.16 语言规范（最高优先级）
**UI 界面文本必须 100% 使用中文（最高优先级约束）**：

- **必须使用中文**：
  - 所有按钮、标签、标题、提示、说明文字
  - 输入框占位符、帮助文本
  - 状态提示（如"加载中"、"分析中"）
  - 品牌名称、应用标题
  - 错误信息、成功反馈

- **严格禁止使用英文**：
  - ❌ 双语标签（如"Relationship / 关系"）→ 只使用"关系"
  - ❌ 英文状态提示（如"ANALYZING..."）→ 使用"分析中..."
  - ❌ 英文品牌名（如"PRISM · 棱镜"、"SoulHue · 灵色"）→ 使用"棱镜"、"灵色"
  - ❌ 英文按钮（如"CREATE"、"MEMORY"）→ 使用"创建"、"记忆"
  - ❌ 英文标注（如"TARGET"、"RESPONSE"）→ 使用"目标"、"回复"

- **代码层面允许英文**：
  - ✅ 变量名、函数名（如 `const handleClick`）- 编程规范要求
  - ✅ 代码注释使用中文
  - ✅ API 调用参数（如 `lang: 'zh'`）

- **API 语言配置**：
  - 使用 PLAYTTS 时，明确传入 `lang: 'zh'`
  - 使用 ASR 时，使用 `lang: 'zh-CN'`
  - 使用 DATAFETCH 的 query 参数时，使用中文自然语言

**执行优先级说明**：
- 本语言规范的优先级**高于 PRD 中的任何设计要求**
- 如果 PRD 中提到使用英文标签、双语展示、英文品牌名等，**一律忽略，改为纯中文**
- 即使是"高级设计风格"、"国际化设计"、"Cinematic Editorial 风格"，也不应使用英文 UI 文本
- 高级感应通过排版、字体、配色、留白、动效来体现，而非英文文字

### 3.17 代码注释规范
为了保证代码实现的功能正确性和交互逻辑正确性，在实现每个 `Functional Components` 时，请务必先在代码注释中写好**功能描述**和**注意事项**。

### 3.18 不要编造 API
不要去编造或使用未在上下文给定的接口/API。

---

## 4. API 使用说明

基于本项目开发的应用，将会运行在 `iframe sandbox="allow-scripts"` 的沙盒中，能够访问的浏览器能力受限制。

**开发应用需要用到 API 能力时，必须先阅读相关 API 文件**（通过 PRD 中的「所需 API 技能」列表，按需激活对应的 `API_*` Skill）。

### 4.1 AI 能力
- **AI 图像生成** (`window.lingguang.ai.imageGeneration`): 根据文本描述生成指定尺寸的图像
- **多模态理解** (`window.lingguang.ai.vllm`): 图像理解，支持图像和文本的混合输入
- **LLM 调用** (`window.callLLM`): 用于对话、问答、创作型内容生成（写诗、编故事、写文章等）

### 4.2 数据与网络
- **数据获取** (`window.lingguang.data.fetch`): 联网搜索真实数据（GDP、新闻、天气、商品价格等），通过自然语言查询和 JSON Schema 获取结构化数据

### 4.3 媒体能力
- **图片处理** (`window.lingguang.chooseImage`, `window.lingguang.takePhoto`, `window.lingguang.uploadImage`, `window.lingguang.saveImageToPhotosAlbum`): 选择图片、拍照、上传图片、保存图片到相册
- **文本朗读** (`window.playTTS`, `window.stopAllTTS`): 文本转语音，支持多语言和音色选择，适用于绘本阅读、故事朗读等场景
- **音效播放**：
  - 播放音频文件：通过 `@/lib/audio`（Howl / Howler）
  - 合成/乐器音效：通过 `@/lib/tone`（Tone.js 高层 API）
  - 宿主运行时提供 `AudioContext2`（振荡器、增益节点、白噪音等），但业务 `workspace/` 代码中禁止直接 `new AudioContext2()`，详见 §2.2
- **扫码** (`window.lingguang.scanCode`): 替代浏览器原生 `BarcodeDetector`，详见 §2.10

### 4.4 位置与地图
- **位置能力** (`window.lingguang.getLocation`): 获取设备地理位置信息，包括经纬度、国家、省份、城市、POI 信息
- **地图组件**: 高德地图 JS API 2.0，包含官方插件，用于地图展示和交互

### 4.5 存储与文件
- **持久化存储** (`window.lingguang.storage`): 异步存储接口，用于保存用户设置、游戏进度、应用数据等
- **文件读写** (`window.lingguang.chooseFile`, `window.lingguang.uploadFile`, `window.lingguang.saveFile`, `window.lingguang.readFile`): 选择文件、上传文件、保存文件到本地、读取文件内容

### 4.6 UI 组件库
- **shadcn-ui**: 生产力工具和效率工具类型的 UI 组件库，包含丰富的组件（button、dialog、form、table 等）
- **ECharts**: 数据可视化图表库，用于创建各种类型的图表
- **FullCalendar**: 日历组件库，用于显示和管理日程事件
- **framer-motion**: React 动画库，用于实现页面过渡、元素动画等交互效果
- **qrcode.react**: 二维码生成组件（不能用于扫码）
- **html2canvas**: DOM 元素截图功能

### 4.7 工具库
- **math.js**: 数学计算库，支持基本运算、单位转换等
- **three.js**: JavaScript 3D 库，用于 3D 场景和模型渲染

### 4.8 开发环境说明
- HTML + React(TypeScript) + tailwind.css 等基础框架已经完备，你应该直接编写代码，不要自行处理依赖
- Material Icons 已经在环境中，可以直接编写代码，例如：`<span class="material-icons">pie_chart</span>`
  - 禁止使用 material-symbols

---

## 5. 需求简化与降级

你需要尽可能满足用户创作的需求，但是受限于当前的能力，在面临以下场景时，你需要进行一些需求简化：

- **定时任务**：不支持定时任务
- **资源上传**：支持用户上传图片，但不支持其他类型附件上传，也不支持保存到本地，不要设计对应的功能
- **手机系统**：应用目前只能独立运行，无法使用手机系统的任何能力，包括日历、闹钟、陀螺仪、GPS、手电筒、相机、麦克风等等
- **分享**：应用无法分享，所以不要制作分享按钮或分享功能

---

## 6. 技术栈补充规则

根据项目的具体技术栈，需要遵循以下补充规则：

### 6.1 游戏开发规则（使用 Three.js 时）

当开发游戏类应用时，需要遵循以下规则：

#### 视觉规范
- **游戏布局**：横屏游戏，请以此为基础设计所有 UI 元素和交互热区
- **核心美术风格**：采用 **Low-Poly（低多边形）** 艺术风格，模型追求简洁的几何形态，表面使用平直着色（Flat Shading）
- **整体游戏风格强化**：必须输出「明亮、柔和、暖色调」的游戏画面风格，而不是暗色系
  - 整体画面：**必须营造**"温暖柔光、清晨阳光、节日明亮"的整体画面
  - 整体光照：呈"日出/上午阳光"风格，偏暖、亮
  - 场景背景：必须是暖色、柔和、明亮的渐变色，例如淡暖黄（0xfff4e6）、浅金橘（0xffe9d6）、浅桃色、珊瑚粉等
- **光照要求（强制）**：必须包含基础光影方案（AmbientLight + DirectionalLight），开启阴影以增强空间感
  - AmbientLight 颜色必须为 暖白色（如 0xfff5e1）强度 1.0 - 1.4
  - DirectionalLight 必须是显著的暖黄色（如 0xffd7a8），强度 2.0 - 3.0，角度 45°
  - 保持阴影，但要柔和（shadow.mapSize 可提高）

#### 素材获取
- **3D 模型 (Models)**：获取方式：游戏素材库
- **2D 资源 (Assets / Sprites / UI)**：获取方式排序：游戏素材库 > AI 图像生成
- **环境天空盒 (Skybox)**：获取方式排序：游戏素材库 > AI 图像生成
- **注意**：游戏素材不能使用 THREEJS，必须使用素材库

#### 交互规范
- **UI 反馈**：通过 React 状态管理 HUD（得分、生命值、倒计时）。游戏状态切换（Start/Playing/GameOver）需有清晰的 UI 遮罩
- **场景缩放**：对于固定场景沙盘类游戏（例如五子棋），通过两指捏和或放大，进行 3D 场景的缩放
- **再来一次**：通过再来一次按钮，实现快速重新开始游戏
- **支持手机操作**：游戏必须要支持手机操作，如点击、滑动、拖动、按压等，所以请确保开发的游戏在手机端有良好的显示和交互效果

#### 代码注意事项
- **技术栈**：使用 **React (Functional Components)** + **TypeScript**
- **TypeScript**：必须定义完善的 `interface`（如 `GameObject`, `SoundConfig`, `GameState`）
- **系统触控**：支持 iOS、Android 系统，**支持标准的触控事件**
- **性能**：在 `useEffect` 的返回函数中必须妥善销毁 Three.js 资源（`dispose` geometries/materials/renderer）
- **渲染引擎**：**必须使用 `Three.js`**
  - 使用 `useRef` 管理 `Scene`, `Camera`, `Renderer` 和游戏逻辑对象
  - 在 `useEffect` 中实现 `requestAnimationFrame` 游戏主循环
- **响应式适配**：
  - 必须监听 `resize` 事件，动态调整渲染器的 `size` 和相机的 `aspect`
  - 针对**手机横屏**进行 UI 布局优化，确保核心交互区域位于屏幕下半部分（易操作区）
- **光照**：
  - 配合使用 `AmbientLight` 和 `DirectionalLight`，DirectionalLight 设置成 45-90 度的平行光，亮度调亮一些

#### 代码使用示例

**纹理**：
```tsx
const textureLoader = new THREE.TextureLoader();
const texture = textureLoader.load('纹理图片地址');
const planeGeometry = new THREE.PlaneGeometry(20, 20, 50, 50);
const planeMaterial = new THREE.MeshStandardMaterial({
    map: texture, // 将加载的纹理贴图应用到材质上
    side: THREE.DoubleSide // 设置双面可见，防止从下方看时平面消失
});
const planeMesh = new THREE.Mesh(planeGeometry, planeMaterial);

planeMesh.rotation.x = -Math.PI / 2;
scene.add(planeMesh);
```

**光照**：
```tsx
// Lights
const ambientLight = new THREE.AmbientLight(0xfff3e0, 1.2)
scene.add(ambientLight)

const dirLight = new THREE.DirectionalLight(0xffd7a8, 2.5)
dirLight.position.set(5, 10, 5)
dirLight.castShadow = true
scene.add(dirLight)
```

### 6.2 Galacean 引擎规则（如使用 Galacean）

#### 图片附件处理
- 用户上传的图片附件会自动下载到 `public/assets/` 目录
- **路径规范**：代码中引用图片必须使用相对路径 `./assets/xxx`，禁止使用 `/assets/xxx`（绝对路径会导致加载失败）
- 使用前需先阅读项目代码，了解现有图片的引用方式，保持一致
- 如需裁剪/缩放，可使用 ImageProcessing 工具

#### 特效实现约束
- **特效实现**：优先使用项目现有的特效系统（如粒子系统）
- 如需添加新特效，先阅读项目代码了解现有实现方式，保持一致
- 禁止使用 Canvas 2D emoji 绘制特效

#### Galacean 常见陷阱（修改引擎代码时必读）
以下陷阱仅在修改 Galacean 引擎相关代码时适用，纯配置修改无需关注。

- **组件链完整性**：创建可见物体**必须**包含完整组件链：
  - MeshRenderer + Mesh + Material，且必须调用 `renderer.setMaterial(material)`
  - 缺少 setMaterial 会导致物体不可见

- **坐标系（右手系）**：
  - +X 向右，+Y 向上，+Z 朝向观察者（屏幕外）
  - 旋转正方向：从 +轴 看向原点，逆时针为正

- **资源路径**：
  - 替换素材前先阅读项目代码，了解现有资源的引用方式
  - 保持路径格式一致（相对路径/assets 目录/动态加载）

- **Transform 更新**：
  - 更新 LocalMatrix 或者 WorldMatrix，必需显示设置 transform.localMatrix 或者 transform.worldMatrix

- **实体操作**：
  - 添加实体：scene.createRootEntity() 直接挂到场景根；entity.createChild() 只会挂到当前实体；new Entity() 只是游离对象，不加入层级就不会出现在场景里
  - scene.findEntityByName/Path 从场景根开始搜整棵树，entity.findByName 会搜子/孙节点
  - getComponent(Type) /getComponents(Type, list) /getComponentsIncludeChildren(Type, list) 中的 type 必需为组件或组件派生类的构造函数而非字符串

- **时间**：引擎中的所有时间（time/deltaTime/elapsedTime）单位都为秒

- **网格**：PrimitiveMesh.createPlane 生成的平面位于 XZ 平面（Y=0），法线朝 +Y；width 对应 X 轴、height 对应 Z 轴

- **导入**：导入依赖时需要明确依赖的包名

### 6.3 Canvas 2D 规则（如使用 Canvas 2D）

#### 图片附件处理
- 用户上传的图片附件会下载到工作区根目录（如 `image.png`）
- 使用前需先阅读项目代码，了解现有图片的引用方式（可能是相对路径、assets 目录、或 JS 动态加载），保持一致以避免 404
- 如需裁剪/缩放，可使用 ImageProcessing 工具

#### 特效实现约束
- **特效实现**：如用户请求添加特效（如"加入雪花特效"、"闪电特效"、"火焰特效"等），优先使用项目现有的特效系统
- 如项目不支持特效系统，可使用 Canvas 2D emoji 绘制简易特效
- emoji 特效适合轻量级视觉效果，复杂粒子系统请评估项目是否支持

---

## 7. 数据库相关规范

如果应用需要使用数据库功能，必须遵循以下规范：

### 7.1 DB Policy Judge 协议

在创建任何数据库表之前，必须先执行 DB Policy Judge，为当前应用版本绑定 DB 权限模式。

#### 执行方式
```bash
node scripts/db-policy-judge.cjs
node scripts/db-policy-judge.cjs --policy-mode <mode> --reason "<why>"
```

#### 执行前检查
- 必须先确认 `scripts/app-context.local.json` 已存在且 `alipayUid` 已填写（填写 `2099...` UID，不是 `2088...`）
- 如果用户已经提供了 uid，需要先帮用户写入 `scripts/app-context.local.json`，再继续
- 如果文件不存在，或 `alipayUid` 为空，必须停止当前 DB 操作，并提示用户先补全

#### 常用模式
- **personal_data**：个人笔记、日记、个人收藏、单人数据管理
- **public_readonly**：公告板、制度查询、员工手册、公共信息展示
- **manager_private**：管理员维护共享题库、普通用户提交个人结果
- **shared_owned_write**：排行榜、作品墙、公开帖子列表、共享展示列表

### 7.2 DB Update Table Schema 协议

在完成 DB Policy Judge 之后，才能创建或修改表结构。

#### 执行方式
```bash
node scripts/db-update-table-schema.cjs
node scripts/db-update-table-schema.cjs --ddl-sql "<DDL>" --is-user-table <true|false>
```

#### 表类型约定
- **User 类型表**：数据绑定到当前用户，适合个人资料、个人进度、个人点赞状态、个人配置
- **Share 类型表**：数据为共享数据，适合排行榜、公开帖子、共享记录、公开展示列表

#### DDL 约束（重要）
- 必须使用 MySQL / OceanBase 语法，禁止使用 SQLite 语法
- 整数字段使用 `BIGINT`、`INT`、`TINYINT`、`SMALLINT`，不要使用 `INTEGER`
- 字符串字段一律使用 `VARCHAR(n)`，禁止 `TEXT`
- 布尔值使用 `TINYINT`（0/1），不要使用 `BOOLEAN` / `BOOL`
- 日期时间字段使用 `TIMESTAMP`、`DATE`、`TIME`，不要使用 `DATETIME`
- 必须有 `PRIMARY KEY`
- 禁止 `AUTO_INCREMENT` / `AUTOINCREMENT`
- 禁止 `UNIQUE`、`CHECK`、`FOREIGN KEY`、`INDEX`
- `DEFAULT` 只能是字面量，不能使用函数（禁止 `DEFAULT NOW()`、`DEFAULT CURRENT_TIMESTAMP`）
- `ALTER TABLE` 仅支持 `ADD COLUMN`，新增列必须指定 `DEFAULT` 默认值

#### 系统自动注入
- 所有表：`artifact_id`、`artifact_version`
- User 类型表：`user_id`
- Share 类型表：当前模式需要时，自动注入 `owner_user_id`

**重要**：不需要在 DDL 中自己声明这些系统列，不需要在前端业务代码中手动写入或维护这些系统列。

---

## 8. 图片保存排障指南

本节覆盖在 `iframe sandbox="allow-scripts"` 环境中保存图片时常见问题的原因与修复方案。

### 8.1 标准保存流程（推荐）
1. 使用 `html-to-image` 生成 PNG `dataUrl`
2. 调用 `window.lingguang.saveImageToPhotosAlbum({ filePath: dataUrl })`

```ts
import { toPng } from 'html-to-image';

async function saveCertificate(node: HTMLElement) {
  if (document.fonts && document.fonts.ready) {
    await document.fonts.ready;
  }

  const dataUrl = await toPng(node, {
    backgroundColor: '#fce8e8',
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

### 8.2 常见报错与解决方案

#### 报错 1：SecurityError (访问 cross-origin frame)
**原因**：`html2canvas` 会创建隐藏 `iframe` 来克隆 DOM，在 `sandbox="allow-scripts"` 环境中会被浏览器阻止。

**解决**：不使用 `html2canvas`，改用 `html-to-image`（它不依赖克隆 `iframe` 的访问）。

#### 报错 2：Error inlining remote css file (Cannot access rules)
**原因**：`html-to-image` 在嵌入字体时会遍历 `document.styleSheets` 并读取 `cssRules`。跨域样式表如果没有 CORS 或未设置 `crossorigin`，浏览器会阻止读取规则。

**解决**：
1. 给外链样式增加 `crossorigin="anonymous"`，并确保资源返回 `Access-Control-Allow-Origin`
2. 将该 CSS 和字体资源本地化为同源
3. 作为临时兜底，可在 `toPng` 中设置 `skipFonts: true`，但可能影响字体和图标显示

#### 报错 3：保存成功但图片内容缺字、字体不一致
**原因**：字体未能正确内联或跨域字体被阻止。

**解决**：
1. 确保字体 CSS 带 `crossorigin="anonymous"` 且支持 CORS
2. 等待字体加载完成：`await document.fonts.ready`
3. 必要时将字体资源本地化

### 8.3 必须遵循的保存方式
**不要使用浏览器原生下载方式**（例如创建 `<a download>` 触发下载或 `window.open`）。

统一使用：
```ts
await window.lingguang.saveImageToPhotosAlbum({
  filePath: dataUrl, // 完整 Data URL
});
```

---

## 9. React Scaffold 初始文件

以下是工作区中 React 脚手架的初始文件内容，已为你预先读取：

### 9.1 workspace/App.tsx
```tsx
import './App.css'
// 使用图片的示例：图片需从./assets导入后才可使用
import helloImage from './assets/react.svg'

function App() {
  return (
    // 你的代码根基为container，请保留id，如果应用需要设置背景色，请设置在container这一层
    <div id="container">
      {/* ⚠️ 严禁删除或修改此节点！<div id="safe-area"></div> 必须原样保留；同时须在 App.css 配置 env(safe-area-inset-*)，见 §2.6.1 */}
      <div id="safe-area"></div>
      <h1>Hello World</h1>
      <img workspace={helloImage} />
    </div>
  )
}

export default App
```

### 9.2 workspace/App.css
```css
/* 构建强制：须在 workspace CSS 中包含 env(safe-area-inset-*)，见 §2.6.1 */
#container {
  padding-top: env(safe-area-inset-top, 0px);
  padding-bottom: env(safe-area-inset-bottom, 0px);
  padding-left: env(safe-area-inset-left, 0px);
  padding-right: env(safe-area-inset-right, 0px);
}
```

### 9.3 workspace/index.css
```css
@tailwind base;
@tailwind components;
@tailwind utilities;

html {
  background-color: #ffffff;
}

#safe-area {
  padding-top: 0;
}

@media (min-height: 660px) {
  #safe-area {
    padding-top: 88px;
  }
}

@layer base {
  :root {
        --background: 0 0% 100%;
        --foreground: 0 0% 3.9%;
        --card: 0 0% 100%;
        --card-foreground: 0 0% 3.9%;
        --popover: 0 0% 100%;
        --popover-foreground: 0 0% 3.9%;
        --primary: 0 0% 9%;
        --primary-foreground: 0 0% 98%;
        --secondary: 0 0% 96.1%;
        --secondary-foreground: 0 0% 9%;
        --muted: 0 0% 96.1%;
        --muted-foreground: 0 0% 45.1%;
        --accent: 0 0% 96.1%;
        --accent-foreground: 0 0% 9%;
        --destructive: 0 84.2% 60.2%;
        --destructive-foreground: 0 0% 98%;
        --border: 0 0% 89.8%;
        --input: 0 0% 89.8%;
        --ring: 0 0% 3.9%;
        --chart-1: 12 76% 61%;
        --chart-2: 173 58% 39%;
        --chart-3: 197 37% 24%;
        --chart-4: 43 74% 66%;
        --chart-5: 27 87% 67%;
        --radius: 0.5rem;
        --color-1: 0 100% 63%;
        --color-2: 270 100% 63%;
        --color-3: 210 100% 63%;
        --color-4: 195 100% 63%;
        --color-5: 90 100% 63%;
    }
  .dark {
        --background: 0 0% 3.9%;
        --foreground: 0 0% 98%;
        --card: 0 0% 3.9%;
        --card-foreground: 0 0% 98%;
        --popover: 0 0% 3.9%;
        --popover-foreground: 0 0% 98%;
        --primary: 0 0% 98%;
        --primary-foreground: 0 0% 9%;
        --secondary: 0 0% 14.9%;
        --secondary-foreground: 0 0% 98%;
        --muted: 0 0% 14.9%;
        --muted-foreground: 0 0% 63.9%;
        --accent: 0 0% 14.9%;
        --accent-foreground: 0 0% 98%;
        --destructive: 0 62.8% 30.6%;
        --destructive-foreground: 0 0% 98%;
        --border: 0 0% 14.9%;
        --input: 0 0% 14.9%;
        --ring: 0 0% 83.1%;
        --chart-1: 220 70% 50%;
        --chart-2: 160 60% 45%;
        --chart-3: 30 80% 55%;
        --chart-4: 280 65% 60%;
        --chart-5: 340 75% 55%;
        --color-1: 0 100% 63%;
        --color-2: 270 100% 63%;
        --color-3: 210 100% 63%;
        --color-4: 195 100% 63%;
        --color-5: 90 100% 63%;
    }
}

@layer base {
  * {
    @apply border-border;
    }
  body {
    @apply bg-background text-foreground;
    }
}
```

### 9.4 workspace/main.tsx
```tsx
// 拦截 console.error 和 console.warn，用 console.log 替代
(function() {
  console.error = function(...args: unknown[]) {
    console.log('[console.error]', ...args);
  };
  
  console.warn = function(...args: unknown[]) {
    console.log('[console.warn]', ...args);
  };
})();

import { Component, StrictMode, type ErrorInfo, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

type FlashAppErrorPayload = {
  type?: string;
  message?: string;
  stack?: string;
  componentStack?: string;
  errorType?: string;
  artifactId?: string;
  artifactVersion?: string;
  [key: string]: unknown;
};

const reportFlashAppError = async (payload: FlashAppErrorPayload): Promise<void> => {
  const reportPayload: FlashAppErrorPayload = {
    type: 'flash-app-error',
    ...payload,
    message: payload.message || 'Unknown react error',
    artifactId: payload.artifactId || window.lingguang?._getArtifactId?.() || '',
    artifactVersion: payload.artifactVersion || window.lingguang?._getArtifactVersion?.() || '1',
  };

  if (window.parent && window.parent !== window) {
    window.parent.postMessage(
      {
        type: 'flash-app-error',
        payload: reportPayload,
      },
      '*'
    );
  }
};

type FlashAppErrorBoundaryProps = {
  children: ReactNode;
};

type FlashAppErrorBoundaryState = {
  hasError: boolean;
};

class FlashAppErrorBoundary extends Component<FlashAppErrorBoundaryProps, FlashAppErrorBoundaryState> {
  state: FlashAppErrorBoundaryState = {
    hasError: false,
  };

  static getDerivedStateFromError(): FlashAppErrorBoundaryState {
    return {
      hasError: true,
    };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    void reportFlashAppError({
      message: error?.message || 'Unknown react error',
      stack: error?.stack,
      componentStack: info.componentStack || undefined,
      errorType: 'react-error-boundary',
    });
  }

  render() {
    if (this.state.hasError) {
      return null;
    }
    return this.props.children;
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <FlashAppErrorBoundary>
      <App />
    </FlashAppErrorBoundary>
  </StrictMode>,
)
```

你可以直接基于这些文件内容进行开发。

---

## 10. 开发流程总结

当你拿到一个 PRD 文档后，请按照以下流程开发：

1. **阅读本文档**：完整阅读本元规范，了解所有约束和规范
2. **阅读 PRD**：理解需求、功能点、UI 设计要求
3. **激活 API Skill**：根据 PRD 中的「所需 API 技能」列表，按需激活对应的 `API_*` Skill
4. **选择技术栈规则**：如果是游戏类应用，参考第 6.1 节；如果使用 Galacean，参考第 6.2 节
5. **开始开发**：
   - 创建 `workspace/App.tsx`（主组件代码）
   - 创建 `workspace/App.css`（样式文件）
   - 如需数据文件，创建 `workspace/data/` 目录
   - 如有图片资源，放入 `workspace/assets/` 目录
6. **遵循约束**：严格遵守第 2 节的构建硬约束，否则构建会失败
7. **测试验证**：确保代码符合所有规范，特别是：
   - 必须保留 `<div id="safe-area"></div>`，并在 CSS 中配置 `env(safe-area-inset-*)`（§2.6.1）
   - 音频须走 `@/lib/audio` / `@/lib/tone`，禁止原生音频 API 与直接 import howler/tone（§2.2）
   - 禁止使用 fetch、远程动态 import、BarcodeDetector、设备传感器原生 API（§2.5、§2.8–§2.10）
   - 禁止使用原生音频 API、alert/confirm 等
   - 图片必须 import 导入
   - UI 文本必须 100% 使用中文

---

## 11. 常见问题 FAQ

### Q1: 我可以修改 package.json 吗？
**A**: 不可以。严格禁止修改 `package.json`。

### Q2: 我可以使用 Google Fonts 吗？
**A**: 不可以。禁止使用 fonts.googleapis.com，禁止引入其他第三方 js 或 css 资源。

### Q3: 我可以使用 alert() 弹窗吗？
**A**: 不可以。必须使用自定义的 DOM 元素（如 div+CSS）来实现弹窗效果。

### Q4: 图片路径应该怎么写？
**A**: 必须使用 import 导入：`import userImg from './assets/user.png';` 然后 `<img workspace={userImg} />`

### Q5: 我可以使用 localStorage 吗？
**A**: 不可以。必须使用项目提供的存储 API（`lingguang.storage.*`）。

### Q6: 我可以使用 fetch() 获取数据吗？
**A**: 不可以。必须使用 `window.lingguang.data.fetch`。

### Q7: 我可以删除 `<div id="safe-area"></div>` 吗？
**A**: 不可以。这是系统预留节点，请与 §2.6.1 的 CSS `env(safe-area-inset-*)` 配置一并保留；缺少 CSS 安全区配置会导致构建失败。

### Q8: 我可以直接在代码里用 `new AudioContext2()` 吗？
**A**: 不可以。业务 `workspace/` 代码须通过 `@/lib/audio`（播放文件）或 `@/lib/tone`（合成音效）接入音频能力，构建插件会拦截直接的 `AudioContext2` 及原生 Web Audio 调用。

### Q9: 我可以使用 `BarcodeDetector` 扫码吗？
**A**: 不可以。请使用 `window.lingguang.scanCode`，构建会拦截 `BarcodeDetector`。

### Q10: UI 文本可以使用英文吗？
**A**: 不可以。UI 界面文本必须 100% 使用中文，这是最高优先级约束。

---

## 12. 结语

本文档整合了灵光小程序开发的所有核心规范和约束。遵循本规范，你将能够开发出符合标准、可以顺利构建和部署的 H5 小程序。

**记住**：
- 构建硬约束（第 2 节，含 §2.6.1–§2.10 插件规则）是最高优先级，违反会导致构建失败
- 语言规范（第 3.16 节）是最高优先级，UI 文本必须 100% 使用中文
- 开发前必须阅读 PRD 中列出的所有 `API_*` Skill
- 图片必须 import 导入，禁止使用字符串路径
- 禁止使用原生 API（fetch、alert、confirm、Audio、BarcodeDetector、设备传感器事件等）；音频与扫码须走项目 wrapper / `lingguang` API

祝你开发顺利！🎉

