import os
import json
import sys
import time
parent_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(parent_dir)
sys.path.append(parent_dir)
sys.path.append(root_dir)
from kutils import DEBUG, INFO, WARN, ERROR

import math
import ast
from tqdm import tqdm
import tarfile
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import shutil
from typing import Union
import yaml
import csv
import base64
from io import BytesIO
import platform
import time
import re
import yaml
import pandas as pd
from datetime import datetime
import hashlib
import subprocess
from collections import Counter

PFILE = 'os.path.basename(__file__).split(\'.\')[0]' # pure filename

def wait(n):
    time.sleep(n)

def dumps(s):
    return json.dumps(s, ensure_ascii=False)

def list_to_dict_count(input_list):
    # 使用 Counter 统计元素出现的次数
    count_dict = dict(Counter(input_list))
    count_dict = dict(sorted(count_dict.items(), key=lambda item: item[1], reverse=True))
    return count_dict

def edit_distance(a, b):
    # 创建二维数组，dp[i][j]表示a前i个字符与b前j个字符的编辑距离
    dp = [[0 for _ in range(len(b) + 1)] for _ in range(len(a) + 1)]

    # 初始化边界条件
    for i in range(len(a) + 1):
        dp[i][0] = i  # a变为空串需要i次删除
    for j in range(len(b) + 1):
        dp[0][j] = j  # 空串变为b需要j次插入

    # 填充dp表
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]  # 字符相同，不需要操作
            else:
                dp[i][j] = min(dp[i - 1][j] + 1,   # 删除
                               dp[i][j - 1] + 1,   # 插入
                               dp[i - 1][j - 1] + 1)  # 替换

    return dp[-1][-1]

def get_home():
    if platform.system() == 'Darwin': 
        HOME_PATH = os.path.expanduser("~")
    else: 
        HOME_PATH = '/ossfs/workspace/'
    return HOME_PATH

def get_nas():
    if get_os() == 'mac': NAS_PATH = f'{get_home()}/data/'
    else: NAS_PATH = '/mnt/agent-s3/common/public/kevin/'
    return NAS_PATH

def get_git():
    current_file = os.path.abspath(__file__)
    current_folder = os.path.dirname(current_file)
    return current_folder + '/'

def get_name(file_path, pure = False):
    filename_with_ext = os.path.basename(file_path)
    if pure:
        last_dot_index = filename_with_ext.rfind('.')
        name = filename_with_ext[:last_dot_index]
        ext = filename_with_ext[last_dot_index+1:]
        return name
    return filename_with_ext

def get_path(file_path):
    file_path = str(file_path).replace('//', '/')
    return os.path.dirname(file_path)

def is_nan(value):
    return value != value

def is_file_exist(path):
    return os.path.exists(path)

def is_folder_exist(folder_path):
    if os.path.exists(folder_path) and os.path.isdir(folder_path):
        return True
    else:
        return False

def is_folder_empty(folder_path):
    if os.path.isdir(folder_path):
        if len(os.listdir(folder_path)) == 0:
            return True
        else:
            return False
    else:
        return True


def list_files(folder_path, full_path = False):
    entries = os.listdir(folder_path)
    file_names = []

    # 遍历条目
    for entry in entries:
        entry_path = os.path.join(folder_path, entry)
        # 检查条目是否为文件
        if os.path.isfile(entry_path):
            file_names.append(entry)  # 添加到文件名列表

    file_names.sort(key=None, reverse=False)

    file_names = [a for a in file_names if '.DS_Store' != a]

    if full_path:
        file_names = [folder_path + '/' + a for a in file_names]

    return file_names

def list_folder_names(path):
    paths = os.listdir(path)
    paths = [a for a in paths if 'DS_Store' not in a]
    paths = [a for a in paths if '.zip' not in a]
    paths.sort()
    return paths

def list_folders(path, full_path = True):
    items = os.listdir(path)
    subdirs = []

    for item in items:
        item_ab_path = os.path.join(path, item)
        if os.path.isdir(item_ab_path):
            if full_path:
                subdirs.append(item_ab_path)
            else:
                subdirs.append(item)

    subdirs.sort(key=None, reverse=False)
    return subdirs

def get_all_filenames(directory):
    filenames = []
    for root, dirs, files in os.walk(directory):
        files.sort()
        for file in files:
            full_path = os.path.join(root, file)
            filenames.append(full_path)
    return filenames

def mkdir(path):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        print(f"Error: {e}")

def rmdir(directory):
    try:
        shutil.rmtree(directory)
        print(f"目录 {directory} 删除成功")
    except Exception as e:
        print(f"删除目录 {directory} 失败：{e}")

def read_xlsx(file, sheet='Sheet1'):
    df = pd.read_excel(file, sheet_name=sheet)
    return df

def read_json(json_file, encoding='utf-8-sig') -> Union[dict, list]:
    data = {}
    if not is_file_exist(json_file): return {}
    if json_file.endswith('.json'):
        with open(json_file, 'r', encoding=encoding) as file:
            data = json.load(file)
    elif json_file.endswith('.jsonl'):
        data = []
        with open(json_file, 'r', encoding=encoding) as f:
            for line in f:
                line_data = json.loads(line)
                data.append(line_data)
    else:
        data = {}
    return data

def write_json(filename, data, indent=4, encoding='utf-8'):
    # Check if file exists
    # if os.path.exists(filename):
    #     print(f"Warning: File '{filename}' already exists and may be overwritten.")
    
    if data == {}: return
    if data == []: return

    try:
        # Write data to JSON file
        with open(filename, 'w', encoding=encoding) as file:
            json.dump(data, file, ensure_ascii=False, indent=indent)
        # print(f"Data successfully written to '{filename}'.")
    except Exception as e:
        print(f"Error occurred while writing to file: {e}")

def write_jsonl(filename, data_list):
    with open(filename, 'w', encoding='utf-8') as f:
        for item in data_list:
            json_line = json.dumps(item, ensure_ascii=False)
            f.write(json_line + '\n')

def read_csv(csv_file):
    with open(csv_file, 'r', encoding='utf-8-sig') as file:
        data = list(csv.reader(file))
        return data

def write_csv(filename, data):
    with open(filename, 'w', newline='', encoding='utf-8-sig') as file:
        writer = csv.writer(file)
        writer.writerows(data)

def update_csv(filename, data):
    with open(filename, 'a', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(data)

def write_txt(filename, data):
    with open(filename, 'w', encoding='utf-8-sig') as file:
        file.write(data)

def write_code(filename, data):
    with open(filename, 'w') as file:
        file.write(data)

def write_yaml(yaml_file, data):
    with open(yaml_file, 'w', encoding='utf-8') as fp:
        yaml.safe_dump(data, fp, indent=2, encoding='utf-8', allow_unicode=True, sort_keys=False)

def read_yaml(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        data = yaml.load(f, Loader=yaml.FullLoader)
    return data

def read_txt(filename):
    with open(filename, 'r', encoding='utf-8-sig') as file:
        return file.read()

def to_json(s):
    json_object = json.loads(s)
    return json_object

def get_time():
    current_time = datetime.now()
    formatted_time = current_time.strftime("%Y%m%d%H%M%S")
    return formatted_time

def get_date():
    current_time = datetime.now()
    formatted_time = current_time.strftime("%Y%m%d%H%M%S")
    formatted_time = formatted_time[:8]
    return formatted_time


def get_date_list(start_date, end_date):
    start_date = datetime.strptime(start_date, "%Y%m%d")
    end_date = datetime.strptime(end_date, "%Y%m%d")

    date_list = []
    current_date = start_date

    while current_date <= end_date:
        date_list.append(current_date.strftime("%Y%m%d"))
        current_date += timedelta(days=1)
    return date_list

def sort_dict(data):
    def keys_are_all_numeric_strings(d):
        return all(isinstance(k, str) and k.isdigit() for k in d.keys())

    def sort_dict_by_key(d):
        if keys_are_all_numeric_strings(d):
            # 按数字大小排序，key保持字符串类型
            sorted_items = sorted(d.items(), key=lambda item: int(item[0]))
        else:
            # 按字符串排序
            sorted_items = sorted(d.items(), key=lambda item: item[0])
        return dict(sorted_items)
    sorted_dict = sort_dict_by_key(data)
    return sorted_dict

def extract_text(text, from_text, to_text = None) -> list:
    if to_text == None:
        return [text[text.find(from_text):]]

    if from_text == None:
        return [text[:text.find(to_text)]]

    pattern = re.escape(from_text) + r'(.*?)' + re.escape(to_text)
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return [match.strip() for match in matches]

    return []

def add_spaces_to_lines(input_text, n=8):
    lines = input_text.splitlines()
    spaces = ''
    for i in range(n):
        spaces += ' '
    indented_lines = [spaces + line for line in lines]
    return '\n'.join(indented_lines)

def gen_fixed_length_hash(input_string, length=32):
    hash_object = hashlib.sha256(input_string.encode('utf-8-sig'))
    hex_dig = hash_object.hexdigest()
    return hex_dig[:length]

def is_string_in_list(string_list, candidate):
    for i, s in enumerate(string_list):
        if candidate in s:
            return i
        if s in candidate:
            return i
    return 0

def get_string_in_list(string_list, candidate):
    matching_strings = [s for s in string_list if candidate in s]
    return matching_strings

def get_uuid():
    import uuid
    unique_id = uuid.uuid4()
    return unique_id

def merge_dicts(dict_list, level=-1):
    merged = {}
    if level == 0: return dict_list

    for d in dict_list:
        for key, value in d.items():
            if key in merged:
                if isinstance(value, dict) and isinstance(merged[key], dict):
                    merged[key] = merge_dicts([merged[key], value], level-1)
                else:
                    if not isinstance(merged[key], list):
                        merged[key] = [merged[key]]
                    merged[key].append(value)
            else:
                merged[key] = value

    return merged

def contains_pattern(s: str, pattern):
    return re.search(pattern, s) is not None

def remove_parentheses_content(string):
    pattern = r'\(.*?\)'
    new_string = re.sub(pattern, '', string)
    return new_string.strip()

def delete_file(file):
    if os.path.exists(file):
        os.remove(file)
    else:
        print(file, " not exist")

def string_list_fuzz_match(str_list, s):
    import fuzzywuzzy
    if not str_list: return 0
    results = []
    for string in str_list:
        results.append(fuzz.ratio(string, s))
    if results == []: return 0
    return max(results)

def copy_file(source_file, destination_folder):
    return shutil.copy(source_file, destination_folder)

def move_file(source_file, destination_folder):
    return shutil.move(source_file, destination_folder)

def copy_folder(source_path, target_path):
    shutil.copytree(source_path, target_path)

def remove_folder(folder):
    shutil.rmtree(folder)

def pl(mark = '-'):
    print(100*mark)

def print_json(data, indent=4):
    print(json.dumps(data, indent=indent, separators=(', ', ': '), ensure_ascii=False))

def execute(command, silent = False):
    if not silent:
        pl()
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

def compress_folder_to_tar_gz(source_dir, output_filename):
    with tarfile.open(output_filename, "w:gz") as tar:
        for root, dirs, files in os.walk(source_dir):
            for file in tqdm(files):
                full_path = os.path.join(root, file)
                tar.add(full_path, arcname=os.path.relpath(full_path, start=source_dir))

def get_os():
    if sys.platform.startswith('linux'):
        return "linux"
    elif sys.platform.startswith('win'):
        return "win"
    elif sys.platform.startswith('darwin'):
        return 'mac'
    return None

def image_to_base64(image_path):
    try:
        with open(image_path, 'rb') as image_file:
            image_bytes = image_file.read()
            encoded_image = base64.b64encode(image_bytes).decode('utf-8')
            return encoded_image
    except IOError:
        print('can not open file')
        return None

def remove_after_target_string(input_string, target_string):
    index = input_string.find(target_string)
    if index != -1:
        return input_string[:index + len(target_string)]
    else:
        return input_string

def pil_image_to_base64(img, format='png'):
    import PIL, PIL.JpegImagePlugin
    if isinstance(img, PIL.JpegImagePlugin.JpegImageFile): format = 'jpg'
    if format == 'jpg': format = 'JPEG'
    elif format == 'png': format = 'PNG'
    output_buffer = BytesIO()
    img.save(output_buffer, format=format)
    byte_data = output_buffer.getvalue()
    base64_str = base64.b64encode(byte_data).decode('utf-8')
    return base64_str

def base64_to_png(base64_string, output_path):
    from PIL import Image
    try:
        decoded_bytes = base64.b64decode(base64_string)
        byte_stream = BytesIO(decoded_bytes)
        image = Image.open(byte_stream)
        image.save(output_path, "PNG")
    except Exception as e:
        print(f"错误：{e}")

def draw_point(image, x, y, pixel_coor = False, color = 'red'):
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    if not pixel_coor:
        width, height = image.size
        x = int(x * width)
        y = int(y * height)
    radius = 5
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    return image

def draw_bounding_box(image, x, y, x2, y2, pixel_coor = False):
    from PIL import ImageDraw
    res_img = image.copy()
    draw = ImageDraw.Draw(res_img)
    if not pixel_coor:
        width, height = image.size
        x = int(x * width)
        y = int(y * height)
        x2 = int(x2 * width)
        y2 = int(y2 * height)
    if y2 < y: 
        temp = y
        y = y2
        y2 = temp
    if x2 < x: 
        temp = x
        x = x2
        x2 = temp
    # draw.rectangle([(x, y), (x2, y2)], outline='blue', width=8)
    # draw.rectangle([(x, y), (x2, y2)], outline='green', width=5)
    draw.rectangle([(x, y), (x2, y2)], outline='red', width=5)
    return res_img 

def add_text_bottom(image, text, font_path=None, font_size=10, text_color=(0, 0, 0), padding=10, bg_color=(255, 255, 255)):
    from PIL import Image, ImageDraw, ImageFont

    """
    在图像底部扩展画布并写入一行文字，兼容 Pillow 10+。

    参数：
    - image: PIL.Image 对象
    - text: 要写入的字符串
    - font_path: 字体文件路径，None 使用默认字体
    - font_size: 字体大小
    - text_color: 文字颜色，RGB元组
    - padding: 文字与图像底部边缘的间距（像素）
    - bg_color: 扩展画布背景色，RGB元组

    返回：
    - 新的 PIL.Image 对象
    """
    # 加载字体
    if font_path:
        font = ImageFont.truetype(font_path, font_size)
    else:
        font = ImageFont.load_default()

    # 计算文字边界框 (left, top, right, bottom)
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_width = right - left
    text_height = bottom - top

    # 新画布高度 = 原图高度 + 文字高度 + 上下padding
    new_height = image.height + 2 * text_height + 2 * padding
    new_image = Image.new('RGB', (image.width, new_height), color=bg_color)

    # 粘贴原图到新画布顶部
    new_image.paste(image, (0, 0))

    # 在底部区域绘制文字，水平居中，垂直位置为原图高度 + padding
    draw = ImageDraw.Draw(new_image)
    x = (new_image.width - text_width) // 2
    y = image.height + padding
    draw.text((x, y), text, fill=text_color, font=font)

    return new_image

def to_pinyin(content):
    from pypinyin import lazy_pinyin
    pinyin = lazy_pinyin(content)
    pinyin = [word[0].upper() + word[1:] if word else word for word in pinyin]
    pinyin = ''.join(pinyin)
    return pinyin

def delete_small_files(folder_path, size_threshold=1024):
    """
    删除指定文件夹及其子文件夹下小于size_threshold字节的文件。

    参数：
    folder_path (str): 目标文件夹路径
    size_threshold (int): 文件大小阈值，单位字节，默认1024（1KB）

    返回：
    int: 删除的文件数量
    """
    deleted_count = 0
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            file_path = os.path.join(root, file)
            try:
                if os.path.getsize(file_path) < size_threshold:
                    os.remove(file_path)
                    deleted_count += 1
                    print(f"Deleted: {file_path}")
            except Exception as e:
                print(f"Error deleting {file_path}: {e}")
    return deleted_count

def point_to_box_distance(point, box):
    """
    计算点到矩形盒子（由[x1, y1, x2, y2]定义）的最短距离。
    
    参数：
    - point: (px, py) 点坐标
    - box: [x1, y1, x2, y2] 盒子坐标，假设 x1 <= x2 且 y1 <= y2
    
    返回：
    - 点到盒子的最短距离（浮点数）
    """
    px, py = point
    x1, y1, x2, y2 = box
    
    # 计算点到盒子在x轴和y轴上的距离
    dx = 0
    if px < x1:
        dx = x1 - px
    elif px > x2:
        dx = px - x2
    
    dy = 0
    if py < y1:
        dy = y1 - py
    elif py > y2:
        dy = py - y2
    
    # 返回欧氏距离
    return math.sqrt(dx*dx + dy*dy)

def exponential_smoothing(data, alpha=0.3):
    """
    对列表data进行指数平滑，保持长度不变。
    alpha: 平滑因子，取值范围(0,1)，越大越贴近原始数据，越小越平滑。
    """
    if not data: return []
    smoothed = [data[0]]  # 第一个元素初始化为原始第一个值
    for i in range(1, len(data)):
        val = alpha * data[i] + (1 - alpha) * smoothed[-1]
        smoothed.append(val)
    return smoothed

def get_class_names_from_file(file_path):
    class_names = []
    with open(file_path, 'r', encoding='utf-8') as f:
        node = ast.parse(f.read(), filename=file_path)

    for item in ast.walk(node):
        if isinstance(item, ast.ClassDef):
            class_names.append(item.name)
    return class_names

def get_curr_date():
    today = datetime.today()
    formatted_date = today.strftime("%Y-%m-%d")
    return formatted_date

def get_n_month_ago(n):
    today = datetime.today()
    two_months_ago = today - relativedelta(months=n)
    formatted_date = two_months_ago.strftime("%Y-%m-%d")
    return formatted_date