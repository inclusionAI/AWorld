---
description: 灵光小程序 API：API_GYROSCOPE。与 PRD「所需 API 技能」对齐后按需激活。陀螺仪
---

你可以使用陀螺仪API来做重力游戏、全景/3D视角控制、视差动效、姿态检测和一些有趣的体感交互。

# 陀螺仪API

```typescript
window.lingguang.gyroscope.start(options?: GyroscopeStartOptions): Promise<void>
window.lingguang.gyroscope.stop(): Promise<void>
```

**lingguang.gyroscope.start 方法失败时（reject）返回：**

```javascript
{
  code: 'PERMISSION_REQUIRED',  // 错误类型枚举（string）
  message: '需要权限'  // 错误信息（string）
}
```

```typescript
interface GyroscopeReading {
  x: number   // 绕 X 轴角速度
  y: number   // 绕 Y 轴角速度
  z: number   // 绕 Z 轴角速度
  timestamp: number // 相对时间，浮点数，单位秒，方便前端做差分等
}

type GyroscopeFrequency = 'low' | 'medium' | 'high'

/**
 * start 的参数：频率 + 回调
 */
interface GyroscopeStartOptions {
  /**
   * 采样频率，可选值：'low'（低）、'medium'（中）、'high'（高）。
   * 默认值为 'high'（高）。
   */
  frequency?: GyroscopeFrequency

  /**
   * 必需：每次采样数据的回调
   */
  onReading: (reading: GyroscopeReading) => void
}
```
