
import pandas as pd
import numpy as np


query = 'The attached spreadsheet shows the inventory for a movie and video game rental store in Seattle, Washington. What is the title of the oldest Blu-Ray recorded in this spreadsheet? Return it as appearing in the spreadsheet. Here are the necessary table files: {file_path}, for processing excel file, you can write python code and leverage excel toolkit to process the file step-by-step and get the information.'
excel_file = '/path/aworld/aworld/dataset/gaia/32102e3e-d12a-4209-9163-7b3a104efe5d.xlsx'
df = pd.read_excel(excel_file)


columns = df.columns


data_array = df.to_numpy()


header_array = np.array([columns.tolist()])
query_array = np.array([[query] + [''] * (len(columns) - 1)])
output_array = np.vstack([header_array, data_array, query_array])


print(output_array)


np.save('output_array.npy', output_array)
np.save('sdsd.npy', output_array)

print(" save finish!")

import pandas as pd
import numpy as np


numpy_array = np.load('output_array.npy', allow_pickle=True)


print(" load NumPy array:")
print(numpy_array)


df = pd.DataFrame(numpy_array[:-1])
query = numpy_array[-1][0]


output_excel_file = 'output_file.xlsx'
df.to_excel(output_excel_file, index=False, header=None)

print(f"NumPy array save to Excel file:{output_excel_file}")
print(f"query：{query}")