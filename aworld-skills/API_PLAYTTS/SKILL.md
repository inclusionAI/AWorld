---
description: 灵光小程序 API：API_PLAYTTS。与 PRD「所需 API 技能」对齐后按需激活。文本朗读
---

{% if os_type != 'PC' and os_type != 'HARMONY' -%} {# 只有移动且非鸿蒙系统才需要使用native tts语音 #}
- 对于合适使用文字类阅读交互的场景（比如绘本阅读，夜间小故事等等涉及到需要文本发音阅读的场景），你需要使用给你预置好的语音播放js库（注意不能使用speechSynthesis等原生的tts进行播放，否则会播放不出来声音）
{% if enable_tts_voice_type -%} {# 版本1.0.32及以上支持语言和音色选择 #}
  * **核心接口**：`window.playTTS(text, options)`，返回Promise
    - `text` (string, 必填): 要播放的文本内容
    - `options` (object, 可选): 播放选项
      - `options.lang` (string, 可选): 语言代码
        - 支持的语言：`'zh'`（中文）、`'en'`（英语）、`'yue'`（粤语）、`'jp'`（日语）、`'ko'`（韩语）、`'fr'`（法语）、`'es'`（西班牙语）、`'de'`（德语）、`'ru'`（俄语）、`'ar'`（阿拉伯语）、`'it'`（意大利语）、`'pt'`（葡萄牙语）、`'pl'`（波兰语）
        - 不传或传 `'auto'`：默认为中文+英文同时支持
      - `options.voiceType` (string, 可选): 音色类型
        - `'fairy_tale'`: 童话故事音色（适合绘本、儿童故事）
        - `'short_play'`: 短剧音色（适合对话、角色扮演）
        - `'common'`: 普通合成音色（适合一般文本）
        - 不传: 使用默认音色
{% else -%}
  * **核心接口**：`window.playTTS(text)`，返回Promise
    - `text` (string, 必填): 要播放的文本内容
{% endif -%}
  * **成功/失败判断**：
    - ✅ **成功**：播放完成时 Promise resolve
    - ❌ **失败**：播放失败或被停止时 Promise reject
  * **注意事项**：
    - **⚠️ 重要：函数访问方式**：`playTTS` 和 `stopAllTTS` 函数挂载在 `window` 对象上，你必须使用 `window.playTTS` 和 `window.stopAllTTS` 来调用，不能直接使用 `playTTS` 或 `stopAllTTS`（否则会报错"函数未定义"）
    - 每次调用 `window.playTTS()` 只能播放一个句子！！所以不要在一次调用中传入过长的文本，否则会导致系统崩溃！！如果有大段落需要连续播放，你需要按句号、问号、感叹号等句子结束符分割成多个句子，然后在代码中通过 `await` 顺序调用多次 `window.playTTS()`
    - **暂停/继续功能实现**：如果你判断需要支持暂停/继续播放功能（比如连续播放一大段文本段落时），你需要自己管理播放状态和进度：
      - 将文本按句号、问号、感叹号等句子结束符分割成数组，记录当前播放到第几句（`currentIndex`）
      - 用户点击暂停时，立即调用 `window.stopAllTTS()` 停止当前播放，并保存当前播放进度
      - 用户点击继续时，从保存的 `currentIndex` 位置开始，继续调用 `window.playTTS()` 播放剩余句子
    - **⚠️ 重要：`window.stopAllTTS()` 的使用时机**：
      - ✅ **正确**：仅在用户主动点击停止按钮时调用 `window.stopAllTTS()`
      - ✅ **正确**：在播放出错需要清理时调用 `window.stopAllTTS()`
      - ❌ **错误**：播放正常完成后（Promise resolve）不要调用 `window.stopAllTTS()`，因为播放已经自动结束，此时调用可能会导致错误提示。正常完成后你按需执行后续逻辑即可。
    - **绝对禁止**：
      - 绝对禁止编造不存在的api！！！ 你能用的就只有 `window.playTTS()` 和 `window.stopAllTTS()` 这两个接口，其他都绝对禁止使用
      - 绝对禁止使用speechSynthesis等原生的tts进行播放，否则会播放不出来声音！！禁止使用new TTS()的原生tts调用，这会导致完全没有声音！！
      - 绝对禁止直接使用 `playTTS` 或 `stopAllTTS`（不加 `window.` 前缀），会导致"函数未定义"错误
{% if enable_tts_voice_type -%} {# 版本1.0.32及以上支持语言和音色选择 #}
    - **语言和音色选择**：
      - **语言选择**：
        - 根据文本内容选择合适的语言代码，可以提升发音准确性
        - 支持13种语言：中文（zh）、英语（en）、粤语（yue）、日语（jp）、韩语（ko）、法语（fr）、西班牙语（es）、德语（de）、俄语（ru）、阿拉伯语（ar）、意大利语（it）、葡萄牙语（pt）、波兰语（pl）
        - 如果文本是中文，使用 `lang: 'zh'`
        - 如果文本是英语，使用 `lang: 'en'`
        - 如果文本是其他语言，使用对应的语言代码
        - 如果不确定语言，可以不传或传 `'auto'`，系统会自动以最常见的中文+英文混杂播放
      - **音色选择**：
        - 根据内容类型选择合适的音色，可以提升用户体验
        - 童话故事、绘本等场景使用 `voiceType: 'fairy_tale'`
        - 对话、角色扮演等场景使用 `voiceType: 'short_play'`
        - 一般文本使用 `voiceType: 'common'` 或不传（使用默认）
        - 同一段连续播放的文本，建议使用相同的音色配置，保持音色一致性
{% endif -%}
  * **使用示例**：
{% if use_react_scaffold -%}
    ```tsx
    import { useRef } from 'react';

    // part1. 基本使用
    function App() {
      const handlePlay = async () => {
        // 直接调用单句（注意：必须使用 window.playTTS）
        await window.playTTS('你好，世界');
      };

      const handleStop = () => {
        // 用户主动调用停止/暂停时调用stopAllTTS（正常播放完成的话，不需要调用此接口！）
        // 注意：必须使用 window.stopAllTTS
        window.stopAllTTS();
      };

      return (
        <div>
          <button onClick={handlePlay}>播放</button>
          <button onClick={handleStop}>停止</button>
        </div>
      );
    }

    // part2. 从数组循环播放
    function PlayList() {
      const sentences: string[] = ['第一句话', '第二句话', '第三句话'];

      const handlePlay = async () => {
        for (let i = 0; i < sentences.length; i++) {
          try {
            await window.playTTS(sentences[i]);
          } catch (error: unknown) {
            const msg = error instanceof Error ? error.message : '未知错误';
            console.error('播放失败:', msg);
            break;
          }
        }
      };

      return <button onClick={handlePlay}>开始播放</button>;
    }

    // part3. 暂停/继续播放高级功能示例
    function PlaybackControl() {
      const currentIndexRef = useRef<number>(0);
      const isPlayingRef = useRef<boolean>(false);
      const textArray: string[] = ['第一句话', '第二句话', '第三句话', '第四句话'];

      // 开始/继续播放
      const startPlayback = async () => {
        if (isPlayingRef.current) return;
        isPlayingRef.current = true;

        for (let i = currentIndexRef.current; i < textArray.length; i++) {
          try {
            await window.playTTS(textArray[i]);
            currentIndexRef.current = i + 1;
          } catch (error: unknown) {
            if (error instanceof Error && error.message === 'TTS播放被停止') {
              // 用户主动停止，退出循环但不重置索引
              break;
            } else {
              const msg = error instanceof Error ? error.message : '未知错误';
              console.error('播放失败:', msg);
              break;
            }
          }
        }

        isPlayingRef.current = false;
        if (currentIndexRef.current >= textArray.length) {
          currentIndexRef.current = 0; // 播放完成，重置索引
        }
      };

      // 暂停播放
      const pausePlayback = () => {
        window.stopAllTTS();
        isPlayingRef.current = false;
        // currentIndexRef.current 保持不变，下次继续从这里播放
      };

      return (
        <div>
          <button onClick={startPlayback}>开始/继续</button>
          <button onClick={pausePlayback}>暂停</button>
        </div>
      );
    }

{% if enable_tts_voice_type -%} {# 版本1.0.32及以上支持语言和音色选择 #}
    // part4. 语言和音色选择示例
    function VoiceExample() {
      const handlePlay = async () => {
        // 指定语言
        await window.playTTS('Hello, world', { lang: 'en' });
        await window.playTTS('你好，世界', { lang: 'zh' });
        await window.playTTS('안녕하세요', { lang: 'ko' });
        await window.playTTS('Bonjour', { lang: 'fr' });
        await window.playTTS('Hola', { lang: 'es' });

        // 指定音色
        await window.playTTS('从前有一座山', { voiceType: 'fairy_tale' });
        await window.playTTS('你好，我是小明', { voiceType: 'short_play' });
        await window.playTTS('这是一段普通文本', { voiceType: 'common' });

        // 同时指定语言和音色
        await window.playTTS('从前有一座山', { lang: 'zh', voiceType: 'fairy_tale' });
        await window.playTTS('Hello, world', { lang: 'en', voiceType: 'common' });
        await window.playTTS('안녕하세요', { lang: 'ko', voiceType: 'short_play' });

        // 连续播放时同时指定语言和音色
        const englishSentences = ['Hello', 'How are you', 'Nice to meet you'];
        for (let sentence of englishSentences) {
          await window.playTTS(sentence, { lang: 'en', voiceType: 'common' });
        }

        const fairyTaleSentences = ['从前有一座山', '山上有一座庙', '庙里有一个老和尚'];
        for (let sentence of fairyTaleSentences) {
          await window.playTTS(sentence, { lang: 'zh', voiceType: 'fairy_tale' });
        }
      };

      return <button onClick={handlePlay}>播放示例</button>;
    }
{% endif -%}
    ```
{% else -%}
    ```javascript
    // part1. 基本使用

    // 直接调用单句（注意：必须使用 window.playTTS）
    await window.playTTS('你好，世界');

    // 用户主动调用停止/暂停时调用stopAllTTS（正常播放完成的话，不需要调用此接口！）
    // 注意：必须使用 window.stopAllTTS
    window.stopAllTTS();
    
    // part2. 从数组循环播放
    const sentences = ['第一句话', '第二句话', '第三句话'];
    for (let i = 0; i < sentences.length; i++) {
      try {
        await window.playTTS(sentences[i]);
      } catch (error) {
        console.error('播放失败:', error.message);
        break;
      }
    }

    // part3. 暂停/继续播放高级功能示例
    const textArray = ['第一句话', '第二句话', '第三句话', '第四句话'];
    let currentIndex = 0;
    let isPlaying = false;

    // 开始/继续播放
    async function startPlayback() {
      if (isPlaying) return;
      isPlaying = true;

      for (let i = currentIndex; i < textArray.length; i++) {
        try {
          await window.playTTS(textArray[i]);
          currentIndex = i + 1;
        } catch (error) {
          if (error.message === 'TTS播放被停止') {
            // 用户主动停止，退出循环但不重置索引
            break;
          } else {
            console.error('播放失败:', error.message);
            break;
          }
        }
      }

      isPlaying = false;
      if (currentIndex >= textArray.length) {
        currentIndex = 0; // 播放完成，重置索引
      }
    }
    
    // 暂停播放
    function pausePlayback() {
      window.stopAllTTS();
      isPlaying = false;
      // currentIndex 保持不变，下次继续从这里播放
    }
    
{% if enable_tts_voice_type -%} {# 版本1.0.32及以上支持语言和音色选择 #}
    // part4. 语言和音色选择示例
    
    // 指定语言
    await window.playTTS('Hello, world', { lang: 'en' });
    await window.playTTS('你好，世界', { lang: 'zh' });
    await window.playTTS('안녕하세요', { lang: 'ko' });
    await window.playTTS('Bonjour', { lang: 'fr' });
    await window.playTTS('Hola', { lang: 'es' });
    
    // 指定音色
    await window.playTTS('从前有一座山', { voiceType: 'fairy_tale' });
    await window.playTTS('你好，我是小明', { voiceType: 'short_play' });
    await window.playTTS('这是一段普通文本', { voiceType: 'common' });
    
    // 同时指定语言和音色
    await window.playTTS('从前有一座山', { lang: 'zh', voiceType: 'fairy_tale' });
    await window.playTTS('Hello, world', { lang: 'en', voiceType: 'common' });
    await window.playTTS('안녕하세요', { lang: 'ko', voiceType: 'short_play' });
    
    // 连续播放时同时指定语言和音色
    const englishSentences = ['Hello', 'How are you', 'Nice to meet you'];
    for (let sentence of englishSentences) {
      await window.playTTS(sentence, { lang: 'en', voiceType: 'common' });
    }
    
    const fairyTaleSentences = ['从前有一座山', '山上有一座庙', '庙里有一个老和尚'];
    for (let sentence of fairyTaleSentences) {
      await window.playTTS(sentence, { lang: 'zh', voiceType: 'fairy_tale' });
    }
{% endif -%}
    ```
{% endif -%}
{% else -%}
- PC端和鸿蒙系统目前不支持tts能力，不要尝试使用tts能力，否则会播放不出来声音！！
{% endif -%}
