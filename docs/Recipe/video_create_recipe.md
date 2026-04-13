# Video Generation Recipe

This recipe explains how to use AWorld-CLI for **one-shot video generation from a single user prompt**.  
Current product focus is: user writes one paragraph of requirements, then AWorld generates the video directly.

## Scope and Current Workflow

- Provide one prompt (plus optional local materials), and AWorld executes the full video generation flow directly.

## Video Types Supported

The current video capabilities align with the README "Create Video" section:

1. Trig-Identity video
2. Corporate Training video
3. Brand Marketing video
4. Social Media video
5. Vtuber video

## Install and Configure AWorld-CLI

Installation and activation steps remain the same as the main README:

- [Your Journey with AWorld-CLI](../../README.md#your-journey-with-aworld-cli)
- [Environment configuration](../../README_env_config.md)

During `aworld-cli --config`, recommended models are:

- `video_diffusion`: `kling-v3`
- `audio_generator`: `doubao-tts`
- `aworld/developer/evaluator`: `claude-sonnet-4.5`

## How to Run

1. Install AWorld + AWorld-CLI and complete `aworld-cli --config`.
2. Put required local assets/materials into your current working directory when needed.
3. Start CLI:

```bash
aworld-cli
```

4. Paste your prompt in one shot and wait for the generation result.

## Example Inputs and Required Materials

The following table lists 5 practical examples, including exact input prompt and required auxiliary files.

| Example | Input Prompt | Required Auxiliary Files / Preconditions |
|---|---|---|
| 1) Trig-Identity | `Please use the remotion-best-practice skill and create a video via code to explain the derivation that sin^2 + cos^2 = 1 using a circle-based geometric demonstration. The video should include intuitive mathematical and geometric visual mechanisms. 1080p, 30fps, duration decided by you but less than 20 seconds. No other input materials are provided.` | No extra materials required. |
| 2) Corporate Training | `Please read a training document named raw_document, summarize its core content, and turn it into a 5-10 second video. You may use the remotion-best-practice skill to help produce a high-quality result.` | User must place the training material file (`raw_document`) in current directory in advance. |
| 3) Brand Marketing | `Please use a clothing image as input and let video_diffusion produce a 1080p, 24fps, 10-second video. If the clothing photo is not 16:9, extend it with a pure white background to fit 16:9. Shoot a marketing ad for this dress: keep the dress centered at all times, and use hard cuts to show different girls (skin tone, hairstyle, accessories) wearing it. The overall style should be youthful and cute. Include visual factors such as product appearance, dynamic visuals, and close-up details. A hard cut every 2 seconds is acceptable. Keep the background consistent (for example, the same studio or a spacious fitting room). Do not let the model's head touch the top frame boundary. Please add background music (download it yourself into the current working directory) and merge it into the generated video.` | User must place: (1) one clothing image, (2) one background music file downloaded to current directory. |
| 4) Social Media | `Please let video_diffusion make a 4:3 video using this image as input, 5-10 seconds, 24fps, and produce a social-media influencer-style operation video for this kitten.` | User must place (1) one cat image, (2) one background music file in current directory as source material. |
| 5) Vtuber | `Call video_diffusion to generate a 10-second, 720p, 24fps video showing an animal anchor (a lark) at the news desk, speaking to camera. The background and camera must remain static, and only the anchor's mouth should move quickly, in a 3D Pixar-style look. Name this video lark.mp4. In parallel, call video_diffusion to generate another 10-second, 720p, 24fps video where an elephant and a giraffe are fighting, also in 3D Pixar style. Name it news.mp4. Then follow the embeded video skill to embed the anchor video (lark) into the bottom-right corner of the news video. Next, call audio generator to create a 9.5-second mp3 in the anchor's voice, delivering a humorous news broadcast. Finally, merge the mp3 into the video.` | No user-provided media required by default. Uses built-in generation + embedded video skill + audio generator flow. |

## Notes

- Prompt text can be used directly as shown above.
- When a case needs local files, make sure those files are already in your current directory before running.
- For feature updates, always refer to [README](../../README.md) as the source of truth.
