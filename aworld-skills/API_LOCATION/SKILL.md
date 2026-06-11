---
description: 灵光小程序 API：API_LOCATION。与 PRD「所需 API 技能」对齐后按需激活。位置能力
---

# window.lingguang.getLocation

获取设备当前的地理位置信息，包括经纬度、国家、省份、城市等信息。可选择是否返回附近的POI（兴趣点）信息。

## 函数签名

```typescript
window.lingguang.getLocation(options?: {
  poi?: boolean;
}): Promise<{
  longitude: number;
  latitude: number;
  country?: string;
  countryCode?: string;
  province?: string;
  provinceCode?: string;
  city?: string;
  cityCode?: string;
  district?: string;
  districtCode?: string;
  pois?: Array<{
    name: string;
    address: string;
  }>;
}>
```

## 参数

**options** (Object): 配置对象

- **poi** (boolean, 可选): 是否返回完整位置信息（包括国家、省份、城市、区县和POI），默认值为 `false`
  - `false`: 只返回经纬度
  - `true`: 返回完整的位置信息，包括国家、省份、城市、区县和 POI 列表

## 返回值

返回 Promise，成功时 resolve，失败时 reject。

**成功时（resolve）返回：**

**当 `poi` 参数为 `false` 时（默认）：**

```javascript
{
  longitude: 116.397128,  // 经度（number）
  latitude: 39.916527     // 纬度（number）
}
```

**当 `poi` 参数为 `true` 时：**

```javascript
{
  longitude: 116.397128,  // 经度（number）
  latitude: 39.916527,    // 纬度（number）
  country: '中国',        // 国家名称（string）
  countryCode: 'CN',      // 国家代码（string）
  province: '北京市',     // 省份名称（string）
  provinceCode: '110000', // 省份代码（string）
  city: '北京市',         // 城市名称（string）
  cityCode: '110100',     // 城市代码（string）
  district: '东城区',     // 区县名称（string）
  districtCode: '110101', // 区县代码（string）
  pois: [                 // POI列表（Array）
    {
      name: '天安门广场',      // POI名称（string）
      address: '北京市东城区'   // POI地址（string）
    },
    // ... 更多POI
  ]
}
```

**失败时（reject）返回：**

```javascript
{
  name: 'PERMISSION_DENIED',  // 错误类型枚举（string）
  message: '用户拒绝了位置权限'  // 错误信息（string）
}
```

## 示例

**示例 1：获取基本位置信息（仅经纬度）**

```javascript
try {
  const result = await window.lingguang.getLocation({
    poi: false
  });
  
  console.log('当前位置:', {
    经度: result.longitude,
    纬度: result.latitude
  });
} catch (error) {
  console.log('获取位置失败:', error.message);
}
```

**示例 2：获取位置信息及附近POI**

```javascript
try {
  const result = await window.lingguang.getLocation({
    poi: true
  });
  
  console.log('位置信息:', {
    经纬度: `${result.latitude}, ${result.longitude}`,
    地址: `${result.country} ${result.province} ${result.city} ${result.district}`,
    附近POI数量: result.pois.length
  });
  
  // 显示附近的POI
  result.pois.forEach((poi, index) => {
    console.log(`POI ${index + 1}: ${poi.name} - ${poi.address}`);
  });
} catch (error) {
  console.log('获取位置失败:', error);
}
```

## 注意事项

1. **权限要求**：
   - 需要位置权限，用户可能会拒绝权限请求
   - 需要开启设备的位置服务（GPS）

2. **返回值说明**：
   - 当 `poi` 参数为 `false` 时，只返回 `longitude` 和 `latitude` 两个字段
   - 当 `poi` 参数为 `true` 时，返回完整的位置信息，包括国家、省份、城市、区县和 POI 列表

3. **定位精度**：
   - 定位精度取决于设备的位置服务状态和网络环境
   - GPS 信号弱或网络问题可能导致定位失败或精度降低
