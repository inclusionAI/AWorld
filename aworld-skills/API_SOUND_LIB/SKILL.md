---
description: 灵光小程序 API：API_SOUND_LIB。与 PRD「所需 API 技能」对齐后按需激活。音乐素材库（预置音效）
---

# 音乐素材库（预置音效）

## 适用场景

当应用需要以下能力时使用本素材库：
- **短音效**：按钮点击、翻页、水滴、掌声、骰子、卡牌、快门、键盘等交互反馈，无需生成、无需合成。
- **BGM / 氛围**：欢快、紧张、浪漫、冥想、白噪音（火焰、鸟鸣、风铃、夜晚等），可直接循环播放。

**时长说明**：下表「时长」列为实际解析结果，选音效时可参考（如按钮反馈优先选 &lt;2s）。
- **交互反馈音效**：短促（0.3s–2s）适合点击反馈，较长（5s–26s）适合结果/氛围。
- **BGM**：可循环，建议用 `<audio loop>` 或播放结束后再次 `play()` 实现循环。

## 使用方式

预置音效以固定 URL 提供，在应用内通过 **HTML5 Audio** 播放即可，无需调用生成类或合成类 API：

```typescript
// 单次播放
const audio = new Audio(url);
audio.play();

// 或 React 中
<audio src={url} onEnded={() => {}} />
```

**建议**：将下方「预置音效 id」与 URL 的映射放在常量或工具函数中（如 `getPresetSoundUrl(id)`），便于维护与复用。

---

## 预置音效清单

### 交互反馈音效

| id（代码中建议使用） | 中文描述 | 时长 | URL |
|---------------------|----------|------|-----|
| heart_beats | 【拟音】心跳的声音 | 11.3s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/heartbeat-sound-effects-for-you-122458.1770035046917.mp3 |
| breathing_asmr | 【拟音】呼吸声 | 18.3s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/gentle-breathing-asmr-for-relaxation-423904.1770035047856.mp3 |
| paper_crumple | 【动作】揉皱纸张 | 16.9s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/paper-crumble-62089.1770035049158.mp3 |
| bubble_wrap | 【动作】捏气泡纸，解压 | 15.8s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/bubble-wrap-411646.1770035050559.mp3 |
| mechanical_keyboard | 【动作】机械键盘打字声 | 8.1s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/mechanical-keyboard-typing-sound-effect-hd-379363.1770035052560.mp3 |
| coins | 【动作】撒钱的声音，金币碰撞的感觉 | 9.1s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/coins-104813.1770035054004.mp3 |
| rattle | 【动作】摇晃的声音，就像摇签的声音 | 12.3s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/rattle-46377.1770035055469.mp3 |
| power_up_strike | 【动作】蓄力攻击 | 2.9s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/power-up-strike-446145.1770035057001.mp3 |
| water_drops | 【拟音】水滴落下的声音 | 6.4s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/water-drops-6223.1770035057785.mp3 |
| paper_flip | 【动作】翻页的声音 | 1.4s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/paper-245786.1770035058836.mp3 |
| camera_shutter | 【拟音】相机快门声 | 0.3s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/camera-shutter-6305.1770035059936.mp3 |
| applause | 【动作】掌声 | 26.1s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/applause.1770035060502.mp3 |
| mouse_click | 【动作】鼠标点击声 | 0.4s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/mouse-click-290204.1770035062038.mp3 |
| toilet_flush | 【拟音】马桶冲水声 | 8.7s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/toilet-flush-fx-5-424984.1770035063131.mp3 |
| dial_tick | 【拟音】机械旋钮调节声 | 14.5s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/telefono-a-disco_tono-de-espera-discado-disk-phone-tone-dial-218134.1770035064700.mp3 |
| wiping_glass | 【动作】擦玻璃声 | 14.8s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/preview.1770035066420.mp3 |
| dog_bark | 【拟音】狗吠 | 5.4s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/dog-bark-sound-450454.1770035067570.mp3 |
| cat_meowing | 【拟音】猫叫 | 1.3s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/cat-meowing-type-02-293290.1770035069118.mp3 |
| winter_wind | 【拟音】风声 | 19.0s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/winter-wind-402331.1770035070634.mp3 |
| pull_refresh | 【动作】拉绳开灯 | 15.2s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/light-pull-string-32448.1770035072395.mp3 |
| creaky_door | 【动作】开门声 | 1.0s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/creaky-old-door-472357.1770035073909.mp3 |
| clock_ticking | 【拟音】倒计时钟声 | 8.1s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/slow-cinematic-clock-ticking-357979.1770035075036.mp3 |
| dice | 【拟音】骰子 | 1.4s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/dice-142528.1770035076241.mp3 |
| cards_dealt | 【拟音】翻牌、卡牌声 | 25.7s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/playing-cards-being-dealt-27024.1770035077369.mp3 |

### BGM / 氛围（可循环）

| id（代码中建议使用） | 中文描述 | 时长 | URL |
|---------------------|----------|------|-----|
| weekend_fun | 【情绪】欢度春节 | 1m20s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/festival-of-fortune-289462.1770035078259.mp3 |
| valentines_day | 【情绪】浪漫情人节 | 1m10s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/valentinex27s-day-classical-love-loop-no2-473298.1770035081875.mp3 |
| happy_funny | 【情绪】搞笑欢快 | 2m57s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/happy-funny-music-461872.1770035084257.mp3 |
| moody_sad_piano | 【情绪】忧郁的钢琴曲 | 1m6s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/moody-sad-piano-463120.1770035087722.mp3 |
| suspense | 【情绪】紧张刺激 | 1m35s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/suspense-460022.1770035090073.mp3 |
| mysterious_dark | 【情绪】恐怖场景，暗黑感 | 1m51s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/mysterious-dark-background-09-408661.1770035092946.mp3 |
| lofi_chill | 【情绪】忧郁 | 4m12s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/lofi-moody-dreams-chill-atmosphere-326690.1770035095355.mp3 |
| love_romantic | 【情绪】婚礼，充满希望，爱情浪漫 | 2m12s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/love-romantic-hopeful-music-333017.1770035099655.mp3 |
| japanese_piano | 【情绪】春天、日式、钢琴音乐 | 52.0s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/japanese-piano-318090.1770035102178.mp3 |
| suspense_trailer | 【情绪】悬念，紧张 | 2m16s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/suspense-tension-trailer-2-345353.1770035104104.mp3 |
| berceuse | 【情绪】舒缓摇篮曲 | 1m53s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/musique-de-la-berceuse-338570.1770035110296.mp3 |
| meditation | 【情绪】冥想背景音 | 2m57s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/meditation-background-434654.1770035112439.mp3 |
| leaves_crunch | 【白噪音】轻踏落叶声 | 8.1s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/walking-through-leaves-44621.1770035115626.mp3 |
| fire | 【白噪音】火焰声 | 17.2s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/fire-crackling-sounds-427410.1770035116495.mp3 |
| thunderstruck | 【白噪音】打雷海边 | 7.0s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/thunderstruck-96354.1770035117315.mp3 |
| bird_life | 【白噪音】树林鸟鸣 | 7m39s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/birdlife-at-malsjon-in-kristdala-sweden-6370.1770035118470.mp3 |
| wind_chimes | 【白噪音】风铃声 | 8.1s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/wind-chimes-2-199848.1770035123720.mp3 |
| night_sound | 【白噪音】夜晚 | 1m23s | https://mdn.alipayobjects.com/asap_serivce/uri/file/as/night-sound-frog-sound-8907.1770035124687.mp3 |

---

## 使用示例

{% if use_react_scaffold -%}
**React：按钮点击播放短音效**
```tsx
const SOUND_URLS: Record<string, string> = {
  mouse_click: 'https://mdn.alipayobjects.com/asap_serivce/uri/file/as/mouse-click-290204.1770035062038.mp3',
  applause: 'https://mdn.alipayobjects.com/asap_serivce/uri/file/as/applause.1770035060502.mp3',
  // ... 其他 id 见上表
};

function ButtonWithSound() {
  const playSound = (id: keyof typeof SOUND_URLS) => {
    const url = SOUND_URLS[id];
    if (url) new Audio(url).play();
  };
  return <button onClick={() => playSound('mouse_click')}>点击</button>;
}
```

**BGM 循环播放**
```tsx
const [bgmAudio, setBgmAudio] = useState<HTMLAudioElement | null>(null);

useEffect(() => {
  const audio = new Audio('https://mdn.alipayobjects.com/asap_serivce/uri/file/as/meditation-background-434654.1770035112439.mp3');
  audio.loop = true;
  setBgmAudio(audio);
  return () => { audio.pause(); };
}, []);

const toggleBgm = () => {
  if (!bgmAudio) return;
  if (bgmAudio.paused) bgmAudio.play();
  else bgmAudio.pause();
};
```
{% else %}
**单次播放短音效**
```javascript
const url = 'https://mdn.alipayobjects.com/asap_serivce/uri/file/as/mouse-click-290204.1770035062038.mp3';
new Audio(url).play();
```

**BGM 循环**
```javascript
const audio = new Audio('https://mdn.alipayobjects.com/asap_serivce/uri/file/as/meditation-background-434654.1770035112439.mp3');
audio.loop = true;
audio.play();
```
{% endif %}

---

## 注意事项

- 预置音效仅支持通过上述 URL 用 `<audio>` 或 `new Audio()` 播放，无需、也不要为这些固定音效调用 MUSICGEN 或 AUDIOCONTEXT2。
- 交互反馈类音效建议在用户操作时单次播放，避免自动连续播放多个音效造成干扰。
- BGM 建议设置 `loop: true` 或播放结束后再次 `play()` 以循环；音量可适当降低以免盖过主交互。
