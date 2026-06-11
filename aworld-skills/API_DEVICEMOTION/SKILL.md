---
description: 灵光小程序 API：API_DEVICEMOTION。与 PRD「所需 API 技能」对齐后按需激活。设备方向
---

你可以使用设备方向 API 获取设备姿态变化，用于体感交互、姿态识别和方向控制等场景。

# 设备方向 API

```typescript
window.lingguang.startDeviceMotionListening(options?: DeviceMotionStartOptions): Promise<void>
window.lingguang.stopDeviceMotionListening(): Promise<void>
window.lingguang.onDeviceMotionChange(callback: (reading: DeviceMotionReading) => void): void
```

**window.lingguang.startDeviceMotionListening 方法失败时（reject）返回：**

```javascript
{
  code: 'PERMISSION_REQUIRED',  // 错误类型枚举（string）
  message: '需要权限'  // 错误信息（string）
}
```

```typescript
type SensorInterval = 'game' | 'ui' | 'normal'

interface DeviceMotionReading {
  alpha: number // 绕 Z 轴旋转角（单位：度），范围 [0, 360)
  beta: number  // 绕 X 轴旋转角（单位：度），范围 [-180, 180)
  gamma: number // 绕 Y 轴旋转角（单位：度），范围 [-90, 90)
}

interface DeviceMotionStartOptions {
  /**
   * 监听频率，可选值：'game'（约 20ms/次）、'ui'（约 60ms/次）、'normal'（约 200ms/次）。
   * 默认值为 'normal'。
   */
  interval?: SensorInterval
}
```
