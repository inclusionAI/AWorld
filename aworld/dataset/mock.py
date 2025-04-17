# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
from importlib import resources

import pandas as pd
import numpy as np

from aworld.utils import import_package


def mock_dataset(name: str):
    if name == 'gaia':
        res = resources.read_binary("aworld.dataset.gaia", "gaia.npy")

        npy_path = f"{os.getcwd()}/gaia.npy"
        with open(npy_path, "wb+") as write:
            write.write(res)

        numpy_array = np.load(npy_path, allow_pickle=True)
        df = pd.DataFrame(numpy_array[:-1])
        query = numpy_array[-1][0]

        save_file_path = f"{os.getcwd()}/gaia.xlsx"
        import_package("openpyxl")
        df.to_excel(save_file_path, index=False, header=None)
        return query.format(file_path=save_file_path)
    return None
