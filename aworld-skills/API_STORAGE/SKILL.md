---
description: 灵光小程序 API：API_STORAGE。与 PRD「所需 API 技能」对齐后按需激活。持久化存储
---

- 你可以使用以下异步存储接口持久化读写数据（例如保存用户设置、应用数据等）：

  - `lingguang.storage.setItem(key: string, value: object): Promise<boolean>`
  - `lingguang.storage.getItem(key: string): Promise<object | null>`
  - `lingguang.storage.removeItem(key: string): Promise<boolean>`
  - `lingguang.storage.clear(): Promise<boolean>`

- **关键点**：
  - 修改存储对象`value: object`数据结构时，请注意向前兼容：注意只能增加object的key，禁止删除object的key；需要对前序版本缺失的字段做默认值处理，禁止直接解构/访问假定一定存在的属性。
  - 所有方法都是异步的，返回 Promise，请在 `async` 函数中使用 `await` 来等待结果，并使用 `try...catch` 来处理错误
  - key只能是字符串，value只能是plain object
  - getItem 可能返回 null，要做好空值判断
  - setItem / removeItem / clear 返回 boolean 表示是否成功

{% if use_react_scaffold -%}
- **使用示例（React 版本）**：

  **示例 1：保存数据**
  ```tsx
  import { useState } from 'react';

  function SettingsComponent() {
    const [settings, setSettings] = useState<{ username: string; theme: string }>({ username: 'user123', theme: 'dark' });

    // showError：请按当前项目的提示风格实现，禁止直接console.error
    const saveSettings = async () => {
      try {
        const ok = await lingguang.storage.setItem('user_settings', settings);
        if (!ok) {
          showError('设置保存失败，请重试'); // ⚠️ 必须告知用户，不能只打 console
        }
      } catch (err: any) {
        showError('存储异常，请稍后重试'); // ⚠️
      }
    };

    return (
      <div>
        <button onClick={saveSettings}>保存设置</button>
      </div>
    );
  }
  ```

  **示例 2：读取数据（初始化时加载）**
  ```tsx
  import { useState, useEffect } from 'react';

  function SettingsPage() {
    const [settings, setSettings] = useState<{ username: string; theme: string } | null>(null);
    const [loading, setLoading] = useState<boolean>(true);

    useEffect(() => {
      async function loadSettings() {
        try {
          const savedSettings = await lingguang.storage.getItem('user_settings');
          if (savedSettings === null) {
            console.log('no settings found');
            setSettings({ username: '', theme: 'light' });
          } else {
            setSettings(savedSettings);
          }
        } catch (err: any) {
          showError('设置加载失败，请稍后重试'); // ⚠️ 必须告知用户
        } finally {
          setLoading(false);
        }
      }
      loadSettings();
    }, []);

    return (
      <div className="settings-page">
        <h1>设置</h1>
        {loading ? (
          // 注意：根据实际应用场景设计loading展示方式
          // 例如：在表单区域显示loading，而不是整个页面只显示一个loading div
          // 可以是在列表区域、表单区域、卡片区域等特定位置显示loading状态
          <div className="settings-form">
            <div className="loading-placeholder">加载设置中...</div>
          </div>
        ) : (
          <div className="settings-form">
            <label>
              用户名：
              <input 
                value={settings?.username || ''} 
                onChange={(e) => setSettings({ ...settings!, username: e.target.value })}
              />
            </label>
            <label>
              主题：
              <select 
                value={settings?.theme || 'light'}
                onChange={(e) => setSettings({ ...settings!, theme: e.target.value })}
              >
                <option value="light">浅色</option>
                <option value="dark">深色</option>
              </select>
            </label>
          </div>
        )}
      </div>
    );
  }
  ```
  
  **说明**：loading状态的展示应该根据实际应用场景来设计：
  - 如果是在列表页面，可以在列表区域显示loading骨架屏或占位符
  - 如果是在表单页面，可以在表单区域显示loading，保持页面其他部分（如标题、导航）正常显示
  - 如果是在卡片/详情页面，可以在卡片内容区域显示loading
  - 避免整个页面只显示一个简单的loading div，应该保持应用的整体布局和结构

  **示例 3：删除数据**
  ```tsx
  function DeleteButton() {
    const deleteSettings = async () => {
      try {
        // 严格禁止使用浏览器原生方法`alert()`和`confirm()`
        // 如需提示或确认功能，必须使用自定义的DOM元素（如div+CSS）来实现弹窗效果
        const ok = await lingguang.storage.removeItem('user_settings');
        if (ok) {
          console.log('删除成功');
        }
      } catch (err: any) {
        showError('操作失败，请稍后重试'); // ⚠️ 必须告知用户（showError 见 skill_error_handling.md）
      }
    };

    return <button onClick={deleteSettings}>删除设置</button>;
  }
  ```

  **示例 4：清空所有数据**
  ```tsx
  function ClearButton() {
    const clearAllData = async () => {
      try {
        // 严格禁止使用浏览器原生方法`alert()`和`confirm()`
        // 如需提示或确认功能，必须使用自定义的DOM元素（如div+CSS）来实现弹窗效果
        const ok = await lingguang.storage.clear();
        if (ok) {
          console.log('清空成功');
        }
      } catch (err: any) {
        showError('操作失败，请稍后重试'); // ⚠️ 必须告知用户（showError 见 skill_error_handling.md）
      }
    };

    return <button onClick={clearAllData}>清空所有数据</button>;
  }
  ```

  **示例 5：初始化时加载数据**
  ```tsx
  import { useState, useEffect } from 'react';

  function GameApp() {
    const [gameData, setGameData] = useState<{ level: number; score: number } | null>(null);

    useEffect(() => {
      async function initApp() {
        try {
          const savedData = await lingguang.storage.getItem('app_data');
          if (savedData === null) {
            // 首次使用，初始化默认数据
            const defaultData = { level: 1, score: 0 };
            await lingguang.storage.setItem('app_data', defaultData);
            setGameData(defaultData);
          } else {
            setGameData(savedData);
          }
        } catch (err: any) {
          showError('数据加载失败，请稍后重试'); // ⚠️ 必须告知用户
        }
      }
      initApp();
    }, []);

    return <div>游戏数据：{JSON.stringify(gameData)}</div>;
  }
  ```

  **示例 6：保存游戏进度**
  ```tsx
  function GameComponent() {
    const [level, setLevel] = useState<number>(1);
    const [score, setScore] = useState<number>(0);

    const saveGameProgress = async () => {
      try {
        const progress = {
          level: level,
          score: score,
          achievements: [],
          lastSaveTime: Date.now()
        };
        const ok = await lingguang.storage.setItem('game_progress', progress);
        if (ok) {
          console.log('游戏进度已保存');
        }
      } catch (err: any) {
        showError('进度保存失败，请稍后重试'); // ⚠️ 必须告知用户
      }
    };

    return (
      <div>
        <button onClick={saveGameProgress}>保存进度</button>
      </div>
    );
  }
  ```
{% else -%}
- **使用示例 1：保存数据**
  ```javascript
  async function saveSettings() {
    const settings = { username: 'user123', theme: 'dark' };
    try {
      const ok = await lingguang.storage.setItem('user_settings', settings);
      if (!ok) {
        showError('保存失败，请重试'); // ⚠️ 必须告知用户
      }
    } catch (err) {
      showError('操作失败，请稍后重试'); // ⚠️ 必须告知用户（showError 见 skill_error_handling.md）
    }
  }
  ```

- **使用示例 2：读取数据**
  ```javascript
  async function loadSettings() {
    try {
      const settings = await lingguang.storage.getItem('user_settings');
      if (settings === null) {
        console.log('no settings found');
        return;
      }
      console.log(settings);
    } catch (err) {
      showError('操作失败，请稍后重试'); // ⚠️ 必须告知用户（showError 见 skill_error_handling.md）
    }
  }
  ```

- **使用示例 3：删除数据**
  ```javascript
  async function deleteSettings() {
    try {
      // 严格禁止使用浏览器原生方法`alert()`和`confirm()`
      // 如需提示或确认功能，必须使用自定义的DOM元素（如div+CSS）来实现弹窗效果
      const ok = await lingguang.storage.removeItem('user_settings');
      if (ok) {
        console.log('删除成功');
      }
    } catch (err) {
      showError('操作失败，请稍后重试'); // ⚠️ 必须告知用户（showError 见 skill_error_handling.md）
    }
  }
  ```

- **使用示例 4：清空所有数据**
  ```javascript
  async function clearAllData() {
    try {
      // 严格禁止使用浏览器原生方法`alert()`和`confirm()`
      // 如需提示或确认功能，必须使用自定义的DOM元素（如div+CSS）来实现弹窗效果
      const ok = await lingguang.storage.clear();
      if (ok) {
        console.log('清空成功');
      }
    } catch (err) {
      showError('操作失败，请稍后重试'); // ⚠️ 必须告知用户（showError 见 skill_error_handling.md）
    }
  }
  ```

- **使用示例 5：初始化时加载数据**
  ```javascript
  async function initApp() {
    try {
      const savedData = await lingguang.storage.getItem('app_data');
      if (savedData === null) {
        // 首次使用，初始化默认数据
        const defaultData = { level: 1, score: 0 };
        await lingguang.storage.setItem('app_data', defaultData);
        return defaultData;
      }
      return savedData;
    } catch (err) {
      showError('数据加载失败，请稍后重试'); // ⚠️ 必须告知用户（showError 见 skill_error_handling.md）
      return null;
    }
  }

  // 页面加载时调用
  window.addEventListener('DOMContentLoaded', async () => {
    const data = await initApp();
    console.log('应用数据:', data);
  });
  ```

- **使用示例 6：保存游戏进度**
  ```javascript
  async function saveGameProgress(level, score, achievements) {
    try {
      const progress = {
        level: level,
        score: score,
        achievements: achievements,
        lastSaveTime: Date.now()
      };
      const ok = await lingguang.storage.setItem('game_progress', progress);
      if (ok) {
        console.log('游戏进度已保存');
      }
    } catch (err) {
      showError('进度保存失败，请稍后重试'); // ⚠️ 必须告知用户
    }
  }
  ```
{% endif -%}

- **注意事项**：
  - 存储的数据会在用户关闭应用后就会丢失，所以你一定要在应用刚加载初始化时就主动调用getItem获取并加载数据，用户才能看到历史的内容
  - 二次编辑代码新增存储字段或改结构时，务必向前兼容，对缺失字段做默认值兼容（如 `obj?.newField ?? defaultValue`），避免因访问不存在的属性导致加载失败
  - 在用户操作时，你要及时保存数据，防止用户数据丢失
  - 对于频繁更新的数据，可以考虑添加防抖机制，避免过于频繁的存储操作
  - 重要：严格禁止使用浏览器原生方法`alert()`和`confirm()`。由于应用会通过iframe嵌入到移动端APP，原生弹窗可能无法正常显示或阻塞交互。如需提示或确认功能，必须使用自定义的DOM元素（如div+CSS）来实现弹窗效果，确保在iframe环境中正常工作
