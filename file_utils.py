import os
from typing import Dict, Union, List

import pandas as pd

PD_DATA_TYPES = Union[pd.Series, pd.DataFrame]


def to_file_name(file_name: str) -> str:
    return file_name.replace(' ', '_').replace('.', '_')


def save_df_to_excel(df: PD_DATA_TYPES,
                     file_name: str,
                     folder_name: str = None,
                     append: bool = False):
    if folder_name is not None:
        file_path = os.path.abspath(f'{folder_name}/{to_file_name(file_name)}.xlsx')
    else:
        file_path = os.path.abspath(f'{to_file_name(file_name)}.xlsx')
    if append and os.path.isfile(file_path):
        df_old = pd.read_excel(file_path, index_col=0)
        df = pd.concat([df_old, df], axis=0)
        df = df[~df.index.duplicated(keep='last')]
        df = df.sort_index()
    with pd.ExcelWriter(file_path, mode="w", engine="openpyxl") as writer:
        df.to_excel(writer)


def save_df_dict_to_excel(df_dict: Dict[str, PD_DATA_TYPES],
                          file_name: str,
                          folder_name: str = None,
                          append: bool = False):
    if folder_name is not None:
        file_path = os.path.abspath(f'{folder_name}/{to_file_name(file_name)}.xlsx')
    else:
        file_path = os.path.abspath(f'{to_file_name(file_name)}.xlsx')
    if append and os.path.isfile(file_path):
        df_dict_old = pd.read_excel(file_path, index_col=0, sheet_name=None)
        for df_name, df in df_dict_old.items():
            if df_name in df_dict:
                df_dict[df_name] = pd.concat([df, df_dict[df_name]], axis=0)
                df_dict[df_name] = df_dict[df_name][~df_dict[df_name].index.duplicated(keep='last')]
                df_dict[df_name] = df_dict[df_name].sort_index()
    with pd.ExcelWriter(file_path, mode="w", engine="openpyxl") as writer:
        for df_name, df in df_dict.items():
            if df is not None and isinstance(df, PD_DATA_TYPES):
                df.to_excel(writer, sheet_name=df_name)


def save_df_to_parquet(df: PD_DATA_TYPES,
                       file_name: str,
                       folder_name: str = None,
                       append: bool = False):
    if folder_name is not None:
        file_path = os.path.abspath(f'{folder_name}/{to_file_name(file_name)}.parquet')
    else:
        file_path = os.path.abspath(f'{to_file_name(file_name)}.parquet')
    if append and os.path.isfile(file_path):
        df_old = pd.read_parquet(file_path)
        df = pd.concat([df_old, df], axis=0)
        df = df[~df.index.duplicated(keep='last')]
        df = df.sort_index()
    df.to_parquet(file_path)


def load_df_from_excel(file_name: str,
                       folder_name: str = None,
                       sheet_name: str | List[str] = 'Sheet1'
                       ) -> pd.DataFrame:
    if folder_name is not None:
        file_path = os.path.abspath(f'{folder_name}/{to_file_name(file_name)}.xlsx')
    else:
        file_path = os.path.abspath(f'{to_file_name(file_name)}.xlsx')
    df = pd.read_excel(f'{file_path}', sheet_name=sheet_name, index_col=0)
    return df


def load_df_dict_from_excel(file_name: str,
                            folder_name: str = None
                            ) -> Dict[str, pd.DataFrame | str]:
    if folder_name is not None:
        file_path = os.path.abspath(f'{folder_name}/{to_file_name(file_name)}.xlsx')
    else:
        file_path = os.path.abspath(f'{to_file_name(file_name)}.xlsx')
    df = pd.read_excel(f'{file_path}', sheet_name=None, index_col=0)
    return df


def load_df_from_parquet(file_name: str,
                         folder_name: str = None):
    if folder_name is not None:
        file_path = os.path.abspath(f'{folder_name}/{to_file_name(file_name)}.parquet')
    else:
        file_path = os.path.abspath(f'{to_file_name(file_name)}.parquet')
    df = pd.read_parquet(f'{file_path}')
    return df
