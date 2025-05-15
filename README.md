<p align="center">
  <img src="readme_assets/aworld_logo.png" alt="AWorld Logo" width="100"/>
  <br>
  <span align="center" style="font-size: 24px;">
    <b><span style="color: #1677FF;">A</span><span style="color: var(--color-fg-default, #333333);">World</span></b>
  </span>
</p>

<div align="center">

[![Twitter Follow](https://img.shields.io/twitter/follow/AWorld_AI?style=social)](https://x.com/InclusionAI666)
[![WeChat QR Code](https://img.shields.io/badge/WeChat-Add%20us-green?logo=wechat&logoColor=white)](https://raw.githubusercontent.com/inclusionAI/AWorld/main/readme_assets/aworld_wechat_qr.png)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

</div>

## News
- 🥳 [2024/05/13] AWorld has updated its state management for browser use and enhanced the video processing MCP server, achieving a score of 77.58 on GAIA validation (Pass@1 = 61.8) and maintaining its position as the top-ranked open-source framework. Learn more: [GAIA leaderboard](https://huggingface.co/spaces/gaia-benchmark/leaderboard)
- ✨ [2025/04/23] AWorld ranks 3rd on GAIA benchmark (69.7 avg) with impressive Pass@1 = 58.8, 1st among open-source frameworks. Reproduce with <code>python examples/gaia/run.py</code>



## Gaia Benchmark

### Run Gaia Benchmark in Docker

- Check Docker is installed and running on your machine, command: `docker ps`

- Clone this repository and switch to the `gaia-benchmark` branch, command: `git clone https://github.com/inclusionAI/AWorld && git checkout gaia-benchmark`

- Download the GAIA dataset from [https://huggingface.co/datasets/gaia-benchmark/GAIA](https://huggingface.co/datasets/gaia-benchmark/GAIA) and place it in the `examples/gaia/GAIA` directory, command: `git clone git@hf.co:datasets/gaia-benchmark/GAIA examples/gaia/GAIA`

    - You'll need to configure [Hugging Face SSH Keys](https://huggingface.co/settings/keys) to access the GAIA repository.

- Configure your API keys:
    - Copy the template configuration file, command: `cp .env.template .env`
    - Replace all `{YOUR_CONFIG}` placeholders with your actual values.

- Build and run the GAIA container with: `sh run-gaia.sh` (wait for the build to complete and the container to start)

- Navigate to [http://127.0.0.1:8080](http://127.0.0.1:8080) and register an account

- Log in to your account, select the `gaia_agent` from the top menu and choose a GAIA question from the list below the page, then click the send button.

## Contributing
We warmly welcome developers to join us in building and improving AWorld! Whether you're interested in enhancing the framework, fixing bugs, or adding new features, your contributions are valuable to us.

For academic citations or wish to contact us, please use the following BibTeX entry:

```bibtex
@software{aworld2025,
  author = {Agent Team at Ant Group},
  title = {AWorld: A Unified Agent Playground for Computer and Phone Use Tasks},
  year = {2025},
  url = {https://github.com/inclusionAI/AWorld},
  version = {0.1.0},
  publisher = {GitHub},
  email = {chenyi.zcy at antgroup.com}
}
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
