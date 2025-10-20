# AWorld BrowserUse Agent for VisualWebArena Benchmark

## Introduction

This repository is the example of Recon-Act's Action Team inference code on the VisualWebArena benchmark.
The Recon-Act Action Team infers the process empowered with targeting tools generated from the Recon-Act Reconnaissance Team, and achieves a 36.48% success rate, outperforming the other automated agents. Despite requiring a moderate number of steps, Recon-Act achieves stable web navigation with only minimal self-corrective actions. 
Please kindly refer to our [paper](https://arxiv.org/abs/2509.21072) for more details. 

## 🚀 Leaderboard Highlights

Please visit the [VisualWebArena leaderboard](https://docs.google.com/spreadsheets/d/1M801lEpBbKSNwP-vDBkC_pF7LdyGU1f_ufZb_NWNBZQ/edit?gid=2044883967#gid=2044883967) to check our current ranking (aworld). The table below shows (as of Sept 25, 2025) the success rates of baseline LLM and VLM agents on VisualWebArena.

| Paper | Method | Model | Classifieds (%) | Reddit (%) | Shopping (%) | Overall (%) |
|-------|--------|-------|-----------------|------------|--------------|-------------|
| [VWA](https\://github.com/web-arena-x/visualwebarena) | Multimodel (SoM) Image + Caps + SoM | Gemini-Pro | 3.42 | 3.81 | 7.73 | 5.71 |
| [VWA](https\://github.com/web-arena-x/visualwebarena) | Multimodel Image + Caps + Acc. Tree | Gemini-Pro | 3.42 | 4.29 | 8.15 | 6.04 |
| [VWA](https\://github.com/web-arena-x/visualwebarena) | Text-only Acc. Tree | GPT-4 | 5.56 | 4.76 | 9.23 | 7.25 |
| [VWA](https\://github.com/web-arena-x/visualwebarena) | Caption-augmented Acc. Tree + Caps | GPT-4 + BLIP-2-T5XL | 8.55 | 8.57 | 16.74 | 12.75 |
| [VWA](https\://github.com/web-arena-x/visualwebarena) | Multimodel Image + Caps + Acc. Tree | GPT-4V | 8.12 | 12.38 | 19.74 | 15.05 |
| [VWA](https\://github.com/web-arena-x/visualwebarena) | Multimodel (SoM) Image + Caps + SoM | GPT-4V | 9.83 | 17.14 | 19.31 | 16.37 |
| [WebDreamer](https://arxiv.org/abs/2411.06559) | - | Qwen2-VL-7B | 17.9 | 11.1 | 20.2 | 17.20 |
| [WebDreamer](https://arxiv.org/abs/2411.06559) | - | Qwen2-VL-72B | 19.6 | 15.9 | 24.6 | 21.00 |
| [WebDreamer](https://arxiv.org/abs/2411.06559) | - | Dreamer-7B | 21.4 | 15.9 | 25.4 | 21.90 |
| [ICAL](https://arxiv.org/abs/2406.14596) | - | GPT-4V | - | - | - | 22.70 |
| [WebDreamer](https://arxiv.org/abs/2411.06559) | - | Dreamer-7B + In-Domain | 25.0 | 15.9 | 26.3 | 23.20 |
| [WebDreamer](https://arxiv.org/abs/2411.06559) | - | GPT-4o | 23.2 | 17.5 | 26.3 | 23.20 |
| [ICAL](https://arxiv.org/abs/2406.14596) | - | GPT-4o | - | - | - | 23.40 |
| [TreeSearch](https://jykoh.com/search-agents) | Search + SoM | GPT-4o | 26.5 | 20.5 | 29.0 | 26.40 |
| [ExAct](https://agent-e3.github.io/ExACT/) | MCTS SA SoM + Caption + Image | GPT-4o | 37.6 | 23.8 | 29.4 | 30.22 |
| [ExAct](https://agent-e3.github.io/ExACT/) | R-MCTS SA SoM + Caption + Image | GPT-4o | 40.2 | 25.2 | 31.9 | 32.53 |
| [ExAct](https://agent-e3.github.io/ExACT/) | R-MCTS MAD SoM + Caption + Image | GPT-4o | **41.0** | **28.7** | 32.3 | 33.74 |
| Ours | Recon-Act | GPT-5-Chat | 39.32 | 27.14 | **39.27** | **36.48** |
| Human | - | - | 91.07 | 87.10 | 88.39 | 88.70 |

## File Structure
```
├── action_team.py # Action team logic for reasoning and action generation
├── agent_config.py # Get agent VLM config
├── aw_agent.py # Core code for our agent
├── bu_mas_prompts.py # Prompts for mas system
├── decision_tools # Decision tools registration folder
│   ├── AuthorFinder.py
│   ├── CategoryGuide.py
│   ├── ClassifiedsPriceSorter.py
│   ├── DownVoter.py
│   ├── ......
│   └── UpVoter.py
├── hint_tools # Hint tools registration folder
│   ├── PostTimeFinder.py
│   └── RedditImageDescriptor.py
├── get_messages.py # Default message contruction
├── kutils.py # Log scripts
├── tool_manager.py # Loads/dispatches hint & decision tools
└── utils.py # Some common utils
```

## Installation

1. VisualWebArena Environment: Please follow https://github.com/web-arena-x/visualwebarena to complete all installation steps (docker, codebase, data generation etc.).

1. Setup dataset folder

    ```
    mkdir <dataset_folder>
    mv visualwebarena/environment_docker/webarena-homepage/static <dataset_folder>
    mv visualwebarena/config_files <dataset_folder>
    ```

    the dataset folder should looks like:
    ```
    ├── config_files
    │   ├── vwa
    │   │   ├── test_classifieds
    │   │   ├── test_classifieds.json
    │   │   ├── test_classifieds.raw.json
    │   │   ├── test_reddit
    │   │   ├── test_reddit.json
    │   │   ├── test_reddit.raw.json
    │   │   ├── test_shopping
    │   │   ├── test_shopping.json
    │   │   └── test_shopping.raw.json
    │   └── wa
    │       └── test_webarena.raw.json
    └── static
        ├── figures
        │   ├── calculator.png
        │   ├── classifieds.png
        │   ├── cms.png
        │   ├── gitlab.png
        │   ├── manual1.png
        │   ├── manual2.png
        │   ├── map.png
        │   ├── onestopshop.png
        │   ├── password.png
        │   ├── reddit.png
        │   ├── scratchpad.png
        │   └── wikipedia.png
        └── input_images
            ├── classifieds
            ├── reddit
            └── shopping
    ```

1. Setup AWorld Environment:
    ```
    git clone https://github.com/inclusionAI/AWorld.git
    conda create -n vwa python=3.10
    conda activate vwa
    cd AWorld/examples/visualwebarena
    pip install -r requirements.txt
    pip install aworld==0.2.7 # ignore conflicts of aworld and visualwebarena and complete the installation
    ```

1. Clone VisualWebArena codes for aworld:
    ```
    git clone https://github.com/web-arena-x/visualwebarena.git
    cd visualwebarena
    git fetch origin pull/82/head:pr82
    git checkout pr82 # AWorld commit
    ln -s <path_to_AWorld_code_base>
    ```

3. Set up an image caption model (e.g., Blip2) and expose its API in OpenAI-compatible format.Server example code:

   ```
   from fastapi import FastAPI, UploadFile, Form
   from pydantic import BaseModel
   from typing import Optional
   from PIL import Image
   import io
   from typing import List, Union, Optional
   from PIL import Image
   import io
   import torch
   import subprocess
   import requests
   import base64
   from transformers import Blip2Processor, Blip2ForConditionalGeneration

   app = FastAPI()

   device = "cuda" if torch.cuda.is_available() else "cpu"

   model_path = '/ossfs/workspace/kevin/model/blip2'
   processor = Blip2Processor.from_pretrained(model_path)
   model = Blip2ForConditionalGeneration.from_pretrained(model_path, device_map="auto" if device=="cuda:0" else None)
   model.to(device)

   model_name = 'KevinBlip' # name should be the same as in your config

   class ContentItem(BaseModel):
       type: str
       text: Optional[str] = None
       image_url: Optional[dict] = None  # {"url": "..."}
       image_base64: Optional[str] = None
       alt_text: Optional[str] = None

   class Message(BaseModel):
       role: str  # "user" / "assistant" / "system"
       content: Union[str, List[ContentItem]]

   class ChatRequest(BaseModel):
       model: str
       messages: List[Message]

   @app.post("/v1/chat/completions")
   async def chat_completions(req: ChatRequest):
       messages = req.messages
       question = messages[1].content[0].text
       image_base64 = messages[1].content[1].image_url['url']
       if image_base64.startswith('data:image'): image_base64 = image_base64.split(',', 1)[1]
       missing_padding = len(image_base64) % 4
       if missing_padding: image_base64 += '=' * (4 - missing_padding)
       image_binary = base64.b64decode(image_base64)
       image = Image.open(io.BytesIO(image_binary)).convert("RGB")
       inputs = processor(image, question, return_tensors="pt").to(device)
       outputs = model.generate(**inputs)
       answer = processor.decode(outputs[0], skip_special_tokens=True)

       return {
           "id": f"chatcmpl-{model_name}",
           "object": "chat.completion",
           "choices": [
               {
                   "message": {
                       "role": "assistant",
                       "content": answer
                   }
               }
           ],
           "model": f"{model_name}"
       }


   def execute(command, silent = False):
       if not silent:
           print(command)
       result = None
       try:
           # Execute the command and capture the output
           # result = os.system(command)
           result = subprocess.check_output(command, shell=True, text=True)
           if not silent: print(result)
       except subprocess.CalledProcessError as e:
           if not silent: print('Error:', e)
       return result

   if __name__ == "__main__":
       import uvicorn
       execute(f'ip -4 addr show')
       uvicorn.run(app, host="0.0.0.0", port=9122)
   ```

2. Set Environment Variables

   ```
   export PYTHONPATH=<path_to_your_visualwebarena_folder>:$PYTHONPATH
   export DATASET=visualwebarena
   export CLASSIFIEDS="http://localhost:9980"
   export CLASSIFIEDS_RESET_TOKEN=<classifieds_reset_token>
   export SHOPPING="http://localhost:7770"
   export REDDIT="http://localhost:9999"
   export WIKIPEDIA="http://localhost:8888"
   export HOMEPAGE="http://localhost:4399"
   export SHOPPING_PSW="Password.123"
   export REDDIT_PSW="test1234"
   export CLASSIFIEDS_PSW="Password.123"
   export SHOPPING_SITE_ADMIN_PSW="admin1234"
   export SHOPPING_ADMIN_PSW="admin1234"
   export GITLAB_PSW='hello1234'
   export OPENROUTER_KEY=<openrouter_key>
   export BASE_URL=<base_url>
   export LLM_MODEL_NAME=gpt-5
   export AGI_URL=<url_to_your_image_caption_model_service>
   export AGI_API_KEY=<api_key_to_your_image_caption_model_service>
   ```

## Quick Start

   ```
   # ensure docker has been launched before running
   cd <path_to_your_visualwebarena_code_folder>
   python run_recon_act_infer.py <domain> # domain can be one of classifieds, reddit, shopping
   ```

## Config Description

Configs can be changed in the run_recon_act_infer.py.
Here are the item descriptions:

   ```
   ### VWAConfig field explanations

   Environment
   - render: Whether to show a live browser window.
   - render_screenshot: Whether to capture step screenshots (for rendering helper).
   - render_fail_only: Keep render artifacts only for failed tasks.
   - slow_mo: Delay (ms) between Playwright actions.
   - action_set_tag: Which predefined action schema to use for decoding actions.
   - observation_type: Form of observation (image, som etc.).
   - current_viewport_only: Limit DOM / screenshot to visible viewport.
   - viewport_width / viewport_height: Browser viewport size.
   - sleep_after_execution: Extra delay after each environment step.
   - output_response: Print raw model response in trajectory.
   - save_trace_enabled: Save full Playwright trace (zip).

   Task control
   - max_steps: Hard cap on steps per task.
   - single_site_mode: Enforce tasks with only one site.
   - flush: Flush result file even if exists.

   Agent behavior
   - instruction_path: Path to prompt/instruction template file.
   - parsing_failure_th: Stop if model output fails parsing this many times.
   - repeating_action_failure_th: Stop if repeating same action too often.
   - test_config_base_dir: Root dir of test config JSON files.

   Captioning
   - caption_model: Name of image caption model.

   Language model / inference
   - provider: Primary inference provider name.
   - eval_provider: Provider used for evaluation LLM calls.
   - model: Main agent model name.
   - mode: Operating mode (vision / som / mas).
   - temperature: Sampling temperature.
   - top_p: nucleus sampling parameter.
   - context_length: Max context length hint.
   - max_tokens: Max generation tokens.
   - stop_token: Optional explicit stop sequence.
   - vwa_code_path: Root path to VWA codebase.
   - vwa_data_path: Path to VWA dataset/resources and output folder (<dataset_folder> in installation).
   - domain: Active domain (shopping / reddit / classifieds).
   - print_time: Log per-step timing.
   - max_retry: Retries for a rollout reset or recovery.
   - max_obs_length: Truncate observation text length.

   Example range selection
   - test_start_idx / test_end_idx: Inclusive task id bounds to run.
   - test_config_files: Concrete list of config file paths (populated externally).
   ```

## Dataset Folder

When finish running, you can check results in the dataset folder (vwa_data_path):

```
├── auth
│   ├── classifieds_state.json
│   ├── reddit_state.json
│   └── shopping_state.json
├── config_files
│   ├── vwa
│   │   ├── test_classifieds
│   │   ├── test_classifieds.json
│   │   ├── test_classifieds.raw.json
│   │   ├── test_reddit
│   │   ├── test_reddit.json
│   │   ├── test_reddit.raw.json
│   │   ├── test_shopping
│   │   ├── test_shopping.json
│   │   └── test_shopping.raw.json
│   └── wa
│       └── test_webarena.raw.json
├── results # output folder
│   ├── gpt-5_mas
│   │   ├── classifieds
│   │   ├── reddit
│   │   ├── results_classifieds.json
│   │   ├── results_reddit.json
│   │   ├── results_shopping.json
│   │   └── shopping
│   └── ...
└── static
    ├── figures
    │   ├── calculator.png
    │   ├── classifieds.png
    │   ├── cms.png
    │   ├── gitlab.png
    │   ├── manual1.png
    │   ├── manual2.png
    │   ├── map.png
    │   ├── onestopshop.png
    │   ├── password.png
    │   ├── reddit.png
    │   ├── scratchpad.png
    │   └── wikipedia.png
    └── input_images
        ├── classifieds
        ├── reddit
        └── shopping
```

## How to Extend Tools

Create a tool following the format below and place it into the corresponding folder (decision_tools or hint_tools); the system will automatically route and execute these tools:

```
desc = <your_description_for_the_tool>

class TOOL_NAME:
    def run(self, intent, aa_response, url, input_img, som_page_screenshot_img, ori_page_screenshot_img, page, vlm_request):
        """Tool Function"""
        response = ''
        try:
            # tool main functions
            print('Tool Running')
        except Exception as e:
            print(e)
            return ''
        return response
```

## Acknowledgements

**VisualWebArena**: 
We thank the authors of the 
[VisualWebArena Dataset](https://github.com/web-arena-x/visualwebarena) 
for releasing the challenging dataset.

```
@article{koh2024visualwebarena,
  title={VisualWebArena: Evaluating Multimodal Agents on Realistic Visual Web Tasks},
  author={Koh, Jing Yu and Lo, Robert and Jang, Lawrence and Duvvur, Vikram and Lim, Ming Chong and Huang, Po-Yu and Neubig, Graham and Zhou, Shuyan and Salakhutdinov, Ruslan and Fried, Daniel},
  journal={arXiv preprint arXiv:2401.13649},
  year={2024}
}

@article{zhou2024webarena,
  title={WebArena: A Realistic Web Environment for Building Autonomous Agents},
  author={Zhou, Shuyan and Xu, Frank F and Zhu, Hao and Zhou, Xuhui and Lo, Robert and Sridhar, Abishek and Cheng, Xianyi and Bisk, Yonatan and Fried, Daniel and Alon, Uri and others},
  journal={ICLR},
  year={2024}
}
```

**AWorld Framework**: We thank the developers of the 
[AWorld Framework](https://github.com/inclusionAI/AWorld) 
for providing a powerful and flexible platform for agent development.
```
@misc{yu2025aworldorchestratingtrainingrecipe,
      title={AWorld: Orchestrating the Training Recipe for Agentic AI}, 
      author={Chengyue Yu and Siyuan Lu and Chenyi Zhuang and Dong Wang and Qintong Wu and Zongyue Li and Runsheng Gan and Chunfeng Wang and Siqi Hou and Gaochi Huang and Wenlong Yan and Lifeng Hong and Aohui Xue and Yanfeng Wang and Jinjie Gu and David Tsai and Tao Lin},
      year={2025},
      eprint={2508.20404},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2508.20404}, 
}
```

**Recon-Act**:

```
@misc{he2025reconactselfevolvingmultiagentbrowseruse,
      title={Recon-Act: A Self-Evolving Multi-Agent Browser-Use System via Web Reconnaissance, Tool Generation, and Task Execution}, 
      author={Kaiwen He and Zhiwei Wang and Chenyi Zhuang and Jinjie Gu},
      year={2025},
      eprint={2509.21072},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2509.21072}, 
}
```