---
description: 灵光小程序 API：API_MAP。与 PRD「所需 API 技能」对齐后按需激活。地图
---

# 高德地图API说明

开发环境中提供了完整的高德地图组件（JS API 2.0），包含官方插件，可以用来实现用户地图相关的应用
所有API均遵循官方文档规范，可以直接参考官方文档进行开发使用，以下是一些示例供参考

## 地图加载方法定义
```typescript
  /**
   * 高德地图加载器（全局函数）
   * 封装了高德地图的加载逻辑，包括安全配置、脚本动态加载和 AMapLoader 初始化
   * @returns Promise<AMap> 返回 AMap 实例
   * @example
   * loadMap().then((AMap) => {
   *   const map = new AMap.Map('container');
   * });
   */
  function loadMap(): Promise<AMap>;
```

## 基本用法
```typescript
async function initMap() {
  try {
    const AMap = await loadMap();
    const map = new AMap.Map('container');
    // 使用地图...
  } catch (error) {
    console.error('地图加载失败:', error);
  }
}
```

## 在 React 组件中使用
```typescript
import { useEffect, useRef } from 'react';

function MapComponent() {
  const mapRef = useRef<any>(null);

  useEffect(() => {
    loadMap()
      .then((AMap) => {
        mapRef.current = new AMap.Map('map-container');
      })
      .catch(console.error);

    return () => {
      // 清理地图实例
      if (mapRef.current) {
        mapRef.current.destroy();
      }
    };
  }, []);

  return <div id="map-container" className="w-full h-[400px]" />;
}
```

## 使用插件示例

高德地图提供了丰富的插件功能，以下以 `AMap.PlaceSearch` 地点搜索插件为例：

### 方式一：使用 panel 参数（插件自动渲染）

如果希望插件自动将搜索结果渲染到指定容器中，可以使用 `panel` 参数：

```typescript
import { useEffect, useRef } from 'react';

function MapWithPlaceSearch() {
  const mapRef = useRef<any>(null);
  const placeSearchRef = useRef<any>(null);

  useEffect(() => {
    loadMap()
      .then((AMap) => {
        // 初始化地图
        mapRef.current = new AMap.Map('map-container', {
          zoom: 13,
          center: [116.397428, 39.90923] // 北京天安门
        });

        // 加载 PlaceSearch 插件
        AMap.plugin('AMap.PlaceSearch', () => {
          // 创建地点搜索实例
          placeSearchRef.current = new AMap.PlaceSearch({
            city: '北京市', // 搜索城市
            citylimit: true, // 是否限制在当前城市
            pageSize: 10, // 每页显示结果数
            pageIndex: 1, // 当前页数
            map: mapRef.current, // 地图实例
            panel: 'panel' // 结果面板容器ID（插件会自动渲染结果到此容器）
          });

          // 搜索地点
          placeSearchRef.current.search('天安门', (status: string, result: any) => {
            if (status === 'complete' && result.info === 'OK') {
              console.log('搜索成功:', result.poiList);
            } else {
              console.error('搜索失败:', result);
            }
          });
        });
      })
      .catch(console.error);

    return () => {
      // 清理资源
      if (placeSearchRef.current) {
        placeSearchRef.current.destroy();
      }
      if (mapRef.current) {
        mapRef.current.destroy();
      }
    };
  }, []);

  return (
    <div>
      <div id="map-container" className="w-full h-[400px]" />
      <div id="panel" className="w-full h-[200px] overflow-auto" />
    </div>
  );
}
```

### 方式二：使用 React 状态管理（推荐）

如果希望使用 React 状态管理来渲染搜索结果，**不要设置 `panel` 参数**，从回调中获取结果并更新状态：

```typescript
import { useEffect, useRef, useState } from 'react';

function MapWithPlaceSearch() {
  const mapRef = useRef<any>(null);
  const placeSearchRef = useRef<any>(null);
  const [searchResults, setSearchResults] = useState<any[]>([]);

  useEffect(() => {
    loadMap()
      .then((AMap) => {
        // 初始化地图
        mapRef.current = new AMap.Map('map-container', {
          zoom: 13,
          center: [116.397428, 39.90923] // 北京天安门
        });

        // 加载 PlaceSearch 插件
        AMap.plugin('AMap.PlaceSearch', () => {
          // 创建地点搜索实例（不设置 panel，避免插件直接操作 DOM）
          placeSearchRef.current = new AMap.PlaceSearch({
            city: '北京市',
            citylimit: true,
            pageSize: 10,
            pageIndex: 1,
            map: mapRef.current
            // 注意：不设置 panel 参数
          });

          // 搜索地点
          placeSearchRef.current.search('天安门', (status: string, result: any) => {
            if (status === 'complete' && result.info === 'OK') {
              // 从 result.poiList.pois 中提取搜索结果
              const pois = result.poiList?.pois || [];
              setSearchResults(pois);
            } else {
              console.error('搜索失败:', result);
            }
          });
        });
      })
      .catch(console.error);

    return () => {
      if (placeSearchRef.current) {
        placeSearchRef.current.destroy();
      }
      if (mapRef.current) {
        mapRef.current.destroy();
      }
    };
  }, []);

  return (
    <div>
      <div id="map-container" className="w-full h-[400px]" />
      <div className="mt-2.5">
        {searchResults.map((poi, index) => (
          <div key={index}>{poi.name} - {poi.address}</div>
        ))}
      </div>
    </div>
  );
}
```

**注意**：如果设置了 `panel` 参数，插件会直接操作 DOM 渲染结果，可能会与 React 的渲染产生冲突。建议在 React 应用中使用方式二。
