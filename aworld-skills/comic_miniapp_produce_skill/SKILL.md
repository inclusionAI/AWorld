---
description: 本技能指南用于指导如何从零开始，基于文本剧本制作一个高质量、风格统一的“小人书/漫画播放”类通用小程序 或者 灵光小程序。该流程强调设定的严谨性、画面的一致性、UI交互的沉浸感，以及工程执行的高效性。
---

# 漫画类灵光小程序制作专业技能 (Comic Mini-App Production Skill)

## 1. 概述
本技能指南用于指导如何从零开始，基于文本剧本制作一个高质量、风格统一的“小人书/漫画播放”类灵光小程序。该流程强调**设定的严谨性**、**画面的一致性**、**UI交互的沉浸感**以及**工程执行的高效性**。

## 2. 标准制作流程

### 第一步：项目初始化 (Project Initialization)
- **复制模板**：将现有的漫画Demo文件夹（`demo_workspace_for_comic`）复制为新的项目文件夹（如 `workspace`），并在`workspace`内部进行相关工作。
    注：如果当前工作目录没有`demo_workspace_for_comic`，你需要参照当前指导以及lingguang-miniapp-meta-skill的指导，来完成接下来的代码文件（对应该小程序的页面布局、交互逻辑，相关数据）以及附件（多媒体附件）的制作工作。
- **清理与准备（⚠️极易踩坑）**：
  - 清理 `assets/image` 和 `assets/audio` 中的旧资源。
  - **特别注意**：如果新章节的页面数少于模板章节，**必须**在 `App.tsx` 中删除多余的图片和音频的 `import` 静态引用以及 `imageMap/audioMap` 中的映射，否则会导致 Vite 编译报错（`Could not resolve`）。

### 第二步：剧本研读与核心设定设计 (Script Reading & Concept Design) —— **【核心关键步】**
- **阅读剧本**：深入理解故事背景、年代设定和人物关系。每个章节通常包含 **6-10个页面（画面）**。
- **编写 `character_design.txt`（角色与视觉设定）**：
  - **年代与基调**：明确故事发生的时代背景，防止“出戏”。
  - **人物特征**：详细定义每个出场人物的五官、发型、衣着、体型，要极其详细。
  - **设计原则**：设计需考虑AI绘画的难易度，特征要鲜明、具体且易于复现。
  - **全面性**：所有人物的所有特征（五官、发型、衣着、体型）都要有具体描述，绝不可因为次要人物而缺少这些必要的特征。
  - **修改原则**：可以对之前的character_design.txt的人物、道具进行增加或者原地修改，但是不要将之前character_design.txt里面已有的人物、道具、场景进行整体的删除。追加的人物、道具、场景等内容要直接写在原有的人物、道具、场景的后面即可，序号递增。不要额外开出“追加”的章节。
- **编写 `page_design.txt`（分镜设计）**：
  - 将剧本拆分为具体的画面（Page）。
  - **分镜要求**：除了该章节的**第一张**和**最后一张**图片外，中间的图片最好采用 **2-3个分镜** 的排版形式（竖分、横分、斜分，可以多样化一些），以增加漫画的叙事张力。
  - **文字要求**：每一个图片都必须包含**对白或旁白文字**，直接印在画面上，帮助观众直接通过观看图片了解剧情。**切忌**在画面或提示词中出现“分镜1”、“分镜2”等明文提示，以免显得突兀。
  - **旁白要求**：旁白文字要能帮助用户仅仅通过旁白文字本身就能了解剧情的推进，所以要稍微详细一些，一般每个page，40-60字左右。

### 第三步：并发资产制作 (Concurrent Asset Generation) —— **【坚决执行步】**
- **图片生成 (Image Generation)**：
  - **Prompt**：务必为中文。并发请求image generator的并发数最高为5。
  - **统一性保障（重中之重）**：必须将 `character_design.txt` 中的完整特征作为“锚点”带入每一个Prompt中，确保全篇人物形象高度统一。
  - **规格要求**：图片的宽高比必须为 **2:3**，分辨率为 **1024x1536**。
  - **文字与排版（画面内嵌文字规范）**：在生图Prompt中必须对画面文字进行极其严格的约束，确保最终效果符合专业漫画标准：
    - **字体与大小**：明确要求“字体统一为楷体，字体稍大，确保在手机屏幕上清晰易读”。
    - **旁白（Narration）**：加的时机要克制，仅在需要交代背景、必要剧情承接或转折时使用。字数要少，简明扼要。**绝对不要**在画面中生成“旁白：”或“旁白【】”等元标签，直接显示内容本身。如需旁白，要在画面中出现，不要在图片中单独额外开出一块空白的位置来专门放置文字。
    - **对白与心理活动（Dialogue/Thoughts）**：是推进剧情的关键。**绝对不要**出现“xxx说：”或“xxx心想：”的提示语，直接展示对话或心理活动的内容本身（可配合对话框或气泡）。
    - **特效文字（Sound Effects）**：在激烈的动作或抓人眼球的场景中，可适当加入特效文字（如“砰！”、“轰！”）增加张力。
    - **人物标识与禁忌**：关键人物初次登场或需要强调时，可以在人物旁边标注【人名】。但**绝对不要**在画面中生成关于“年龄”（如“15岁”）的文字描述。在Prompt中应明确加上：“请注意：画面中绝对不要出现任何关于年龄的文字介绍”。如有次要人物出现，把次要人物名字（不要有 【】）加在该人物出现在画面中的边上。
    - **Prompt 话术建议**：建议使用类似 `画面上必须包含且仅包含以下文字（字体统一为楷体，字体稍大）：‘文字1’ 以及 ‘文字2’。` 的句式，以强约束生图模型。
    - **分镜标签禁忌**：按照分镜设计进行生成，不带任何“分镜”字样的标签。
    - **文字位置**：所有的文字都要在画面中出现，不要在图片中单独额外开出一块空白的位置来专门放置文字。
  - **并发执行**：使用并发调用生成图片，严格按照规范保存至 `assets/image/`。
- **音频生成 (Audio Generation)**：
  - 参考 `page_design` 中的剧情文本和其他信息，从剧情旁白的视角来构思音频内容（注意：绝对不是人物对白的内容本身！），让听众仅仅通过音频信息就能了解客观的剧情推进，稍微详细一些，不要过于简略。并发调用语音生成工具，分配合适的音色，保存至 `assets/audio/`。

### 第四步：数据与代码集成 (Data & Code Integration) —— **【交互与界面设计核心】**

在编写 `App.tsx`、`App.css` 和 `storyData.ts` 时，必须严格遵循以下交互设计规范和 `lingguang-miniapp-meta-skill` 的硬性约束。

#### 1. 数据结构 (`data/storyData.ts`)
数据文件必须清晰定义每一页的属性，方便组件渲染和预加载：
```typescript
export interface StoryPage {
  id: number;
  title: string;
  narration: string;
  image: string;
  audio: string;
}

export const storyPages: StoryPage[] = [
  {
    id: 1,
    title: "第一章 标题",
    narration: "旁白内容...",
    image: "page_1_1.png",
    audio: "page_1_1.mp3"
  },
  // ...
];
```

#### 2. 纯黑沉浸式 UI 设计 (`App.css`)
- **不需要任何 `main-bg` 背景图片**。漫画应用的核心是突出漫画图片本身。
- **纯黑背景**：将 `body`, `#root`, `.app`, `.main-page`, `.story-image-container` 的背景全部设置为 `#000`。
- **安全区配置（Meta Skill 强制要求）**：必须在 CSS 中使用 `env(safe-area-inset-*)`。
```css
body, .app, .main-page, .story-image-container {
  background: #000;
  margin: 0;
  padding: 0;
  overflow: hidden;
}

/* 必须包含的安全区配置 */
#container {
  padding-top: env(safe-area-inset-top, 0px);
  padding-bottom: env(safe-area-inset-bottom, 0px);
}

/* 翻页动画 */
.slide-up { animation: slideUpAnim 0.4s ease-out forwards; }
.slide-down { animation: slideDownAnim 0.4s ease-out forwards; }
@keyframes slideUpAnim {
  0% { transform: translateY(100%); opacity: 0; }
  100% { transform: translateY(0); opacity: 1; }
}
@keyframes slideDownAnim {
  0% { transform: translateY(-100%); opacity: 0; }
  100% { transform: translateY(0); opacity: 1; }
}
```

#### 3. 交互逻辑与组件实现 (`App.tsx`)
- **Meta Skill 强制要求**：
  - 必须保留 `<div id="safe-area"></div>`。
  - 图片必须通过 `import` 静态导入，不能直接写字符串路径。
  - 音频必须使用 `import { Howl } from '@/lib/audio'`，**绝对禁止**使用原生 `<audio>` 或 `new Audio()`。
- **手势滑动翻页**：通过监听 `onTouchStart`, `onTouchMove`, `onTouchEnd` 实现上下滑动翻页。向上滑动（看下一页），向下滑动（看上一页）。
- **点击显示/隐藏控制栏**：点击屏幕任意位置切换底部控制栏的显示状态。
- **章节与页面跳转**：如果是多章节合集，底部控制栏应包含一个滑动条（Slider）或跳转菜单，方便用户在章节间和章节内部快速跳转。

**核心代码结构示例**：
```tsx
import './App.css'
import { useState, useEffect, useRef } from 'react'
import { storyPages } from './data/storyData'
import { Howl } from '@/lib/audio'

// 必须静态导入图片
import img1 from './assets/image/page_1_1.png'
const imageMap: Record<string, string> = { 'page_1_1.png': img1 }

function App() {
  const [currentPage, setCurrentPage] = useState(0)
  const [showControls, setShowControls] = useState(false)
  const [slideDirection, setSlideDirection] = useState<'up' | 'down' | null>(null)
  
  const touchStartY = useRef(0)
  const touchEndY = useRef(0)
  const soundRef = useRef<Howl | null>(null)

  // 手势滑动逻辑
  const handleTouchStart = (e: React.TouchEvent) => {
    touchStartY.current = e.targetTouches[0].clientY
  }
  const handleTouchMove = (e: React.TouchEvent) => {
    touchEndY.current = e.targetTouches[0].clientY
  }
  const handleTouchEnd = () => {
    if (!touchStartY.current || !touchEndY.current) return
    const distance = touchStartY.current - touchEndY.current
    if (distance > 50) {
      // 向上滑动 -> 下一页
      if (currentPage < storyPages.length - 1) {
        setSlideDirection('up')
        setCurrentPage(prev => prev + 1)
      }
    } else if (distance < -50) {
      // 向下滑动 -> 上一页
      if (currentPage > 0) {
        setSlideDirection('down')
        setCurrentPage(prev => prev - 1)
      }
    }
    touchStartY.current = 0; touchEndY.current = 0;
  }

  // 音频播放逻辑 (必须使用 Howl)
  useEffect(() => {
    // 停止上一个音频
    if (soundRef.current) soundRef.current.stop()
    
    // 播放当前音频 (假设音频也已静态导入或通过特定方式加载)
    // soundRef.current = new Howl({ src: [audioMap[storyPages[currentPage].audio]] })
    // soundRef.current.play()
    
    return () => { if (soundRef.current) soundRef.current.stop() }
  }, [currentPage])

  return (
    <div className="app">
      {/* ⚠️ 严禁删除此节点！ */}
      <div id="safe-area"></div>
      
      <div 
        className="main-page" 
        onClick={() => setShowControls(!showControls)}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
      >
        <div className="scroll-content">
          <div key={currentPage} className={`slide-container ${slideDirection ? 'slide-' + slideDirection : ''}`}>
            <div className="story-image-container">
              <img 
                src={imageMap[storyPages[currentPage].image]} 
                alt={storyPages[currentPage].title}
                className="story-image"
              />
            </div>
          </div>
        </div>

        {/* 底部控制栏 */}
        <div className={`control-bar ${showControls ? "" : "hidden"}`} onClick={(e) => e.stopPropagation()}>
          {/* 在此处实现章节跳转 Slider、播放/暂停按钮等 */}
        </div>
      </div>
    </div>
  )
}

export default App
```

### 第五步：审查与修正 (Review & Refine)
- **视觉审查**：检查生成的图片是否符合年代设定，人物特征是否连贯，分镜排版是否自然。
- **UI审查**：确保纯黑沉浸模式生效，无多余背景图；上下滑动翻页手势与动画顺畅；控制栏状态正确。
- **合规审查**：严格对照 `lingguang-miniapp-meta-skill`，检查是否保留了 `#safe-area`，是否使用了 `Howl` 播放音频，是否静态导入了图片。

## 3. 核心要素与执行纪律总结
1. **强设定的贯彻**：`character_design.txt` 是贯穿所有生图Prompt的“视觉契约”。
2. **漫画叙事感**：通过 2:3 的大图、中间页面的多宫格分镜、以及直接融入画面的对白和旁白，打造沉浸式的漫画阅读体验。
3. **极致的沉浸交互**：**纯黑背景，无需 `main-bg`**。引入上下滑动翻页手势与平滑动画，底部采用极简单行控制栏，将舞台完全交给漫画本身。
4. **坚决的工程执行**：严格遵守灵光小程序元规范（Meta Skill），特别是安全区、音频播放和资源导入的硬性约束。