---
description: 灵光小程序 API：API_DB。与 PRD「所需 API 技能」对齐后按需激活。数据库
---

# 前端直连关系型数据库接口规范

## 何时用 DB、何时用 STORAGE（必读）

- **用 DB**：**多条记录**、**会随使用增长的列表**、需要**按条增删改查**或**按条件查询/分页**的数据。例如：笔记列表、待办、借阅记录、订单、评论、签到记录等。即使用户未提“多表”，单表多条记录也应使用 DB。
- **用 STORAGE**：每个 key 对应**一份**小数据，整体读、整体写，key 数量少且固定。例如：用户设置、主题、单份游戏进度。**禁止**把多条记录塞进一个 key 做全量读写（如 100 条笔记存成一个 key），否则代价高且易超限。

---

## ⚠️ 使用DB功能前必读（生成阶段能力调用）

**使用DB功能时，必须严格按照以下流程执行，禁止跳过任何步骤**：

1. **必须**通过 `CapabilityCall` 调用 `db.policy_judge` capability 选择DB应用模式
2. **必须**通过 `CapabilityCall` 调用 `db.update_table_schema` capability 创建表结构（这是唯一合法的创建表方式）
3. **禁止**在前端代码中执行任何DDL操作（CREATE TABLE、ALTER TABLE等）

**违反流程的后果**：
- 表结构缺少系统自动注入的列（`user_id`, `artifact_id`, `artifact_version`）
- 无法经过PolicyManager的校验和权限控制
- 运行时无法正确进行数据隔离和权限控制
- 应用可能无法正常工作

---

## 目标与原则

本规范用于指导 **AI 生成可维护、可预测、可安全执行** 的数据库访问代码。核心原则：

* **禁止拼接 SQL 值**：所有动态值必须通过 `binds` 绑定。
* **仅支持 `?` 占位符**：SQL 中只允许使用 `?`。
* **禁止 `SELECT *`**：必须显式列出所有输出列，并使用 `AS` 提供稳定字段名。
* **返回对象数组且字段名稳定**：`query()` 返回 `Array<Object>`，对象 key 必须可预测、稳定、可直接用于前端渲染与业务逻辑。

---

## DB集成完整流程（Agent生成阶段）

**⚠️ 强制要求**：使用DB功能时，必须严格按照以下流程执行，**禁止跳过任何步骤**。

### 流程概览（必须严格遵循）

```
1. 通过 `CapabilityCall` 调用 `db.policy_judge` capability 选择DB应用模式
        │
        ▼
2. 通过 `CapabilityCall` 调用 `db.update_table_schema` capability 创建表结构（DDL） ⚠️ 必须执行
        │
        ▼
3. 生成前端代码（只包含DML/DQL操作）⚠️ 禁止包含DDL
```

### 步骤1：选择DB应用模式（必须执行）

**⚠️ 强制要求**：在创建表之前，必须先通过 `CapabilityCall` 调用 `db.policy_judge` capability 选择合适的DB应用模式并绑定Policy。

- 这是创建表的前置条件，不能跳过
- 不确定参数怎么填：先调用 `CapabilityCall(mode="help", capability="db.policy_judge")` 获取参数模板与示例

### 步骤2：创建表结构（必须执行）

**⚠️ 强制要求**：必须在Agent生成阶段通过 `CapabilityCall` 调用 `db.update_table_schema` capability 创建表结构。

**这是唯一合法的创建表的方式**，禁止使用其他方式创建表。

**重要说明**：
- SQL语法要求、表类型选择（User表/Share表）、系统自动注入的列、DDL操作限制等详细信息：
  - 不确定参数怎么填：先调用 `CapabilityCall(mode="help", capability="db.update_table_schema")` 获取参数模板与示例
- 所有表会自动添加 `artifact_id` 和 `artifact_version` 列
- User表会额外自动添加 `user_id` 列用于数据隔离

**⚠️ 禁止行为**：
- ❌ 禁止在前端代码中调用 `lingguang.db.execute()` 执行 `CREATE TABLE` 语句
- ❌ 禁止在前端代码中创建 `createTable()` 或类似函数
- ❌ 禁止在 `useEffect` 或组件初始化时执行DDL操作

### 步骤3：生成前端代码（只包含DML/DQL）

**⚠️ 强制要求**：前端代码只能包含DML/DQL操作，绝对禁止包含DDL操作。

**允许的操作**：
- ✅ `SELECT` - 查询数据
- ✅ `INSERT` - 插入数据
- ✅ `UPDATE` - 更新数据
- ✅ `DELETE` - 删除数据

**禁止的操作**：
- ❌ **绝对禁止** `CREATE TABLE` - 表结构已在步骤2创建
- ❌ **绝对禁止** `ALTER TABLE` - 表结构修改必须通过工具完成
- ❌ **绝对禁止** `DROP TABLE` - 不支持删除表
- ❌ **绝对禁止** 任何形式的DDL操作

**不需要处理系统列**：
- 不需要在SQL中指定 `user_id`、`artifact_id`、`artifact_version` 等系统列
- 系统会在运行时自动注入这些列

**为什么禁止前端DDL**：
1. 表结构应该在生成阶段就创建好，运行时不需要再创建
2. 前端执行DDL无法获得系统自动注入的列（`user_id`, `artifact_id`等）
3. 前端执行DDL无法经过PolicyManager的校验和权限控制
4. 前端执行DDL可能导致表结构不一致，影响数据隔离和权限控制
5. 前端执行DDL会导致表结构缺少必要的系统列，运行时无法正常工作

---

## 前端代码接口定义（TypeScript）

### 基础类型

```ts
type BindValue =
  | null
  | string
  | number
  | boolean
  | Date; // 注意：最终会被序列化为字符串发送；推荐直接传 string（见“时间字段约定”）

type DbReq = {
  /**
   * SQL 语句，仅支持 `?` 占位符。
   * 严禁将用户输入/动态值直接拼接进 sql 字符串。
   */
  sql: string;

  /**
   * 与 `?` 一一对应的绑定参数数组。
   * 数量必须与 sql 中 `?` 的个数一致，顺序必须严格对应。
   */
  binds?: BindValue[];

  /**
   * 可选：超时控制（毫秒），用于防止慢查询导致 UI 挂起。
   */
  timeoutMs?: number;

  /**
   * 可选：取消控制。调用方可通过 AbortController 取消本次查询。
   */
  signal?: AbortSignal;
};
```

### 返回结构

```ts
type DbQueryResult<T = Record<string, any>> = {
  success: boolean;
  data: T[];              // 每个对象代表一行；key 为 SQL 显式指定的列别名（AS）
  message?: string;
};

type DbExecuteResult = {
  success: boolean;
  data: {
    rowsAffected: number;
  };
  message?: string;
};
```

### 时间字段约定（必须遵守）

- **query 返回**：SQL 结果里的 `DATE / DATETIME / TIMESTAMP` 列，**统一返回 `string`**（ISO 8601，例如 `"2026-01-28T16:27:14"`；`DATE` 为 `"YYYY-MM-DD"`）。
  - 前端如需 `Date`，请在业务侧显式 `new Date(value)`；**不要**在类型上直接写 `Date` 并假设运行时返回的是 `Date` 对象。
- **binds 入参**：允许传 `Date`，但它只会在传输链路中被序列化为字符串；为避免时区/格式歧义，**推荐直接传 `string`**（`"YYYY-MM-DD"` 或 `"YYYY-MM-DD HH:mm:ss"`）。

### 方法签名

```ts
declare const lingguang: {
  db: {
    query<T = any>(req: DbReq): Promise<DbQueryResult<T>>;
    execute(req: DbReq): Promise<DbExecuteResult>;
  };
};
```
---

### 主键与 INSERT 约定（必须遵守）

- **系统不支持自增主键**：建表时禁止使用 AUTO_INCREMENT / AUTOINCREMENT。
- **INSERT 必须显式包含主键列**：主键无自增时，主键列（如 `id`）必须在 INSERT 中显式写入，值由业务层生成（如 `Date.now()`、`crypto.randomUUID()` 等）。**系统不支持 `lastInsertId`**。
- 推荐写法：在业务层生成主键值，通过 `binds` 传入；插入成功后返回该值。系统不支持 lastInsertId，不能用其获取新插入的 id。

---

- **支持的SQL语法**：

  **SELECT 查询**：
  - 基本查询：`SELECT col1, col2 FROM table`（禁止使用 `SELECT *`，必须显式列出所有列）
  - WHERE条件：`=, !=, <>, >, >=, <, <=, AND, OR, NOT, BETWEEN, IS NULL, IS NOT NULL, LIKE`
  - 排序：`ORDER BY col ASC/DESC`
  - 分页：`LIMIT n OFFSET m`
  - 分组：`GROUP BY col` + `HAVING condition`
  - 聚合函数：`COUNT, SUM, AVG, MIN, MAX`
  - **INNER JOIN**：仅支持两表连接，以id为连接条件

  **INSERT 插入**：
  - 单行：`INSERT INTO table (col1, col2) VALUES (val1, val2)`
  - 多行：`INSERT INTO table (col1, col2) VALUES (v1, v2), (v3, v4)`

  **UPDATE 更新**：
  - 基本更新：`UPDATE table SET col1 = val1 WHERE condition`

  **DELETE 删除**：
  - 基本删除：`DELETE FROM table WHERE condition`

- **不支持的SQL语法**：
  - **禁止 LEFT JOIN、RIGHT JOIN、FULL JOIN**（仅支持 INNER JOIN，两表连接请使用 INNER JOIN）
  - 三表及以上的JOIN
  - 子查询
  - UNION / INTERSECT / EXCEPT
  - DISTINCT
  - 窗口函数

- **重要注意事项**：
  - **用户数据自动隔离**：对于User表（包含user_id列的表），系统会自动在查询中添加user_id条件，你的SQL中不需要手动处理
  - **版本数据兼容**：读取数据时会自动添加版本限制，确保只读取当前版本及之前版本的数据
  - 单次查询最大返回1000行数据
  - 建议在查询中使用LIMIT进行分页
  - 频繁写入时建议添加防抖机制

---

## 规范要求（Must / Must Not / Should）

### 1) 参数绑定（安全与可移植性）

**Must**

* 所有动态值必须通过 `binds` 传递。
* `binds` 的长度必须与 SQL 中 `?` 的数量一致，并按出现顺序一一对应。

**Must Not**

* 禁止任何形式的字符串拼接注入值，例如：

  * `"... where name = '" + name + "'"`（禁止）
  * `` `... where id = ${id}` ``（禁止）

**Should**

* 对用户输入先做基础校验（例如长度、格式），再作为 bind 传入。

---

### 2) 字段名稳定性（禁止 `*`，必须显式列）

**Must**

* 禁止使用 `SELECT *`。
* 必须显式列出所有输出列。
* 必须为每个输出列指定稳定的别名（`AS`），作为返回对象的 key。
* 多表 `JOIN` 时必须避免重名字段：所有列都必须 `AS` 到唯一 key。

**Must Not**

* 禁止 `SELECT * FROM ...`
* 禁止依赖数据库默认列名、或依赖驱动返回的列名大小写规则。

**Should**

* 别名使用前端常用的 `camelCase`，例如 `userId`, `orderTotal`。
* 只查询业务需要的列，避免“顺手全查”。

---

### 3) `query` 与 `execute` 的使用边界

**Must**

* `query()` 仅用于读取（典型为 `SELECT ...`）。
* `execute()` 用于写入/变更（典型为 `INSERT/UPDATE/DELETE ...`）。

**Should**

* 对写入操作返回 `rowsAffected` 并进行业务判断（例如必须影响 1 行）。

---

### 4) 取消与超时（UI 可靠性）

**Should**

* 输入联想/搜索场景：使用 `AbortController` 取消旧请求，避免旧结果覆盖新结果。
* 长查询场景：设置 `timeoutMs`，避免 UI 长时间等待。

> 取消行为建议：当 `signal` aborted 时 Promise 应被拒绝（抛出 AbortError 或等价错误），调用方可选择忽略该错误。

---

### 5) 保存/录入界面：禁止 form 提交

保存按钮用 `type="button"` + `onClick` 调用 `lingguang.db.execute`，禁止 `type="submit"`。

---

### 6) 错误处理与用户反馈（必须遵守）

**Must**

* `success=false` 时，**必须**用适合当前项目的 UI 展示错误信息，让用户知道发生了什么
* 展示方式使用自定义 DOM 元素（如页内 toast、错误提示栏）

**Must Not**

* 禁止在 `success=false` 时不给用户任何提示
* 禁止吞掉 `catch` 里的异常而不展示任何错误提示

---

## 示例

### 示例 1：查询（显式列 + 稳定别名 + binds）

```ts
const res = await lingguang.db.query<{
  userId: number;
  userName: string;
  createdAt: string;
}>({
  sql: `
    select
      u.id as userId,
      u.name as userName,
      u.created_at as createdAt
    from user u
    where u.status = ?
    order by u.id desc
    limit ?
  `,
  binds: ["active", 50],
});

if (!res.success) {
  throw new Error(res.message || "db.query failed");
}

// res.data: [{ userId, userName, createdAt }, ...]
for (const row of res.data) {
  // 使用表格或列表组件渲染 row.userId / row.userName，不要使用 console 输出。
}
```

✅ 要点：无 `*`、每列都有 `AS`、key 稳定可直接消费。

---

### 示例 2：JOIN（必须消除重名字段）

```ts
const res = await lingguang.db.query<{
  userId: number;
  userName: string;
  orderId: number;
  orderTotal: number;
}>({
  sql: `
    select
      u.id as userId,
      u.name as userName,
      o.id as orderId,
      o.total as orderTotal
    from user u
    join orders o on o.user_id = u.id
    where u.id = ?
    order by o.id desc
    limit ?
  `,
  binds: [123, 20],
});
```

✅ 要点：`u.id` 与 `o.id` 必须通过 `AS userId/orderId` 明确区分。

---

### 示例 3：可取消搜索（AbortSignal）

```ts
let controller: AbortController | null = null;

async function searchUsers(keyword: string) {
  controller?.abort();
  controller = new AbortController();

  const res = await lingguang.db.query({
    sql: `
      select
        u.id as userId,
        u.name as userName
      from user u
      where u.name like ?
      order by u.id desc
      limit ?
    `,
    binds: [`%${keyword}%`, 20],
    signal: controller.signal,
    timeoutMs: 3000,
  });

  return res.data;
}
```

---

## 常见错误与禁止写法（AI 必须避免）

### 禁止：`SELECT *`

```sql
select * from user
```

### 禁止：拼接动态值

```ts
await lingguang.db.query({
  sql: `select id as userId from user where name='${name}'`, // 禁止
});
```

### 禁止：bind 数量与 `?` 不匹配

```ts
await lingguang.db.query({
  sql: `select id as userId from user where status = ? and type = ?`,
  binds: ["active"], // 禁止：缺少一个 bind
});
```

### 禁止：`<button type="submit">`（iframe 沙箱无 allow-forms，详见规范 5)

---

## 关于 `IN (?)` 的约定（必须遵守）

由于仅支持 `?` 且 `binds` 是一维数组，**禁止**直接把数组作为单个 bind 传入 `IN (?)`：

🚫 禁止：

```ts
sql: `... where id in (?)`
binds: [[1,2,3]] // 禁止：多数驱动不会自动展开
```

✅ 推荐（手动展开占位符）：

```ts
const ids = [1, 2, 3];
await lingguang.db.query({
  sql: `select u.id as userId, u.name as userName
        from user u
        where u.id in (${ids.map(() => "?").join(",")})`,
  binds: ids,
});
```

---

## 最终检查清单（AI 生成代码前必须自检）

* 是否使用了 `SELECT *`？（必须为否）
* 是否每个输出列都显式列出，并带 `AS` 稳定别名？
* 是否所有动态值都通过 `binds`，且未发生字符串拼接？
* `?` 个数是否与 `binds.length` 完全一致？
* 是否使用了 LEFT/RIGHT/FULL JOIN？（必须为否，仅支持 INNER JOIN）
* JOIN 是否消除了重名字段？
* 搜索/频繁触发场景是否使用了 `AbortSignal` 或 `timeoutMs`？
* 保存/录入按钮是否使用 `type="button"` + `onClick` 调用 execute，而非 `type="submit"`？（必须为是）

## 更多示例

## 示例 1：查询数据（禁止 `*` + 稳定字段名）

```js
async function loadNotes() {
  try {
    const result = await lingguang.db.query({
      sql: `
        select
          n.id as noteId,
          n.title as title,
          n.content as content,
          n.created_at as createdAt
        from notes n
        order by n.created_at desc
      `,
      binds: []
    });

    if (result.success) {
      // 用适合当前项目的 UI 在主内容区渲染查询结果列表（如表格、卡片列表），不要使用 console 输出。
      return result.data; // [{ noteId, title, content, createdAt }, ...]
    } else {
      // 用适合当前项目的 UI（如错误提示区或 toast）展示 result.message，并提供重试入口。
      return [];
    }
  } catch (err) {
    // 用适合当前项目的 UI 展示“查询异常，请稍后重试”的友好提示，不要暴露底层异常堆栈给用户。
    return [];
  }
}
```

---

## 示例 2：插入数据（禁止拼接 + binds，主键显式生成）

```js
async function addNote(title, content) {
  try {
    // 注意：不需要手动添加user_id，系统会自动注入（如果你的系统有该机制）
    // 主键无自增，需在业务层生成并显式写入；系统不支持 lastInsertId
    const id = Date.now();
    const now = new Date().toISOString();

    const result = await lingguang.db.execute({
      sql: `
        insert into notes (id, title, content, created_at)
        values (?, ?, ?, ?)
      `,
      binds: [id, title, content, now]
    });

    if (result.success) {
      // 用成功 toast/状态条提示“保存成功”，并刷新列表或关闭编辑弹窗。
      return true;
    } else {
      // 在表单错误区或 toast 展示 result.message，引导用户修正后重试。
      return false;
    }
  } catch (err) {
    // 用适合当前项目的 UI 展示“保存异常，请稍后重试”的友好提示。
    return false;
  }
}
```

---

## 示例 3：更新数据（禁止拼接 + binds）

```js
async function updateNote(noteId, title, content) {
  try {
    const result = await lingguang.db.execute({
      sql: `
        update notes
        set
          title = ?,
          content = ?,
          updated_at = NOW()
        where id = ?
      `,
      binds: [title, content, noteId]
    });

    if (result.success) {
      // 建议校验 rowsAffected，确保确实更新到一行
      if (result.data.rowsAffected !== 1) {
        // 用适合当前项目的 UI 给出警告提示：更新未命中或命中多行，并提示用户刷新后重试。
      }
      // 用成功 toast/状态提示“更新成功”，并同步刷新对应 UI 数据。
      return true;
    } else {
      // 用适合当前项目的 UI（如错误提示区或 toast）展示 result.message。
      return false;
    }
  } catch (err) {
    // 用适合当前项目的 UI 展示“更新异常，请稍后重试”的友好提示。
    return false;
  }
}
```

---

## 示例 4：删除数据（禁止拼接 + binds）

```js
async function deleteNote(noteId) {
  try {
    // 严格禁止使用浏览器原生方法`alert()`和`confirm()`
    // 如需确认功能，必须使用自定义的DOM元素来实现确认弹窗
    const result = await lingguang.db.execute({
      sql: `
        delete from notes
        where id = ?
      `,
      binds: [noteId]
    });

    if (result.success) {
      if (result.data.rowsAffected !== 1) {
        // 用适合当前项目的 UI 给出警告提示：删除未命中或命中多行，并提示用户刷新后重试。
      }
      // 用成功 toast/状态提示“删除成功”，并从列表中移除对应项或触发重新查询。
      return true;
    } else {
      // 用适合当前项目的 UI（如错误提示区或 toast）展示 result.message。
      return false;
    }
  } catch (err) {
    // 用适合当前项目的 UI 展示“删除异常，请稍后重试”的友好提示。
    return false;
  }
}
```

---

## 示例 5：条件查询（LIKE 禁止拼接，通配符放在 bind 里）

```js
async function searchNotes(keyword) {
  try {
    const like = `%${keyword}%`;

    const result = await lingguang.db.query({
      sql: `
        select
          n.id as noteId,
          n.title as title,
          n.content as content,
          n.created_at as createdAt
        from notes n
        where n.title like ? or n.content like ?
        order by n.created_at desc
        limit ?
      `,
      binds: [like, like, 20]
    });

    if (result.success) {
      return result.data;
    }
    return [];
  } catch (err) {
    // 用适合当前项目的 UI 展示“搜索失败，请稍后重试”的提示，并保留当前页面可操作状态。
    return [];
  }
}
```

---

## 示例 6：聚合查询（显式别名稳定 key）

```js
async function getNoteStats() {
  try {
    const totalResult = await lingguang.db.query({
      sql: `
        select
          count(*) as total
        from notes
      `,
      binds: []
    });

    const todayResult = await lingguang.db.query({
      sql: `
        select
          count(*) as today
        from notes
        where date(created_at) = curdate()
      `,
      binds: []
    });

    if (totalResult.success && todayResult.success) {
      return {
        total: totalResult.data[0]?.total ?? 0,
        today: todayResult.data[0]?.today ?? 0
      };
    }
    return null;
  } catch (err) {
    showError('统计失败，请稍后重试');
    return null;
  }
}
```

---

## 示例 7：JOIN 查询（多表字段必须唯一别名 + 禁止裸字段名）

```js
async function getNotesWithCategories() {
  try {
    // 仅支持 INNER JOIN，以id为连接条件（按你原文约束）
    const result = await lingguang.db.query({
      sql: `
        select
          n.id as noteId,
          n.title as title,
          n.created_at as createdAt,
          c.id as categoryId,
          c.name as categoryName
        from notes n
        inner join categories c on n.category_id = c.id
        order by n.created_at desc
      `,
      binds: []
    });

    if (result.success) {
      return result.data; // [{ noteId, title, createdAt, categoryId, categoryName }, ...]
    }
    return [];
  } catch (err) {
    // 用适合当前项目的 UI（如错误提示区或 toast）展示“查询失败，请稍后重试”。
    return [];
  }
}
```
