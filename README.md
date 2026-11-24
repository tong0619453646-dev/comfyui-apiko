# APIKO for ComfyUI

Generate images and videos in ComfyUI with Nano Banana, GPT Image, Seedream, Seedance, Kling, Wan, MiniMax and Grok Imagine, and call text models such as Gemini, GPT, Claude and DeepSeek, all through one [APIKO](https://apiko.app/) API key. Nothing runs on your GPU: the nodes send the job to the APIKO API, wait for it, and return a normal ComfyUI `IMAGE`, `VIDEO` or `STRING`.

- Website: [apiko.app](https://apiko.app/)
- Models, live prices and status: [apiko.app/models](https://apiko.app/models)
- API reference: [apiko.app/docs](https://apiko.app/docs/introduction)

## Nodes

| Node | What it does | Output |
|---|---|---|
| **APIKO Image** | Text to image; connect `images` to edit or combine photos | `IMAGE`, result URL, task id |
| **APIKO Video** | Text to video, first/last frame to video, or video from reference images and clips | `VIDEO`, result URL, task id |
| **APIKO Chat** | Ask a text or vision model, e.g. to write or translate prompts for the other two nodes | `STRING` |

All three live under the **APIKO** category. Connect the video output to ComfyUI's **Save Video** node and the image output to **Save Image** or **Preview Image**.

## Install

- **ComfyUI-Manager**: search for `APIKO` and install.
- **comfy-cli**: `comfy node install comfyui-apiko`
- **Manually**:

  ```bash
  cd ComfyUI/custom_nodes
  git clone https://github.com/tong0619453646-dev/comfyui-apiko
  ```

No extra Python packages are needed. The `VIDEO` output needs a ComfyUI release from 2025 or later (native video support).

## API key

1. Sign up at [apiko.app](https://apiko.app/) and create a key in the dashboard. It is pay as you go: credit packs from $5, no subscription.
2. Give the key to ComfyUI in one of two ways:
   - set the environment variable `APIKO_API_KEY` before starting ComfyUI, or
   - create `custom_nodes/comfyui-apiko/api_key.txt` containing only the key.

The key is not a node input on purpose. ComfyUI saves node inputs into workflow files and into the metadata of every PNG it writes, so a key typed into a node would leak the moment you share an image or workflow. The nodes send the key to the configured API host and never print it. The default base URL is `https://api.apiko.app`. To override it, set `APIKO_BASE_URL` before starting ComfyUI.

## Models

The drop-downs list the models on [apiko.app/models](https://apiko.app/models) at release time. Any other id your key can call works through `custom_model`.

| Type | Models |
|---|---|
| Image | [Nano Banana 2.1](https://apiko.app/models/nano-banana-2.1), [Nano Banana Pro](https://apiko.app/models/nano-banana-pro), Nano Banana 2, [GPT Image 2.6 Sunburst](https://apiko.app/models/gpt-image-2.6-sunburst), GPT Image 2.6 Flare, GPT Image 2.5 Sunburst / Flare, GPT Image 2, [Seedream 5.5](https://apiko.app/models/seedream-5.5), Seedream 5.0 Pro, MiniMax H3 Image |
| Video | [Seedance 2.5](https://apiko.app/models/seedance-2.5), Seedance 2.0 / 2.0 Fast / 2.0 Mini, [Kling 4.0](https://apiko.app/models/kling-4.0), [Wan 3.0 image-to-video](https://apiko.app/models/wan3.0-image-to-video), Wan 3.0 text-to-video and reference-to-video (and Prime), MiniMax H3, Grok Imagine Video 1.5, Gemini Omni 1.1 Flash |
| Text | Gemini 3.5 Flash, GPT-6 Astra, Claude Sonnet 5.5, Claude Opus 5.5, DeepSeek V4 Pro, Qwen 3.8 Max, Kimi K3, GLM 5.3 |

When the node has the model's request schema, it validates parameters locally before submitting an image or video job. It drops unsupported fields (for example `quality` on Nano Banana) and stops with a clear message if a value is invalid or a required field is missing. If the model list is temporarily unavailable, the node submits the request and lets the APIKO gateway validate it. An HTTP 401 or 403 from the model list stops submission.

## Inputs worth knowing

**APIKO Image**
- `size`: aspect ratio such as `16:9`, or `default` to let the model decide.
- `resolution`: `1k` / `2k` / `4k` tiers on Nano Banana, GPT Image and Seedream 5.5. Higher tiers cost more per image.
- `images`: a batch of reference images. Each one is uploaded to APIKO (free, public link, deleted after 72 hours).
- `variation`: not sent to the API. ComfyUI reuses the last result while the inputs stay the same, so change this number when you want a new image with the same prompt.

**APIKO Video**
- `duration` in seconds, `resolution`, `aspect_ratio` and `generate_audio`. Video is billed per second of output at the resolution's rate.
- `first_frame` / `last_frame`: image-to-video and first/last-frame control on Seedance, Wan, MiniMax, Grok Imagine and Gemini Omni.
- `reference_images`, `reference_video_urls`, `reference_audio_urls`: reference-to-video on Seedance 2.5, Kling 4.0, Wan 3.0 reference and MiniMax H3. In the prompt, call them "Image 1", "Video 1", "Audio 1" in the order given. Wan bills the length of reference clips on top of the output.
- `seed`: sent only to models that take one (Seedance).

**Both**
- `extra_json`: any other field from the model's API reference, e.g. `{"background": "transparent"}`.
- `resume_task_id`: fetch an earlier task without submitting (or paying for) a new one.
- `timeout_minutes`: how long to wait. Images usually take under a minute (4K up to about 5 minutes); videos take 2 to 15 minutes.

## Billing and failures

- Each queued run of an Image or Video node is one paid job, charged to your APIKO balance at the price shown on the model page. Failed jobs are refunded in full.
- If you cancel the queue or the node times out, the job keeps running on APIKO and is still billed. The console shows its task id; paste it into `resume_task_id` and queue again to download the result for free.
- Result links are valid for 24 hours. Save anything you want to keep with Save Image / Save Video.

## Troubleshooting

| Message | Meaning |
|---|---|
| `No APIKO API key found` | Set `APIKO_API_KEY` or create `api_key.txt`, then restart ComfyUI. |
| `HTTP 401` | The key is wrong or was deleted. |
| `HTTP 402` | Balance too low; top up in the APIKO dashboard. |
| `not available to this API key` | The model id is wrong or retired; nothing was billed. Check [apiko.app/models](https://apiko.app/models). |
| `HTTP 503` | The model has no available route right now; try again later. |
| `... failed and was refunded in full` | The provider rejected the job (often an unreachable reference URL or a content refusal). Change the input before retrying. |

## Development

```bash
python -m unittest discover -s tests -v   # run with ComfyUI's Python to include the node tests
```

The tests run against a local mock of the APIKO API (`tests/mock_apiko.py`) and never call the real service.

## License

MIT. See [LICENSE](LICENSE).
