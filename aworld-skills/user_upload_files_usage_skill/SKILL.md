---
name: user_upload_files_usage_skill
description: 这是一个专门用于指导如何处理用户上传的附件文件（下载、深度处理或快速解析）的综合专业技能文档。核心原则：通过 Python 脚本读取本地 JSON 配置，严禁手动复述长 Token。
---

# 用户上传附件处理指南 (User Upload Files Usage Skill)

本指南旨在为 AI 智能体提供一套标准化的工作流，用于处理用户在对话中上传的附件文件（如 PDF、Word、图片、视频等）。

## 🚨 核心信息获取原则 (Crucial Principle)
用户带来的附件信息（包含极长且无规律的 `Authorization` Token、`fileId`、`fileUrl` 等）会统一装载在当前工作目录的 **`user_upload_files_meta_info.json`** 文件中。
**严禁**在 Bash 命令（如 `curl`）中手动拼接或复述这些长字段，以防截断或拼写错误！
**必须**通过编写并执行 Python 脚本来读取该 JSON 文件，提取结构化字段，并由代码自动发起网络请求。

---

## 🧭 核心决策：模式选择指南 (Mode Selection Guide)

在编写处理脚本前，**必须首先明确当前任务的场景，并在以下两种模式中做出选择。** 

### 🟢 模式一：本地下载与深度处理模式（优先推荐 / 默认模式）
- **适用场景**：绝大多数场景。特别是当需要对文件进行**高质量的深度理解**、需要**编辑/修改/重组**文件、或者文件是**图片/视频等多媒体格式**时。
- **核心优势**：后续操作空间极广。下载到本地后，不仅可以利用本地工具进行更高质量的解析，还可以直接编辑、格式转换或作为素材用于其他生成任务。
- **执行路径**：在 Python 脚本中执行**下载逻辑**。

### 🟡 模式二：网关快速解析模式（快捷 / 轻量文本模式）
- **适用场景**：仅限于需要**直接、快速获取文件大致文本内容**，且**绝对不涉及文件改造或编辑**的场景。**严禁**用于图片、视频等多媒体文件。
- **核心优势**：无需下载实体文件，直接通过内部 Gateway 接口异步获取文档（如 PDF、Word）的解析文本。
- **执行路径**：在 Python 脚本中执行**网关解析逻辑**。

---

## 💻 标准化 Python 脚本模板 (Python Implementation Template)

请根据你选择的模式，参考以下 Python 脚本模板。你可以将该脚本写入当前目录（如 `process_attachments.py`）并执行。

```python
import json
import time
import urllib.request
import re

def download_file(file_id, file_url, file_type):
    """模式一：本地下载逻辑 (优先推荐)"""
    match = re.search(r'alipayobjects\.com/([^/]+)/afts/file', file_url)
    biz_key = match.group(1) if match else 'leopard_file'
    filename = f"{file_id}.{file_type}"
    
    # 主推环境
    internal_url = f"http://mass.stable.alipay.net/{biz_key}/afts/file/{file_id}"
    print(f"[*] Downloading {filename} from stable env...")
    try:
        req = urllib.request.Request(internal_url)
        with urllib.request.urlopen(req) as response, open(filename, 'wb') as out_file:
            out_file.write(response.read())
        print(f"[+] Successfully downloaded {filename}")
    except Exception as e:
        print(f"[-] Stable env failed: {e}. Trying fallback...")
        # 备选环境
        fallback_url = f"https://mass-office.alipay.com/{biz_key}/afts/file/{file_id}"
        try:
            req = urllib.request.Request(fallback_url)
            with urllib.request.urlopen(req) as response, open(filename, 'wb') as out_file:
                out_file.write(response.read())
            print(f"[+] Successfully downloaded {filename} via fallback")
        except Exception as e2:
            print(f"[-] Fallback failed: {e2}")

def parse_file(file_id, file_url, file_type, auth_token, session_id):
    """模式二：网关快速解析逻辑"""
    print(f"[*] Registering and parsing {file_id} via Gateway ...")
    headers = {"Authorization": auth_token, "Content-Type": "application/json"}
    payload = {
        "fileId": file_id, "fileFormat": file_type, "fileUrl": file_url,
        "sessionId": session_id, "autoParse": True
    }
    
    # 1. 注册并触发解析
    try:
        req = urllib.request.Request("https://asapgw.alipay.com/api/files", 
                                     data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
        with urllib.request.urlopen(req) as response:
            print(f"[*] Register success: {json.loads(response.read().decode('utf-8')).get('success')}")
    except Exception as e:
        print(f"[-] Failed to register: {e}")
        return

    # 2. 等待并查询结果 (必须等待至少3秒)
    print("[*] Waiting 4 seconds for parsing...")
    time.sleep(4)
    try:
        req = urllib.request.Request(f"https://asapgw.alipay.com/api/files/{file_id}/parse_result", 
                                     headers=headers, method='GET')
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            if res_data.get('success') and res_data.get('data', {}).get('parseStatus') == 'succeeded':
                text = res_data.get('data', {}).get('text', '') or res_data.get('data', {}).get('rawResult', {}).get('markdown_result', {}).get('content', '')
                out_txt = f"{file_id}_parsed.txt"
                with open(out_txt, 'w', encoding='utf-8') as f:
                    f.write(text)
                print(f"[+] Successfully saved parsed content to {out_txt}")
            else:
                print("[-] Parse not succeeded yet or failed.")
    except Exception as e:
        print(f"[-] Failed to get parse result: {e}")

def main():
    # 核心：从 JSON 文件读取元数据，避免手动复述长 Token
    with open('user_upload_files_meta_info.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    processed_ids = set()
    for att in data.get('attachments', []):
        file_id = att.get('fileId')
        if file_id in processed_ids: continue
        processed_ids.add(file_id)
        
        file_url = att.get('fileUrl')
        file_type = att.get('fileType')
        auth_token = att.get('authorization')
        session_id = att.get('session_id', 'default-session-id')
        
        print(f"\n--- Processing File: {file_id} ---")
        
        # ==========================================
        # ⚠️ 智能体请注意：根据当前任务需求，保留以下其中一种模式的调用即可！
        # ==========================================
        
        # 【模式一】本地下载 (优先推荐)
        download_file(file_id, file_url, file_type)
        
        # 【模式二】网关快速解析
        # parse_file(file_id, file_url, file_type, auth_token, session_id)

if __name__ == '__main__':
    main()
```

## 🛑 全局约束与注意事项 (Global Constraints)

1. **绝对禁止手动拼接 Token**：不要在 Bash 中使用 `curl -H "Authorization: Bearer eyJ..."`，必须通过上述 Python 脚本读取 JSON 执行。
2. **工作目录限制**：所有操作（如下载文件、保存解析文本、运行脚本）必须在当前工作目录进行，严禁切换到 `/tmp` 等其他目录。
3. **模式互斥**：在实际编写脚本时，请根据任务需求注释掉不需要的模式，不要在同一个任务中无意义地混用两种模式。如果需要编辑文件，一开始就必须选择模式一。
