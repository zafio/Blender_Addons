import importlib
import site
import subprocess
import sys
from typing import List
from typing import Set

import bpy

from .. import globs


class InstallPIL(bpy.types.Operator):
    bl_idname = 'smc.get_pillow'
    bl_label = 'Install PIL'
    bl_description = 'Click to install Pillow. This could take a while and needs "Allow Online Access" ' \
                     '(Preferences > System > Network)'

    def execute(self, context: bpy.types.Context) -> Set[str]:
        if not getattr(bpy.app, 'online_access', True):
            self.report({'ERROR'}, 'Online access is disabled. Enable "Allow Online Access" in '
                                   'Preferences > System > Network, then try again')
            return {'CANCELLED'}

        try:
            self._ensure_pip()
            self._run_pip(['install', '--upgrade', '--user', 'Pillow'])
        except (subprocess.CalledProcessError, OSError) as e:
            self.report({'ERROR'}, 'Pillow installation failed: {0}. See the system console for details'.format(e))
            return {'CANCELLED'}

        user_site = site.getusersitepackages()
        if user_site not in sys.path:
            sys.path.insert(0, user_site)
        importlib.invalidate_caches()

        from .combiner import combiner_ops
        if combiner_ops.load_pil():
            globs.pil_exist = True
            self.report({'INFO'}, 'Pillow installed. Restart Blender if the combiner reports errors')
        else:
            globs.smc_pi = True
            self.report({'INFO'}, 'Installation complete, please restart Blender')
        return {'FINISHED'}

    @staticmethod
    def _ensure_pip() -> None:
        try:
            import pip  # noqa: F401
        except ImportError:
            subprocess.check_call([sys.executable, '-m', 'ensurepip', '--user'])

    @staticmethod
    def _run_pip(args: List[str]) -> None:
        subprocess.check_call([sys.executable, '-m', 'pip'] + args + ['--disable-pip-version-check'])
