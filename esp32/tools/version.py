"""PlatformIO pre-script: the firmware version comes from esp32/VERSION."""
import os

Import("env")  # noqa: F821

with open(os.path.join(env.subst("$PROJECT_DIR"), "VERSION")) as fh:  # noqa: F821
    version = fh.read().strip()
env.Append(CPPDEFINES=[("WG_FW_VERSION", env.StringifyMacro(version))])  # noqa: F821
