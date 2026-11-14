from typing import Dict
from typing import Union

import bpy

from . import extend_lists
from . import extend_types
from . import operators
from . import ui
from .icons import initialize_smc_icons
from .icons import unload_smc_icons

__bl_classes = [
    ui.main_menu.MaterialMenu,
    ui.property_menu.PropertyMenu,

    operators.combiner.Combiner,
    operators.combine_list.RefreshObData,
    operators.combine_list.CombineSwitch,
    operators.multicombine_list.MultiCombineColor,
    operators.multicombine_list.MultiCombineImageAdd,
    operators.multicombine_list.MultiCombineImageMove,
    operators.multicombine_list.MultiCombineImagePath,
    operators.multicombine_list.MultiCombineImageReset,
    operators.multicombine_list.MultiCombineImageRemove,
    operators.browser.OpenBrowser,
    operators.get_pillow.InstallPIL,

    extend_types.CombineList,

    extend_lists.SMC_UL_Combine_List,
]


def register_all(bl_info: Dict[str, Union[str, tuple]]) -> None:
    _register_classes()
    initialize_smc_icons()
    extend_types.register()


def unregister_all() -> None:
    _unregister_classes()
    unload_smc_icons()
    extend_types.unregister()


def _register_classes() -> None:
    count = 0
    for cls in __bl_classes:
        try:
            bpy.utils.register_class(cls)
            count += 1
        except ValueError as e:
            print('Error:', cls, e)
    print('Registered', count, 'Material Combiner classes.')
    if count < len(__bl_classes):
        print('Skipped', len(__bl_classes) - count, 'Material Combiner classes.')


def _unregister_classes() -> None:
    count = 0
    for cls in reversed(__bl_classes):
        try:
            bpy.utils.unregister_class(cls)
            count += 1
        except (ValueError, RuntimeError) as e:
            print('Error:', cls, e)
    print('Unregistered', count, 'Material Combiner classes.')

