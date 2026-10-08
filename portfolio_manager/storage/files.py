"""Excel files of the data and results folders."""

import os

import pandas as pd

PD_DATA_TYPES = pd.Series | pd.DataFrame


def to_file_name(file_name: str) -> str:
    """File name without spaces and dots."""
    return file_name.replace(' ', '_').replace('.', '_')


def save_df_to_excel(df: PD_DATA_TYPES, file_name: str, folder_name: str = None, append: bool = False):
    """Save a frame; with append, merge it into the saved one (new rows win)."""
    if folder_name is not None:
        file_path = os.path.abspath(f'{folder_name}/{to_file_name(file_name)}.xlsx')
    else:
        file_path = os.path.abspath(f'{to_file_name(file_name)}.xlsx')
    if append and os.path.isfile(file_path):
        df_old = pd.read_excel(file_path, index_col=0)
        df = pd.concat([df_old, df], axis=0)
        df = df[~df.index.duplicated(keep='last')]
        df = df.sort_index()
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with pd.ExcelWriter(file_path, mode='w', engine='openpyxl') as writer:
        df.to_excel(writer)


def save_df_dict_to_excel(
    df_dict: dict[str, PD_DATA_TYPES], file_name: str, folder_name: str = None, append: bool = False
):
    """Save frames as the sheets of one file; with append, merge them into the saved ones."""
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
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with pd.ExcelWriter(file_path, mode='w', engine='openpyxl') as writer:
        for df_name, df in df_dict.items():
            if df is not None and isinstance(df, PD_DATA_TYPES):
                df.to_excel(writer, sheet_name=df_name)


def load_df_from_excel(file_name: str, folder_name: str = None, sheet_name: str | list[str] = 'Sheet1') -> pd.DataFrame:
    """Read one sheet (or several) of a file."""
    if folder_name is not None:
        file_path = os.path.abspath(f'{folder_name}/{to_file_name(file_name)}.xlsx')
    else:
        file_path = os.path.abspath(f'{to_file_name(file_name)}.xlsx')
    df = pd.read_excel(f'{file_path}', sheet_name=sheet_name, index_col=0)
    return df


def load_df_dict_from_excel(file_name: str, folder_name: str = None) -> dict[str, pd.DataFrame | str]:
    """Read every sheet of a file: sheet name -> frame."""
    if folder_name is not None:
        file_path = os.path.abspath(f'{folder_name}/{to_file_name(file_name)}.xlsx')
    else:
        file_path = os.path.abspath(f'{to_file_name(file_name)}.xlsx')
    df = pd.read_excel(f'{file_path}', sheet_name=None, index_col=0)
    return df
