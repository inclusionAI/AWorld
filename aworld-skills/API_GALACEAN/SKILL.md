---
description: 灵光小程序 API：API_GALACEAN。与 PRD「所需 API 技能」对齐后按需激活。Galacean 3D引擎
---

# Galacean 开发 Skill

> 文档路径: `{{ sandbox_path }}`
> 本文档补充 readme.md 中未覆盖的 Galacean 引擎特定内容

## API 查询策略

在 `{{ sandbox_path }}/engine/declare/` 使用 Grep 搜索：

| 搜索目标 | ✅ 正确 | ❌ 错误 |
|---------|--------|--------|
| 类名 | `PrimitiveMesh` | - |
| 方法名 | `createCuboid` | `PrimitiveMesh.createCuboid` |
| 属性 | `position` | `transform.position` |

> `.d.ts` 中方法声明是 `createCuboid(`，不含类名前缀

**三次法则**：同一 API 最多搜索 3 次，仍无结果则标注 `// TODO: 待验证`，**禁止臆造**。

---

## 核心陷阱

### Transform 旋转方向（最常见错误）

Y轴正向旋转是**逆时针**，但 sin/cos 公式是顺时针，视觉旋转需**取反**：

```typescript
dx = Math.sin(angle) * speed;      // 移动用原始角度
dz = -Math.cos(angle) * speed;
entity.transform.setRotation(0, -angle, 0);  // 视觉旋转取反
```

### 相机跟随必须在 onLateUpdate

在 onUpdate 中会抖动：

```typescript
onLateUpdate(deltaTime: number): void {
  const t = 1 - Math.exp(-smoothSpeed * deltaTime);
  Vector3.lerp(currentPos, targetPos, t, outPos);
  this.entity.transform.position = outPos;
}
```

### 材质修改前必须 clone

否则影响所有共享该材质的物体：

```typescript
const mat = renderer.getMaterial().clone();
mat.baseColor = new Color(1, 0, 0);
renderer.setMaterial(mat);
```

### 物理引擎初始化

点击/碰撞必须先初始化：

```typescript
const engine = await WebGLEngine.create({
  canvas: "canvas",
  physics: new PhysXPhysics()
});
```

### 碰撞体形状

只有 5 种：`Box`、`Sphere`、`Capsule`、`Plane`、`Mesh`
**CylinderColliderShape 不存在！**

### 脚本注册时机

`Loader.registerClass("Name", Class)` 必须在 `resourceManager.load()` 前执行，Name 需与场景引用一致。

---

## 按需查阅（遇到问题时再读）

| 遇到问题 | 查阅文档 |
|----------|----------|
| API 参数格式不确定 | `QUICK_REF.md` |
| 核心陷阱未覆盖的问题 | `GOTCHAS.md` |
| 实现点击/拖拽交互 | `engine/Input/` + `engine/Physics/` |
| 相机控制/跟随细节 | `engine/Camera/` |
| Script 生命周期顺序 | `engine/Script/` |

---

## 编码后自检

```
□ Transform 旋转取反了？
□ 相机跟随在 onLateUpdate？
□ 材质修改前 clone？
□ PhysXPhysics 初始化了？
□ MeshRenderer.setMaterial 调用了？
□ Collider.addShape 调用了？
□ Loader.registerClass 在 load 前？
□ 不确定的 API 在 declare 验证了？
```
