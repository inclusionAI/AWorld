---
name: education_poem_miniapp_produce_skill
description: 这是一个专门用于指导如何制作灵光闪应用之诗词教育类的小应用的指南
---

# 诗词教育类灵光小程序制作指南 (Education Poem Miniapp Produce Skill)

> **适用场景**：当你需要从零开始，制作一个以“古诗词教育、赏析、互动问答”为核心的灵光小程序（如《琵琶行》、《长恨歌》等）时，请完整阅读并遵循本指南。
> **前置依赖**：必须同时遵循 `lingguang-miniapp-meta-skill`（灵光小程序开发元规范）。

---

## 1. 核心工作流 (Workflow)

制作一个高质量的诗词教育小程序，标准工作流应分为以下四个阶段：

### 第一阶段：资料检索与内容设计 (Content Design)
1. **检索原典**：如果自身知识库不足，需先搜索该诗词的全文、权威译文、创作背景及核心知识点。
2. **分页切分**：将长篇诗词按逻辑（如每两句/四句一页）切分为多个页面（Page）。
3. **设计互动**：为每一个页面设计 1 道相关的单选题（考查修辞、字词、情感或背景），并准备 3-4 个选项及正确答案。
4. **撰写赏析**：为每个页面提炼简明扼要的【译文】与【赏析】。

### 第二阶段：媒体资产生成 (Media Generation)
1. **背景图生成 (Image)**：
   - **比例要求**：必须是 **2:3**（如 `768x1152`），以完美适配移动端全屏背景。
   - **复用策略**：不需要为每一页单独配图。建议按诗词的“意境段落”（每 4-5 页）共用一张背景图，既保证视觉连贯性，又节省生成时间。
   - **存放路径**：统一输出到 `workspace/assets/image/`。
2. **音频生成 (Audio)**：
   - **TTS 朗读**：为每一页的诗词文本生成对应的朗读音频（使用 `audio_generator`）。
   - **存放路径**：统一输出到 `workspace/assets/audio/`。

---

## 2. 布局与代码规范 (Layout & Code Specs)

为了让后人能快速 pickup，以下明确列出各个核心文件的具体写法和示例代码。

### 2.1 数据结构 (`workspace/data/poemData.ts`)
必须使用 `import` 引入图片，严禁使用字符串硬编码路径。将文本、图片引用、音频引用组装为强类型的结构化数据。

```typescript
// workspace/data/poemData.ts
import img1 from '../assets/image/image1.png';

export interface PoemData {
  lines: string[];       // 诗词正文（如 ["浔阳江头夜送客", "枫叶荻花秋瑟瑟"]）
  image: string;         // 背景图引用
  knowledge: string;     // 译文与赏析
  question: string;      // 互动问题
  options: string[];     // 选项数组
  answer: number;        // 正确选项的索引
}

export const poemData: PoemData[] = [
  { 
    lines: ["浔阳江头夜送客", "枫叶荻花秋瑟瑟"], 
    image: img1, 
    knowledge: "【译文】...【赏析】...", 
    question: "诗中哪两个景物点明了季节是秋天？", 
    options: ["浔阳江、夜", "枫叶、荻花", "主人、客"], 
    answer: 1 
  },
  // ... 更多数据
];
```

### 2.2 样式配置 (`workspace/App.css`)
必须配置安全区环境变量，以满足灵光小程序的构建硬约束。

```css
/* workspace/App.css */
#container {
  padding-top: env(safe-area-inset-top, 0px);
  padding-bottom: env(safe-area-inset-bottom, 0px);
  padding-left: env(safe-area-inset-left, 0px);
  padding-right: env(safe-area-inset-right, 0px);
}
```

### 2.3 核心逻辑与布局 (`workspace/App.tsx`)
采用 **全屏沉浸式 + 底部悬浮卡片** 的布局。以下是核心骨架示例，展示了背景动效、音频控制、固定高度的互动卡片以及底部导航。

```tsx
// workspace/App.tsx
import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronDown, Volume2, VolumeX } from 'lucide-react';
import { Howl } from '@/lib/audio';
import { poemData } from './data/poemData';
import './App.css';

// 导入音频文件
import audio1 from './assets/audio/audio1.mp3';
const audioFiles: Record<number, string> = { 1: audio1 /* ... */ };

export default function App() {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isMuted, setIsMuted] = useState(false);
  const [isCardExpanded, setIsCardExpanded] = useState(true);
  const [showKnowledge, setShowKnowledge] = useState(false);
  const ttsHowlRef = useRef<Howl | null>(null);

  const currentData = poemData[currentIndex];

  // 切换页面时自动播放（如果未静音），并清理上一个音频
  useEffect(() => {
    if (ttsHowlRef.current) ttsHowlRef.current.stop();
    if (!isMuted) {
      const tts = new Howl({ src: [audioFiles[currentIndex + 1]], html5: true });
      ttsHowlRef.current = tts;
      tts.play();
    }
    return () => ttsHowlRef.current?.unload();
  }, [currentIndex, isMuted]);

  return (
    <div id="container" className="relative w-full h-full overflow-hidden bg-black text-white font-serif">
      {/* 必须保留的安全区节点 */}
      <div id="safe-area"></div>
      
      {/* 1. 背景图 (Framer Motion 淡入淡出) */}
      <AnimatePresence mode="wait">
        <motion.img
          key={currentData.image}
          src={currentData.image}
          initial={{ opacity: 0, scale: 1.05 }}
          animate={{ opacity: 0.6, scale: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 1.5 }}
          className="absolute inset-0 w-full h-full object-cover z-0"
        />
      </AnimatePresence>

      {/* 2. 音频控制按钮 (注意 top-[116px] 避开安全区) */}
      <div className="absolute top-[116px] right-4 z-20">
        <button onClick={() => setIsMuted(!isMuted)} className="w-12 h-12 rounded-full bg-black/50 backdrop-blur-sm">
          {isMuted ? <VolumeX /> : <Volume2 />}
        </button>
      </div>

      {/* 3. 核心内容区 */}
      <div className="relative z-10 flex flex-col items-center justify-between h-full p-6 pb-24">
        
        {/* 诗词展示 */}
        <motion.div className="flex-1 flex flex-col items-center justify-center gap-6 w-full">
          <h1 className="text-3xl md:text-5xl font-bold text-amber-100 drop-shadow-lg text-center">
            {currentData.lines[0]}
          </h1>
        </motion.div>

        {/* 4. 互动与赏析卡片 (固定高度防抖动) */}
        <motion.div layout className="w-full max-w-2xl flex flex-col flex-shrink-0 bg-black/60 backdrop-blur-md border border-amber-900/50 rounded-2xl overflow-hidden">
          {/* 卡片 Header (点击折叠/展开) */}
          <div onClick={() => setIsCardExpanded(!isCardExpanded)} className="flex justify-between p-4 bg-amber-900/20">
            <span className="text-amber-200 font-bold">互动与赏析</span>
            <motion.div animate={{ rotate: isCardExpanded ? 0 : 180 }}>
              <ChevronDown className="text-amber-200" />
            </motion.div>
          </div>

          <AnimatePresence initial={false}>
            {isCardExpanded && (
              <motion.div initial={{ height: 0 }} animate={{ height: 'auto' }} exit={{ height: 0 }} className="overflow-hidden">
                <div className="p-6 pt-2">
                  {/* Tabs 切换 */}
                  <div className="flex border-b border-amber-900/50 mb-4">
                    <button onClick={() => setShowKnowledge(false)}>互动问答</button>
                    <button onClick={() => setShowKnowledge(true)}>知识点赏析</button>
                  </div>

                  {/* 内容区：必须固定高度 h-[280px] 防止切换 Tab 时高度跳动 */}
                  <div className="h-[280px] overflow-y-auto">
                    {showKnowledge ? (
                      <div className="text-gray-200 whitespace-pre-line">{currentData.knowledge}</div>
                    ) : (
                      <div>{/* 答题选项渲染逻辑... */}</div>
                    )}
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>

        {/* 5. 底部导航栏 */}
        <div className="absolute bottom-6 left-0 right-0 flex justify-between px-8 z-20">
          <button onClick={() => setCurrentIndex(prev => Math.max(0, prev - 1))}>上一句</button>
          <button>网格选择器</button>
          <button onClick={() => setCurrentIndex(prev => Math.min(poemData.length - 1, prev + 1))}>下一句</button>
        </div>
      </div>
    </div>
  );
}
```

---

## 3. 关键执行细节与成败得失 (Lessons Learned)

在过往的开发实践中，总结出以下极易出错的细节点，**必须严格遵守**：

### 3.1 音频播放的“坑”与最佳实践
- **严禁使用原生 API**：绝对不能使用 `new Audio()` 或 `<audio>` 标签，必须使用 `import { Howl } from '@/lib/audio'`。
- **全局静音逻辑（核心体验）**：
  - 必须维护一个全局的 `isMuted` 状态。
  - **自动播放**：当用户翻页（`currentIndex` 改变）时，如果 `!isMuted`，则自动播放新页面的 TTS 音频；如果已静音，则保持静音。
  - **切换页面时的清理**：在 `useEffect` 监听 `currentIndex` 变化时，必须先 `stop()` 掉上一个页面的音频，防止多个音频重叠播放。
- **组件卸载清理**：必须在 `App.tsx` 的根 `useEffect` 中返回清理函数，调用 `unload()` 释放音频资源。

### 3.2 互动卡片的 UI 稳定性（防抖动）
- **固定高度**：在“互动问答”和“知识点赏析”两个 Tab 之间切换时，内容长度往往不同。**必须为内容容器设置固定高度**（如 `h-[280px] overflow-y-auto`），绝对不能使用 `min-h` + `max-h`，否则切换 Tab 时整个卡片会忽大忽小，导致严重的视觉跳动。
- **折叠/展开动效**：
  - 使用 `framer-motion` 的 `<AnimatePresence>` 包裹折叠内容。
  - 箭头旋转逻辑：展开时箭头朝上，收起时箭头朝下。注意三元表达式的对应关系（如 `rotate: isCardExpanded ? 0 : 180`，取决于你默认使用的 Icon 是 ChevronUp 还是 ChevronDown）。

### 3.3 移动端安全区与层级遮挡
- **安全区节点**：必须保留 `<div id="safe-area"></div>`。
- **悬浮按钮位置**：右上角的音频按钮不要贴顶太近（如 `top-4` 可能会被灵动岛或状态栏遮挡），建议下移至 `top-[116px]` 左右。
- **层级（z-index）**：背景图 `z-0`，内容区 `z-10`，悬浮按钮和导航栏 `z-20`，全屏弹窗（如页面选择器）`z-50`。

### 3.4 视觉与交互的“高级感”
- **配色体系**：诗词类应用推荐使用暗色系背景 + 琥珀色/金色文字（如 Tailwind 的 `bg-black`, `text-amber-100`, `border-amber-900/50`），配合 `backdrop-blur-md`（毛玻璃效果），营造古风高级感。
- **答题反馈**：用户点击选项后，必须立即给出视觉反馈（正确标绿 + Check 图标，错误标红 + X 图标），并禁用其他选项的点击。
- **页面跳转**：除了“上一句/下一句”，必须提供一个“页面选择器（网格视图）”，方便用户快速跳转到指定的诗句，这对于长篇诗词（如 40+ 页）至关重要。

---

## 4. 总结

制作诗词教育类小程序，**核心在于“意境的营造”与“交互的流畅”**。
1. 意境依赖于：2:3 的高质量国风配图、贴合诗意的 TTS 朗读、毛玻璃与暗金色的 UI 质感。
2. 流畅依赖于：全局统一的静音/自动播放逻辑、固定高度防抖动的 Tab 卡片、以及丝滑的 Framer Motion 页面过渡。
遵循以上规范，即可快速、稳定地交付高质量的诗词教育灵光应用。
