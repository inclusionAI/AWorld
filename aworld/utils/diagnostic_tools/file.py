## 编码
import pandas as pd
import numpy as np

# 读取 Excel 文件
query = 'The attached spreadsheet shows the inventory for a movie and video game rental store in Seattle, Washington. What is the title of the oldest Blu-Ray recorded in this spreadsheet? Return it as appearing in the spreadsheet. Here are the necessary table files: {file_path}, for processing excel file, you can write python code and leverage excel toolkit to process the file step-by-step and get the information.'
excel_file = '/Users/chenpeng/Documents/02code/aworld/aworld/dataset/gaia/32102e3e-d12a-4209-9163-7b3a104efe5d.xlsx'  # 替换为实际的文件路径
df = pd.read_excel(excel_file)  # 将 Excel 文件加载为 DataFrame

# 获取表头
columns = df.columns

# 将 DataFrame 转换为 numpy 数组
data_array = df.to_numpy()

# 构造包含表头和数据的新数组
header_array = np.array([columns.tolist()])  # 表头转化为 NumPy 数组，维度调整为二维
query_array = np.array([[query] + [''] * (len(columns) - 1)])  # 构造一条新的数据
output_array = np.vstack([header_array, data_array, query_array])  # 垂直合并表头、数据和新的行

# 打印结果
print(output_array)

# 保存为 .npy 文件
np.save('output_array.npy', output_array)
np.save('sdsd.npy', output_array)

print("保存完成！")
## 解码
import pandas as pd
import numpy as np

# 从之前保存的 .npy 文件中加载 NumPy 数组
numpy_array = np.load('output_array.npy', allow_pickle=True)

# 打印加载的 NumPy 数组
print("加载的 NumPy 数组：")
print(numpy_array)

# 将 NumPy 数组转换为 Pandas DataFrame
df = pd.DataFrame(numpy_array[:-1])
query = numpy_array[-1][0]

# 保存为 Excel 文件
output_excel_file = 'output_file.xlsx'  # 给输出的 Excel 文件取一个名字
df.to_excel(output_excel_file, index=False, header=None)  # index=False 可以去掉行索引

print(f"已成功将 NumPy 数组保存为 Excel 文件：{output_excel_file}")
print(f"query：{query}")