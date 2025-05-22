import glob
import json
import os
import re

# 指定日志文件目录
log_dir = "/Users/arac/Desktop/AWorld/gaia-benchmark/logs/test/all"

# 查找所有匹配的日志文件
log_files = glob.glob(os.path.join(log_dir, "super_agent_*.log"))

# 存储结果的列表
results = []

# 正则表达式模式
answer_pattern = re.compile(r" - INFO - Agent answer: (.*)$")
detail_pattern = re.compile(r" - INFO - Detail: (.*)$")
uuid_pattern = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)

# 处理每个日志文件
for log_file in log_files:
    print(f"处理文件: {log_file}")

    answer = None
    detail_json = None
    task_id = None

    dataset = {}
    with open(
        "/Users/arac/Desktop/AWorld/gaia-benchmark/GAIA/2023/test/metadata.jsonl",
        mode="r",
        encoding="utf-8",
    ) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    json_data = json.loads(line)
                    dataset[json_data["task_id"]] = json_data
                except json.JSONDecodeError as e:
                    print(f"JSON解析错误: {e} 在文件 {log_file}\n行内容: {line}")

    with open(log_file, "r", encoding="utf-8") as f:
        uuid = ""
        for line in f:
            # 查找答案行
            answer_match = answer_pattern.search(line)
            uuid_match = uuid_pattern.search(line)
            if answer_match and uuid_match:
                answer = answer_match.group(1).strip()
                uuid = uuid_match.group(1)
                # print(f"找到UUID&Answer: {uuid} -> {answer}")

        record = dataset.get(uuid, None)
        if record and answer is not None:
            file_result = {
                "task_id": record["task_id"],
                "question": record.get("Question", ""),
                "level": record.get("Level", ""),
                "answer": answer,
            }
            results.append(file_result)
            # print(f"添加结果: {file_result['task_id']}")

        if record and answer is None:
            file_result = {
                "task_id": record["task_id"],
                "question": record.get("Question", ""),
                "level": record.get("Level", ""),
                "answer": "<FAILED/>",
            }
            results.append(file_result)
            # print(f"添加结果: {file_result['task_id']}")

# 输出结果
print(f"\n总共处理了 {len(log_files)} 个文件，找到 {len(results)} 个结果")

# 将结果保存到JSON文件
output_file = "/Users/arac/Desktop/AWorld/gaia-benchmark/logs/test/all/results_0520_extracted.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"结果已保存到: {output_file}")
