---
description: 灵光小程序 API：API_AUDIOCONTEXT2。与 PRD「所需 API 技能」对齐后按需激活。音效播放
---

{% if enable_native_audio_context2_for_mobile -%} {# 移动的1.0.20以上，走native audio context2 #}
- 对于合适使用音效加强交互体验的场景（比如乐器功能演示、音效模拟、音频计算器等），你需要使用AudioContext2来播放音效
  * **重要说明**：由于iOS WebView限制，原生Web Audio API也就是AudioContext是无法正常工作的，必须使用我给你预设好的AudioContext2
  * **引入方式**：AudioContext2已预置在mini_app_base.js中，无需额外引入
  * **使用方法**：与原生AudioContext完全一致，使用`new AudioContext2()`创建实例
  * **支持的方法**：仅支持这四个接口：createOscillator(), createGain(), createBufferSource(), createBuffer()，currentTime（getter），sampleRate（只读属性，值为44100），其他方法不支持会报错
  * **支持的参数控制**：setValueAtTime(), linearRampToValueAtTime(), exponentialRampToValueAtTime()
  * **不支持的方法**：resume(), suspend(), close(), state等原生AudioContext方法
  * **重要限制**：
    - 不支持播放中的动态增益器调整，如需调整音量必须先stop()再重新创建
    - oscillator只能start()一次，stop()后不能重新start()，需要创建新的oscillator
    - oscillator必须连接到audioContext.destination才能播放，否则start()会被忽略，导致无法播放!!oscillator必须连接到audioContext.destination才能播放，否则start()会被忽略，导致无法播放!!
  * **⚠️ 严格限制**：只能使用上述支持的方法，未提供的接口（如createBiquadFilter、createAnalyser、createConvolver、createDelay、createDynamicsCompressor等）会报错，一定一定一定不要使用这些接口！
  * **适用场景**：乐器模拟、音效演示、音频计算器、白噪音播放器、音频可视化应用
  * **注意事项**：
    - 当你用AudioContext2来实现背景音效时，不要在一开始就播放声音，这样容易影响用户体验；你应该做个判断，当用户操作某些按钮进行交互后，再开始播放背景声音。
    - **禁止一次操作播放多个声音**：不要在用户一次操作后连续自动播放多个声音（比如用户按一次按钮，自动播放音阶1-7），这会导致多个oscillator状态管理混乱，stop()无法正确停止所有声音。正确的做法是：每次用户交互只播放一个声音。但是，如果用户连续多次操作（比如用户连续按按钮1、2、3、4、5、6、7），每次操作播放一个声音，这是允许的。
  * **常用音效代码模板**：
{% if use_react_scaffold -%}
    ```tsx
    // 🎹 钢琴按键音效（单次播放）
    function PianoButton({ frequency }: { frequency: number }) {
      const playPianoNote = () => {
        const audioContext = new AudioContext2();
        const oscillator = audioContext.createOscillator();
        const gainNode = audioContext.createGain();
        
        oscillator.type = 'sine';
        oscillator.frequency.setValueAtTime(frequency, audioContext.currentTime);
        
        // 设置音量包络（在播放前设置automation事件）
        gainNode.gain.setValueAtTime(0, audioContext.currentTime);
        gainNode.gain.linearRampToValueAtTime(0.3, audioContext.currentTime + 0.01);
        gainNode.gain.exponentialRampToValueAtTime(0.001, audioContext.currentTime + 1.5);
        
        oscillator.connect(gainNode);
        gainNode.connect(audioContext.destination);
        
        oscillator.start();
        oscillator.stop(audioContext.currentTime + 1.5);
      };

      return <button onClick={playPianoNote}>播放音符</button>;
    }
    
    // 🌧️ 白噪音/雨声（持续播放）
    function WhiteNoisePlayer() {
      const [isPlaying, setIsPlaying] = useState<boolean>(false);
      const playerRef = useRef<any>(null);

      useEffect(() => {
        playerRef.current = {
          audioContext: null,
          gainNode: null,
          oscillator: null,
          start() {
            this.stop();
            
            this.audioContext = new AudioContext2();
            this.gainNode = this.audioContext.createGain();
            this.gainNode.gain.setValueAtTime(0.1, this.audioContext.currentTime);
            this.gainNode.connect(this.audioContext.destination);
            
            this.oscillator = this.audioContext.createOscillator();
            this.oscillator.type = 'sawtooth';
            this.oscillator.frequency.setValueAtTime(200, this.audioContext.currentTime);
            this.oscillator.connect(this.gainNode);
            
            this.oscillator.start();
          },
          stop() {
            if (this.oscillator) {
              this.oscillator.stop();
              this.oscillator = null;
            }
            if (this.audioContext) {
              this.audioContext = null;
            }
          }
        };
      }, []);

      const handleToggle = () => {
        if (isPlaying) {
          playerRef.current.stop();
          setIsPlaying(false);
        } else {
          playerRef.current.start();
          setIsPlaying(true);
        }
      };

      return <button onClick={handleToggle}>{isPlaying ? '停止' : '开始'}</button>;
    }
    
    // 🎵 多音效切换器（雨声/海浪/森林）
    function SoundSwitcher() {
      const [currentSound, setCurrentSound] = useState<string | null>(null);
      const switcherRef = useRef<any>(null);

      useEffect(() => {
        switcherRef.current = {
          audioContext: null,
          currentOscillator: null,
          playSound(type: string) {
            this.stop();
            
            if (!this.audioContext) {
              this.audioContext = new AudioContext2();
            }
            
            const oscillator = this.audioContext.createOscillator();
            const gainNode = this.audioContext.createGain();
            
            switch(type) {
              case 'rain':
                oscillator.type = 'sawtooth';
                oscillator.frequency.setValueAtTime(200, this.audioContext.currentTime);
                gainNode.gain.setValueAtTime(0.15, this.audioContext.currentTime);
                break;
              case 'ocean':
                oscillator.type = 'triangle';
                oscillator.frequency.setValueAtTime(150, this.audioContext.currentTime);
                gainNode.gain.setValueAtTime(0.24, this.audioContext.currentTime);
                break;
              case 'forest':
                oscillator.type = 'square';
                oscillator.frequency.setValueAtTime(300, this.audioContext.currentTime);
                gainNode.gain.setValueAtTime(0.09, this.audioContext.currentTime);
                break;
            }
            
            oscillator.connect(gainNode);
            gainNode.connect(this.audioContext.destination);
            oscillator.start();
            
            this.currentOscillator = oscillator;
          },
          stop() {
            if (this.currentOscillator) {
              this.currentOscillator.stop();
              this.currentOscillator = null;
            }
          }
        };
      }, []);

      const handlePlay = (type: string) => {
        switcherRef.current.playSound(type);
        setCurrentSound(type);
      };

      return (
        <div>
          <button onClick={() => handlePlay('rain')}>雨声</button>
          <button onClick={() => handlePlay('ocean')}>海浪</button>
          <button onClick={() => handlePlay('forest')}>森林</button>
        </div>
      );
    }
    ```
{% else -%}
    ```javascript
    // 🎹 钢琴按键音效（单次播放）
    function playPianoNote(frequency) {
        const audioContext = new AudioContext2();
        const oscillator = audioContext.createOscillator();
        const gainNode = audioContext.createGain();
        
        oscillator.type = 'sine';
        oscillator.frequency.setValueAtTime(frequency, audioContext.currentTime);
        
        // 设置音量包络（在播放前设置automation事件）
        gainNode.gain.setValueAtTime(0, audioContext.currentTime);
        gainNode.gain.linearRampToValueAtTime(0.3, audioContext.currentTime + 0.01);
        gainNode.gain.exponentialRampToValueAtTime(0.001, audioContext.currentTime + 1.5);
        
        oscillator.connect(gainNode);
        gainNode.connect(audioContext.destination);
        
        oscillator.start();
        oscillator.stop(audioContext.currentTime + 1.5);
    }
    
    // 🌧️ 白噪音/雨声（持续播放）
    class WhiteNoisePlayer {
        constructor() {
            this.audioContext = null;
            this.gainNode = null;
            this.oscillator = null;
        }
        
        start() {
            this.stop(); // 先停止之前的
            
            this.audioContext = new AudioContext2();
            this.gainNode = this.audioContext.createGain();
            this.gainNode.gain.setValueAtTime(0.1, this.audioContext.currentTime);
            this.gainNode.connect(this.audioContext.destination);
            
            this.oscillator = this.audioContext.createOscillator();
            this.oscillator.type = 'sawtooth';
            this.oscillator.frequency.setValueAtTime(200, this.audioContext.currentTime);
            this.oscillator.connect(this.gainNode);
            
            this.oscillator.start();
        }
        
        stop() {
            if (this.oscillator) {
                this.oscillator.stop();
                this.oscillator = null;
            }
            if (this.audioContext) {
                this.audioContext = null;
            }
        }
    }
    
    // 🎵 多音效切换器（雨声/海浪/森林）
    class SoundSwitcher {
        constructor() {
            this.audioContext = null;
            this.currentOscillator = null;
        }
        
        playSound(type) {
            this.stop();
            
            // 只创建一次AudioContext2
            if (!this.audioContext) {
                this.audioContext = new AudioContext2();
            }
            
            // 为每种音效创建独立的oscillator和gainNode
            const oscillator = this.audioContext.createOscillator();
            const gainNode = this.audioContext.createGain();
            
            switch(type) {
                case 'rain':
                    oscillator.type = 'sawtooth';
                    oscillator.frequency.setValueAtTime(200, this.audioContext.currentTime);
                    gainNode.gain.setValueAtTime(0.15, this.audioContext.currentTime);
                    break;
                case 'ocean':
                    oscillator.type = 'triangle';
                    oscillator.frequency.setValueAtTime(150, this.audioContext.currentTime);
                    gainNode.gain.setValueAtTime(0.24, this.audioContext.currentTime);
                    break;
                case 'forest':
                    oscillator.type = 'square';
                    oscillator.frequency.setValueAtTime(300, this.audioContext.currentTime);
                    gainNode.gain.setValueAtTime(0.09, this.audioContext.currentTime);
                    break;
            }
            
            oscillator.connect(gainNode);
            gainNode.connect(this.audioContext.destination);
            oscillator.start();
            
            this.currentOscillator = oscillator;
        }
        
        stop() {
            if (this.currentOscillator) {
                this.currentOscillator.stop();
                this.currentOscillator = null;
            }
        }
    }
    ```
{% endif -%}
{% else -%} {# PC或低版本移动端，使用Web Audio API #}
- 对于合适使用音效加强交互体验的场景（比如乐器功能演示、音效模拟、音频计算器等），你需要使用Web Audio API来播放音效
  * **重要说明**：PC端或低版本移动端可以使用原生Web Audio API（AudioContext）
  * **使用方法**：使用`new AudioContext()`创建实例
  * **注意事项**：
    - 当你用Web Audio API来实现背景音效时，不要在一开始就播放声音，这样容易影响用户体验；你应该做个判断，当用户操作某些按钮进行交互后，再开始播放背景声音。
{% endif -%}
