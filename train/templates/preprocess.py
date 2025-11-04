from pathlib import Path
from urllib.parse import urlparse
import base64
import io
from PIL import Image
import requests


def open_image_from_any(src: str, *, timeout: int = 10) -> Image.Image:
    """
    Open an image from a file path, URL, or base-64 string with Pillow.

    Parameters
    ----------
    src : str
        The image source.  It can be:
          • path to an image on disk  
          • http(s) URL  
          • plain base-64 or data-URI base-64
    timeout : int, optional
        HTTP timeout (s) when downloading from a URL.

    Returns
    -------
    PIL.Image.Image
    """
    parsed = urlparse(src)

    # 1) Detect a URL ----------------------------------------------------------------
    if parsed.scheme in {"http", "https"}:
        # --- requests version
        resp = requests.get(src, timeout=timeout)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content))

        # --- urllib version (uncomment if you can’t pip-install requests)
        # with urllib_request.urlopen(src, timeout=timeout) as fp:
        #     return Image.open(fp)

    # 2) Detect a base-64 string ------------------------------------------------------
    #    • data-URI style:  "data:image/png;base64,……"
    #    • bare base-64    :  "iVBORw0KGgoAAAANSUhEUgAABVYA…"
    try:
        # Strip header if present
        if src.startswith("data:"):
            header, b64 = src.split(",", 1)
        else:
            b64 = src

        # “validate=True” quickly rejects non-b64 text without decoding everything
        img_bytes = base64.b64decode(b64, validate=True)
        return Image.open(io.BytesIO(img_bytes))

    except (base64.binascii.Error, ValueError):
        # Not base-64 → fall through to path handling
        pass

    # 3) Treat it as a local file path ----------------------------------------------
    path = Path(src).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Image file not found: {path}")
    return Image.open(path)
