---
description: 灵光小程序 API：API_GAME_ASSETS。与 PRD「所需 API 技能」对齐后按需激活。游戏素材库
---

# 游戏素材库

## 素材库
- 链接：[https://mdn.alipayobjects.com/asap_serivce/uri/file/as/20260120_material]
- 列表：

| 素材类型 | 中文名 | 素材数量 |
|-----|-------|-----|
| images | 2026 | 3 |
| images | 爆竹 | 10 |
| images | 灯笼 | 6 |
| images | 福袋 | 2 |
| images | 福字 | 2 |
| images | 红包 | 1 |
| images | 金币 | 2 |
| images | 烟花 | 7 |
| images | 中国结 | 8 |
| model | 爆竹 | 4 |
| model | 窗花 | 4 |
| model | 春联 | 4 |
| model | 灯笼 | 8 |
| model | 福字 | 3 |
| model | 红包 | 5 |
| model | 饺子 | 3 |
| model | 烟花 | 4 |
| model | 中国结 | 5 |
| skybox | 山脉 | 4 |
| skybox | 天空 | 2 |

## 触发条件：

- 3D model/2D images：当用户需求中没有明确说明（特指用图片做贴图使用）时，需要**优先使用 3D 模型**。本素材库提供了高质量的 3D 模型（.glb 格式），能够大幅提升视觉效果和用户体验。3D 模型支持旋转、缩放、动画等交互，相比 2D 图片更有立体感和节日氛围。所有 3D 模型已经过极致压缩优化（256纹理 + WebP + 无mesh压缩），**零依赖、无需任何 decoder**，体积极小（~1MB/文件）、加载快、兼容性强。只有在某个类别没有提供 3D 模型时，才降级使用 2D 图片

## 素材获取方式

开发环境中已经集成了素材，如需使用素材，需要在对应的代码文件中引入，示例如下：

### Manifest 地址

```typescript
// 极致压缩版（推荐）：零依赖、无需 decoder、~1MB/文件
const MANIFEST_URL = '//mdn.alipayobjects.com/asap_serivce/uri/file/as/20260120_material/manifest.json'
```

### 获取素材清单

```typescript
interface AssetItem {
  id: string;
  file: string;
}

interface SkyboxFiles {
  px: string; // Right
  nx: string; // Left
  py: string; // Top
  ny: string; // Bottom
  pz: string; // Front
  nz: string; // Back
}

interface SkyboxItem {
  id: string;
  files: SkyboxFiles;
}

interface Assets {
  images: Record<string, AssetItem[]>;
  models: Record<string, AssetItem[]>;
  skyboxes: Record<string, SkyboxItem[]>;
}

interface Manifest {
  base_url: string;
  version: string;
  assets: Assets;
}

// 获取素材清单
const manifest: Manifest = await fetch(MANIFEST_URL).then(r => r.json())
```

### 随机获取素材（推荐方法）

> **💡 提示**：这里有一个包含了 3D 模型渲染、2D 图片降级、加载天空盒、loading 状态等逻辑的代码示例。

```typescript
/**
 * 辅助函数：从数组中随机获取一个元素。
 * @param arr - 目标数组
 * @returns 数组中的一个随机元素，如果数组为空或未定义，则返回 undefined。
 */
function getRandomItemFromArray<T>(arr: T[] | undefined): T | undefined {
  if (!arr || arr.length === 0) {
    return undefined;
  }
  return arr[Math.floor(Math.random() * arr.length)];
}

/**
 * 辅助函数：根据 manifest 和文件相对路径构建完整的 URL。
 * @param manifest - 资源清单对象
 * @param file - 文件的相对路径
 * @returns 完整的资源 URL
 */
function buildAssetUrl(manifest: Manifest, file: string): string {
  return manifest.base_url + file;
}

// =================================================================
// 主要功能函数
// =================================================================

// 优先获取 3D 模型，没有则降级到 2D 图片
function getRandomAsset(manifest: Manifest, category: string): { type: '3d' | '2d', url: string } | null {
  // 1. 优先尝试获取 3D 模型
  const models = manifest.assets.models[category];
  const randomModel = getRandomItemFromArray(models);
  if (randomModel) {
    return {
      type: '3d',
      url: buildAssetUrl(manifest, randomModel.file)
    };
  }

  // 2. 降级到 2D 图片
  const images = manifest.assets.images[category];
  const randomImage = getRandomItemFromArray(images);
  if (randomImage) {
    return {
      type: '2d',
      url: buildAssetUrl(manifest, randomImage.file)
    };
  }

  return null;
}

// 随机选择一个有 3D 模型的类别
function getRandomCategoryWith3D(manifest: Manifest): string | null {
  // 直接从 manifest.assets.models 中筛选出包含有效模型的类别
  const categoriesWithModels = Object.keys(manifest.assets.models)
    .filter(key => manifest.assets.models[key]?.length > 0);
  // 使用辅助函数从有效类别列表中随机选择一个
  return getRandomItemFromArray(categoriesWithModels) ?? null;
}
// 仅用于特殊场景：获取 2D 图片
function getRandomImage(manifest: Manifest, category: string): string | null {
  const images = manifest.assets.images[category];
  const randomItem = getRandomItemFromArray(images);
  // 使用三元运算符简化返回逻辑
  return randomItem ? buildAssetUrl(manifest, randomItem.file) : null;
}
// 仅用于特殊场景：获取 3D 模型
function getRandomModel(manifest: Manifest, category: string): string | null {
  const models = manifest.assets.models[category];
  const randomItem = getRandomItemFromArray(models);
  return randomItem ? buildAssetUrl(manifest, randomItem.file) : null;
}

interface SkyboxAsset {
  id: string;
  urls: SkyboxFiles;
}
// 处理 Skybox（非必须使用，根据实际需求决定）
function getRandomSkybox(manifest: Manifest, category: string): SkyboxAsset | null {
  const skyboxes = manifest.assets.skyboxes[category];
  const randomItem = getRandomItemFromArray(skyboxes);

  if (!randomItem) {
    return null;
  }
  
  const urls: Partial<SkyboxFiles> = {};
  for (const key in randomItem.files) {
      const face = key as keyof SkyboxFiles;
      urls[face] = buildAssetUrl(manifest, randomItem.files[face]);
  }

  return {
    id: randomItem.id,
    urls: urls as SkyboxFiles
  };
}
```

## 使用示例

### 完整示例1：优先使用 3D 模型的春节红包应用（推荐）

这是一个**完整的、可直接使用的示例**，展示如何同时支持 3D 模型和 2D 图片，自动优先选择 3D 模型。

```tsx
import { useState, useEffect, useRef } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

// --- 1. 类型定义与常量 (根据新结构更新) ---

const MANIFEST_URL = '//mdn.alipayobjects.com/asap_serivce/uri/file/as/20260120_material/manifest.json'

// 新的结构体定义
interface AssetItemDef {
  id: string;
  file: string;
}

interface Assets {
  images: Record<string, AssetItemDef[]>;
  models: Record<string, AssetItemDef[]>;
  // skyboxes 暂未使用，但类型已定义
  skyboxes: Record<string, AssetItemDef[]>;
}

interface Manifest {
  base_url: string;
  version: string;
  assets: Assets;
}

// 组件内部使用的，描述一个被选中的素材
interface SelectedAsset {
  type: '3d' | '2d'
  url: string
  category: string
}

// --- 2. 独立的辅助函数 (逻辑解耦) ---

/**
 * 辅助函数：从数组中随机获取一个元素。
 */
function getRandomItemFromArray<T>(arr: T[] | undefined): T | undefined {
  if (!arr || arr.length === 0) return undefined;
  return arr[Math.floor(Math.random() * arr.length)];
}

/**
 * 随机获取一个素材（优先 3D 模型）。
 * @param manifest - 资源清单对象
 * @returns 一个被选中的素材对象，或在找不到时返回 null。
 */
function getRandomAsset(manifest: Manifest): SelectedAsset | null {
  // 过滤出有 3D 模型的类别
  const categoriesWithModels = Object.keys(manifest.assets.models)
    .filter(key => manifest.assets.models[key]?.length > 0);

  if (categoriesWithModels.length === 0) {
    // 如果没有任何类别有 3D 模型，可以考虑降级到所有 2D 类别
    // (此处为简化，直接返回 null)
    return null;
  }
  
  // 随机选择一个有 3D 模型的类别
  const randomCategory = getRandomItemFromArray(categoriesWithModels)!; // `!` 是安全的，因为已检查长度
  const modelItem = getRandomItemFromArray(manifest.assets.models[randomCategory]);

  if (modelItem) {
    return {
      type: '3d',
      url: manifest.base_url + modelItem.file,
      category: randomCategory,
    };
  }
  
  // 理论上不会到这里，但作为兜底，可以从同一类别尝试获取 2D 图片
  const imageItem = getRandomItemFromArray(manifest.assets.images[randomCategory]);
  if (imageItem) {
    return {
      type: '2d',
      url: manifest.base_url + imageItem.file,
      category: randomCategory,
    };
  }

  return null;
}

// --- 3. React 组件 ---

function SpringFestivalApp() {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [currentAsset, setCurrentAsset] = useState<SelectedAsset | null>(null);
  const [showAsset, setShowAsset] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  // 3D 相关 refs
  const containerRef = useRef<HTMLDivElement>(null);
  const animationRef = useRef<number | null>(null);

  // 加载素材清单
  useEffect(() => {
    fetch(MANIFEST_URL)
      .then(r => r.json())
      .then(data => setManifest(data))
      .catch(err => console.error('Failed to load manifest:', err));
  }, []);
  
  // 初始化 3D 场景
  useEffect(() => {
    if (!showAsset || !containerRef.current || !currentAsset || currentAsset.type !== '3d') {
      return;
    }
    
    // 创建场景
    const scene = new THREE.Scene();

    // 创建相机
    const camera = new THREE.PerspectiveCamera(75, 1, 0.1, 1000);
    camera.position.z = 3;

    // 创建渲染器
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(300, 300);
    renderer.setPixelRatio(window.devicePixelRatio); // 适配高清屏
    containerRef.current.appendChild(renderer.domElement);
    
    // 添加光源
    scene.add(new THREE.AmbientLight(0xffffff, 0.8));
    const directionalLight = new THREE.DirectionalLight(0xffffff, 10);
    directionalLight.position.set(5, 5, 0);
    directionalLight.castShadow = true
    scene.add(directionalLight);

    let model: THREE.Object3D | null = null;
    const loader = new GLTFLoader();

    // 加载 3D 模型
    setIsLoading(true);
    loader.load(
      currentAsset.url,
      (gltf) => {
        model = gltf.scene;
        // 自动计算模型的边界和中心，并进行缩放和居中
        const box = new THREE.Box3().setFromObject(model);
        const size = box.getSize(new THREE.Vector3()).length();
        const center = box.getCenter(new THREE.Vector3());
        
        model.position.x += (model.position.x - center.x);
        model.position.y += (model.position.y - center.y);
        model.position.z += (model.position.z - center.z);
        
        camera.position.z = size * 1.2; // 根据模型大小调整相机距离
        camera.lookAt(0, 0, 0);

        scene.add(model);
        setIsLoading(false);
        
        // **启动动画循环**
        animate(); 
      },
      undefined,
      (error) => {
        console.error('Failed to load 3D model:', error);
        setIsLoading(false);
      }
    );
    
    // 动画循环函数
    const animate = () => {
      animationRef.current = requestAnimationFrame(animate);
      if (model) {
         model.rotation.y += 0.01;
         model.rotation.x = Math.sin(Date.now() * 0.001) * 0.1;
      }
      renderer.render(scene, camera);
    }
    
    // Cleanup
    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
      // 安全地清理 DOM 和 Three.js 对象
      if (containerRef.current) {
        containerRef.current.innerHTML = '';
      }
      renderer.dispose();
      scene.clear();
    };
  }, [showAsset, currentAsset]);

  // 点击红包事件
  const handleClick = () => {
    if (!manifest) return; // 确保 manifest 已加载
    
    const asset = getRandomAsset(manifest);
    if (!asset) return;

    setCurrentAsset(asset);
    setShowAsset(true);

    // 4 秒后自动关闭
    setTimeout(() => {
      setShowAsset(false);
    }, 4000);
  };

  // --- JSX (与之前相同，无需修改) ---
  return (
    <div className="min-h-screen bg-gradient-to-b from-red-50 to-yellow-50 flex flex-col items-center justify-center p-4">
      <h1 className="text-4xl font-bold text-red-600 mb-8">🧧 春节红包 🧧</h1>

      <button
        onClick={handleClick}
        disabled={showAsset || !manifest} // 在 manifest 加载完成前禁用按钮
        className="w-48 h-64 bg-gradient-to-b from-red-500 to-red-700 rounded-2xl shadow-2xl flex flex-col items-center justify-center cursor-pointer hover:scale-105 active:scale-95 transition-transform border-4 border-yellow-400 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <div className="w-20 h-20 bg-gradient-to-b from-yellow-300 to-yellow-500 rounded-full flex items-center justify-center shadow-lg">
          <span className="text-4xl">福</span>
        </div>
        <div className="mt-4 text-yellow-300 text-xl font-bold">{manifest ? '点击开启' : '加载中...'}</div>
      </button>

      {showAsset && currentAsset && (
        <div className="fixed inset-0 flex items-center justify-center bg-black bg-opacity-30 z-50">
          <div className="bg-white rounded-2xl p-8 shadow-2xl animate-fade-in-scale">
            {currentAsset.type === '2d' ? (
              <img src={currentAsset.url} alt="春节元素" className="w-64 h-64 object-contain" />
            ) : (
              <div className="relative">
                <div ref={containerRef} className="w-[300px] h-[300px]" />
                {isLoading && (
                  <div className="absolute inset-0 flex items-center justify-center bg-white bg-opacity-80">
                    <div className="text-lg text-gray-600">加载中...</div>
                  </div>
                )}
              </div>
            )}
            <p className="mt-4 text-center text-2xl font-bold text-red-600">
              恭喜发财，大吉大利！
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default SpringFestivalApp
```

### 完整示例2：使用天空盒

```tsx
// 这是一个完整的、可直接使用的天空盒（Skybox）示例。
// 它演示了如何根据新的 manifest 结构加载天空盒资源，
// 并将其应用到 Three.js 场景中作为背景和环境反射贴图。

import React, { useState, useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

// --- 1. 类型定义与常量 (与新结构完全匹配) ---

const MANIFEST_URL = '//mdn.alipayobjects.com/asap_serivce/uri/file/as/20260120_material/manifest.json';

// 和之前任务中推导出的结构体保持一致
interface AssetItemDef {
  id: string;
  file: string;
}

interface SkyboxFiles {
  px: string; // Right
  nx: string; // Left
  py: string; // Top
  ny: string; // Bottom
  pz: string; // Front
  nz: string; // Back
}

interface SkyboxItem {
  id: string;
  files: SkyboxFiles;
}

interface Assets {
  images: Record<string, AssetItemDef[]>;
  models: Record<string, AssetItemDef[]>;
  skyboxes: Record<string, SkyboxItem[]>;
}

interface Manifest {
  base_url: string;
  version: string;
  assets: Assets;
}

// --- 2. 独立的辅助函数 ---

/**
 * 辅助函数：从数组中随机获取一个元素。
 */
function getRandomItemFromArray<T>(arr: T[] | undefined): T | undefined {
  if (!arr || arr.length === 0) return undefined;
  return arr[Math.floor(Math.random() * arr.length)];
}

/**
 * 随机获取一个天空盒资源。
 * @param manifest - 资源清单对象
 * @returns 一个随机的天空盒对象，或在找不到时返回 null。
 */
function getRandomSkybox(manifest: Manifest): SkyboxItem | null {
  const categories = Object.keys(manifest.assets.skyboxes)
    .filter(key => manifest.assets.skyboxes[key]?.length > 0);

  if (categories.length === 0) return null;

  const randomCategory = getRandomItemFromArray(categories)!;
  return getRandomItemFromArray(manifest.assets.skyboxes[randomCategory]) ?? null;
}

// --- 3. React 组件 ---

function SkyboxViewerApp() {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [currentSkybox, setCurrentSkybox] = useState<SkyboxItem | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const canvasRef = useRef<HTMLDivElement>(null);

  // 加载素材清单
  useEffect(() => {
    fetch(MANIFEST_URL)
      .then(res => res.json())
      .then(data => {
        setManifest(data);
        // 首次加载时自动选择一个天空盒
        setCurrentSkybox(getRandomSkybox(data));
      })
      .catch(err => console.error('Failed to load manifest:', err));
  }, []);

  // 核心：Three.js 场景设置与天空盒加载
  useEffect(() => {
    if (!currentSkybox || !canvasRef.current || !manifest) return;

    setIsLoading(true);
    const container = canvasRef.current;

    // 场景
    const scene = new THREE.Scene();

    // 相机
    const camera = new THREE.PerspectiveCamera(75, container.clientWidth / container.clientHeight, 0.1, 1000);
    camera.position.z = 5;

    // 渲染器
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(window.devicePixelRatio);
    container.appendChild(renderer.domElement);

    // 控制器 (允许用户拖动视角)
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true; // 增加拖拽惯性

    // 添加一个反光球体来展示环境反射
    const sphereGeometry = new THREE.SphereGeometry(1, 32, 32);
    const sphereMaterial = new THREE.MeshStandardMaterial({
      metalness: 1.0, // 100% 金属
      roughness: 0.1, // 非常光滑
    });
    const sphere = new THREE.Mesh(sphereGeometry, sphereMaterial);
    scene.add(sphere);
    
    // 加载天空盒
    const loader = new THREE.CubeTextureLoader();
    const { base_url } = manifest;
    const { files } = currentSkybox;
    
    // **注意：URL 顺序必须是 [px, nx, py, ny, pz, nz]**
    const urls = [
      base_url + files.px, // right
      base_url + files.nx, // left
      base_url + files.py, // top
      base_url + files.ny, // bottom
      base_url + files.pz, // front
      base_url + files.nz, // back
    ];

    loader.load(urls, (cubeTexture) => {
      // 将天空盒应用为场景背景
      scene.background = cubeTexture;
      
      // 同时应用为环境贴图，这样 PBR 材质才能反射天空
      scene.environment = cubeTexture;

      setIsLoading(false);
    });

    // 动画循环
    const animate = () => {
      requestAnimationFrame(animate);
      controls.update(); // 更新控制器
      renderer.render(scene, camera);
    };
    animate();

    // 处理窗口大小变化
    const handleResize = () => {
      camera.aspect = container.clientWidth / container.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    };
    window.addEventListener('resize', handleResize);

    // 清理函数
    return () => {
      window.removeEventListener('resize', handleResize);
      container.removeChild(renderer.domElement);
      renderer.dispose();
      controls.dispose();
    };
  }, [currentSkybox, manifest]);

  // 点击按钮切换天空盒
  const handleSwitchSkybox = () => {
    if (!manifest) return;
    setCurrentSkybox(getRandomSkybox(manifest));
  };

  return (
    <div className="w-full h-screen flex flex-col items-center justify-center bg-gray-900 text-white p-4">
      <div className="absolute top-4 left-4 z-10">
        <h1 className="text-2xl font-bold">天空盒查看器</h1>
        <p className="text-gray-400">当前天空盒 ID: {currentSkybox?.id ?? '加载中...'}</p>
        <button
          onClick={handleSwitchSkybox}
          disabled={isLoading || !manifest}
          className="mt-2 px-4 py-2 bg-blue-600 rounded hover:bg-blue-700 disabled:bg-gray-500 transition-colors"
        >
          {isLoading ? '加载中...' : '随机切换天空盒'}
        </button>
      </div>

      {isLoading && (
        <div className="absolute inset-0 flex items-center justify-center z-20 bg-black bg-opacity-50">
          <p className="text-xl">正在加载天空盒...</p>
        </div>
      )}

      <div ref={canvasRef} className="w-full h-full" />
    </div>
  );
}

export default SkyboxViewerApp;
```


**关键点说明**：

1. ✅ **优先选择有 3D 模型的分类**：使用 `categoriesWithModels` 过滤
2. ✅ **根据类型渲染**：2D 用 `<img>`，3D 用 Three.js 场景
3. ✅ **正确的 useEffect 依赖**：只依赖 `[showAsset, manifest, currentAsset]`
4. ✅ **添加 loading 状态**：3D 模型加载时显示提示
5. ✅ **正确的 cleanup**：清理动画、模型、渲染器


## ⚠️ 常见错误及避免方法

### 错误 1: 选择了没有 3D 模型的分类 ❌

**错误代码**:
```typescript
// ❌ 错误：硬编码的列表中可能包含没有 3D 模型的分类
const categories = ['灯笼', '福字', '爆竹', '金币']; // 假设 "金币" 在 manifest.assets.models 中不存在
const randomCategory = categories[Math.floor(Math.random() * categories.length)];

const models = manifest.assets.models[randomCategory]; 
const randomModel = models[Math.floor(Math.random() * models.length)];
```

**正确做法**:
```typescript
// ✅ 正确做法 1: 从一个你感兴趣的列表中进行安全筛选

// 1. 定义你感兴趣的分类列表
const desiredCategories = ['灯笼', '福字', '爆竹', '金币'];

// 2. 使用 .filter() 筛选出真正包含 3D 模型的分类
const safeCategories = desiredCategories.filter(category => {
  // 检查该分类是否存在于 models 对象中，并且其数组长度大于 0
  const models = manifest.assets.models[category];
  return models && models.length > 0;
});

// 3. 安全检查：如果筛选后列表为空，则直接返回或处理
if (safeCategories.length === 0) {
  console.warn('在指定的分类中，没有找到任何 3D 模型。');
  return; // or handle this case
}

// 4. 从安全列表中随机选择一个分类
const randomCategory = safeCategories[Math.floor(Math.random() * safeCategories.length)];

// 5. 安全地访问数据（此时 models 必定是存在的非空数组）
const models = manifest.assets.models[randomCategory];
const randomModel = models[Math.floor(Math.random() * models.length)];

console.log(`成功从分类 "${randomCategory}" 中获取模型:`, randomModel.id);
```

### 错误 2: useEffect 依赖项导致场景重复创建 ❌

**错误代码**:
```typescript
const [modelType, setModelType] = useState('')

// ❌ 错误：modelType 变化会导致 useEffect 重新执行，销毁并重建场景
useEffect(() => {
  // 创建场景、相机、渲染器
  const scene = new THREE.Scene()
  // ...

  // 加载 3D 模型（异步）
  loader.load(modelUrl, (gltf) => {
    scene.add(gltf.scene)  // 可能场景已经被销毁了！
  })

  return () => {
    renderer.dispose()  // cleanup 时销毁场景
  }
}, [show3D, modelType])  // ❌ modelType 不应该在这里
```

**问题**：每次 `setModelType()` 都会触发 useEffect 重新执行，导致：
1. cleanup 函数执行，销毁旧场景
2. 创建新场景
3. 异步加载新模型
4. 但模型可能还没加载完，场景又被销毁了（用户看不到）

**正确做法**:
```typescript
// ✅ 正确：不要把会频繁变化的 state 放在依赖数组中
useEffect(() => {
  if (!show3D || !containerRef.current || !manifest) return

  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(75, 1, 0.1, 1000)
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  // ...

  // 加载模型的逻辑
  loadRandomModel()

  return () => {
    renderer.dispose()
  }
}, [show3D, manifest])  // ✅ 只依赖 show3D 和 manifest
```

### 错误 3: 没有添加加载状态 ❌

**问题**：3D 模型加载是异步的，可能需要几秒钟。如果没有 loading 提示，用户会看到空白场景。

**正确做法**:
```typescript
const [isModelLoading, setIsModelLoading] = useState(false)

const loadModel = () => {
  setIsModelLoading(true)  // ✅ 开始加载

  loader.load(
    modelUrl,
    (gltf) => {
      scene.add(gltf.scene)
      setIsModelLoading(false)  // ✅ 加载完成
    },
    undefined,
    (error) => {
      console.error('Failed to load model:', error)
      setIsModelLoading(false)  // ✅ 加载失败也要重置
    }
  )
}

// UI 中显示 loading
{isModelLoading && <div>加载中...</div>}
```

### 错误 4: cleanup 时没有清理模型 ❌

**问题**：忘记在 useEffect 的 cleanup 函数中清理模型，会导致内存泄漏。

**正确做法**:
```typescript
useEffect(() => {
  const scene = new THREE.Scene()
  const renderer = new THREE.WebGLRenderer()
  let model: THREE.Object3D | null = null

  loader.load(modelUrl, (gltf) => {
    model = gltf.scene
    scene.add(model)
  })

  return () => {
    // ✅ 清理模型
    if (model) {
      scene.remove(model)
      model = null
    }
    renderer.dispose()
  }
}, [show3D])
```

## 注意事项

1. **图片格式为 PNG/JPG**，带透明通道，可直接叠加使用
2. **素材随机选择**应在运行时进行，确保每次访问展示不同素材
3. **不要硬编码素材 URL**，始终通过 manifest 获取，便于后续更新
4. **优先使用 3D 模型**，只在分类没有 3D 模型时才使用 2D 图片
5. **过滤分类时检查 models.length > 0**，避免选到没有 3D 模型的分类
6. **useEffect 依赖数组要谨慎**，避免不必要的场景重建
7. **添加 loading 状态**，提升用户体验
