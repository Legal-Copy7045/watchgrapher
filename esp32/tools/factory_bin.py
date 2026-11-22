"""
PlatformIO post-script: after firmware.bin is built, also write
firmware.factory.bin -- bootloader, partition table, OTA data and app merged
into one image flashed at offset 0. That is what the browser installer flashes;
firmware.bin on its own is the over-the-air update.
"""
Import("env")  # noqa: F821


def merge(source, target, env):
    build = env.subst("$BUILD_DIR")
    app = env.subst("$BUILD_DIR/${PROGNAME}.bin")
    out = f"{build}/firmware.factory.bin"
    parts = []
    for offset, image in env.get("FLASH_EXTRA_IMAGES", []):
        parts += [offset, env.subst(image)]
    parts += [env.subst("$ESP32_APP_OFFSET"), app]
    cmd = ['"$PYTHONEXE"', '"$OBJCOPY"', "--chip", env.subst("$BOARD_MCU"), "merge_bin",
           "-o", f'"{out}"', "--flash_mode", "keep", "--flash_freq", "keep",
           "--flash_size", env.BoardConfig().get("upload.flash_size", "keep")]
    cmd += [f'"{p}"' if i % 2 else p for i, p in enumerate(parts)]
    env.Execute(" ".join(cmd))


env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", merge)  # noqa: F821
