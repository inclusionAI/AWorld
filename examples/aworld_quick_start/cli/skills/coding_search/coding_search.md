---
name: coding_search
description: Programming utility for executing Python scripts to search and download images from Baidu Images API. This is a code execution tool for developers, NOT for content generation or presentation creation tasks.
model_config: {
  "llm_model_name": "matrixllm.claude-sonnet-4-20250514",
  "llm_provider": "openai",
  "llm_temperature": 0.6,
  "llm_base_url": "https://agi.alipay.com/api",
  "llm_api_key": "sk-ec93f5148ee64b11a75e82b41716ced1",
  "params": {"max_completion_tokens": 40960},
  "ext_config": {
    "max_tokens": 40960
  }
}
mcp_servers: ["terminal-server"]
mcp_config: {
  "mcpServers": {
    "terminal-server": {
      "command": "python",
      "args": [
        "-m",
        "examples.aworld_quick_start.mcp_tool.terminal_server"
      ],
      "env": {
      }
    }
  }
}
active: True
---

# 🖼️ coding_search - Baidu Image Search & Download

Use Python script to call Baidu Image API, search by keywords and batch download images.

## Features

1. **Create Download Directory** - Automatically create folder to store images
2. **Search Images** - Call Baidu Image Search API to get image list
3. **Batch Download** - Download first N images from search results
4. **Error Handling** - Handle network errors and download failures gracefully

## Usage

Execute the following Python script to search and download images:

```python
import requests
import os
import urllib.parse
import time
import json
import re

def fix_json_escape_sequences(content: str) -> str:
    r"""
    Fix invalid escape sequences in JSON
    
    Valid escape sequences in JSON: \", \\, \/, \b, \f, \n, \r, \t, \uXXXX
    Invalid escape sequences (such as \') need to be fixed
    """
    # Method 1: Replace \/ with / (forward slash doesn't need escaping in JSON, but \/ is allowed)
    # Actually \/ is valid in JSON, but some parsers may not support it

    # Method 2: Fix invalid single quote escape \' -> '
    # In JSON strings, single quotes don't need escaping, only double quotes need escaping
    content = content.replace("\\'", "'")

    # Method 3: Fix other possible invalid escape sequences
    # Process character by character to ensure all backslash escapes are valid
    result = []
    i = 0
    while i < len(content):
        if content[i] == '\\' and i + 1 < len(content):
            next_char = content[i + 1]
            # Check if it's a valid escape sequence
            if next_char in ['"', '\\', '/', 'b', 'f', 'n', 'r', 't', 'u']:
                # Valid escape sequence
                if next_char == 'u' and i + 5 < len(content):
                    # Unicode escape sequence \uXXXX
                    unicode_part = content[i+2:i+6]
                    try:
                        # Verify if it's valid hexadecimal
                        int(unicode_part, 16)
                        result.append(content[i:i+6])
                        i += 6
                        continue
                    except ValueError:
                        # Invalid Unicode escape, escape the backslash
                        result.append('\\\\')
                        result.append(next_char)
                        i += 2
                        continue
                else:
                    # Other single character escape sequences
                    result.append(content[i:i+2])
                    i += 2
                    continue
            else:
                # Invalid escape sequence, escape the backslash itself
                result.append('\\\\')
                result.append(next_char)
                i += 2
                continue
        else:
            result.append(content[i])
            i += 1

    return ''.join(result)

def safe_json_loads(content: str):
    """
    Safely parse JSON, if it fails, try to fix escape sequences and retry
    
    Args:
        content: JSON string content
        
    Returns:
        Parsed Python object
        
    Raises:
        json.JSONDecodeError: If parsing still fails after fixing
    """
    # First try to parse directly
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        # If parsing fails, try to fix escape sequences
        print(f'JSON parsing failed, attempting to fix escape sequences... (error position: line {e.lineno}, column {e.colno})')
        fixed_content = fix_json_escape_sequences(content)
        try:
            return json.loads(fixed_content)
        except json.JSONDecodeError as e2:
            print(f'Still failed after fixing: {e2} (error position: line {e2.lineno}, column {e2.colno})')
            raise

# Configuration
search_query = 'David Beckham football'  # Search keyword
download_dir = 'beckham_photos' # Download directory
download_count = 10             # Number of images to download

# Create download directory
os.makedirs(download_dir, exist_ok=True)

# Build Baidu Image Search URL
encoded_query = urllib.parse.quote(search_query)
search_url = f'https://image.baidu.com/search/acjson?tn=resultjson_com&logid=&ipn=rj&ct=201326592&is=&fp=result&fr=&word={encoded_query}&queryWord={encoded_query}&cl=2&lm=-1&ie=utf-8&oe=utf-8&adpicid=&st=-1&z=&ic=&hd=&latest=&copyright=&s=&se=&tab=&width=&height=&face=0&istype=2&qc=&nc=1&expermode=&nojc=&isAsync=&pn=0&rn=30&gsm=1e'

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Referer': 'https://image.baidu.com/'
}

try:
    response = requests.get(search_url, headers=headers, timeout=10)
    print(f'Search response status: {response.status_code}')

    if response.status_code == 200:
        # Use safe parsing function to automatically handle escape sequence issues
        data = safe_json_loads(response.text)
        if 'data' in data and isinstance(data['data'], list):
            images = data['data']
            print(f'Found {len(images)} images')

            downloaded = 0
            for i, img in enumerate(images[:download_count]):
                if 'thumbURL' in img:
                    img_url = img['thumbURL']
                    try:
                        img_response = requests.get(img_url, headers=headers, timeout=10)
                        if img_response.status_code == 200:
                            filename = f'{download_dir}/image_{i+1}.jpg'
                            with open(filename, 'wb') as f:
                                f.write(img_response.content)
                            print(f'Downloaded: {filename}')
                            downloaded += 1
                            time.sleep(1)  # Avoid too frequent requests
                    except Exception as e:
                        print(f'Failed to download image {i+1}: {e}')

            print(f'Successfully downloaded {downloaded} images in total')
        else:
            print('No image data found')
    else:
        print(f'Search failed, status code: {response.status_code}')

except Exception as e:
    print(f'Error during search: {e}')
```

## Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `search_query` | Search keyword | `'David Beckham'` |
| `download_dir` | Download save directory | `'beckham_photos'` |
| `download_count` | Number of images to download | `10` |

## API Parameters

Main parameters for Baidu Image Search API:
- `word` / `queryWord`: Search keyword (URL encoded)
- `pn`: Pagination start position (starts from 0)
- `rn`: Number of results per page (max 30)

## Notes

1. **Request Frequency**: Set 1 second interval between downloads to avoid anti-crawler restrictions
2. **User-Agent**: Must simulate browser request headers
3. **Referer**: Must set Baidu Image domain as referrer
4. **Timeout**: Set 10 second timeout to avoid long blocking
5. **Image Quality**: `thumbURL` is thumbnail, use `middleURL` or `objURL` for larger sizes
6. **Download Limit**: The script must not download more than 10 images per execution