"""Genere version_info.txt (fiche "Details" de l'exe Windows) a partir de APP_VERSION dans app.py.
SignPath exige un nom de produit et une version identiques dans chaque build."""
import re
import sys
from pathlib import Path

racine = Path(__file__).resolve().parent.parent
version = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', (racine / "app.py").read_text(encoding="utf-8"), re.M).group(1)
nums = [int(x) for x in re.findall(r"\d+", version)[:4]]
nums += [0] * (4 - len(nums))
t = tuple(nums)

(racine / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040C04B0', [
      StringStruct('CompanyName', 'leluxo'),
      StringStruct('FileDescription', 'FS25 Stream & Companion'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'FS25 Stream Dashboard'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 leluxo - MIT License'),
      StringStruct('OriginalFilename', 'FS25 Stream Dashboard.exe'),
      StringStruct('ProductName', 'FS25 Stream & Companion'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1036, 1200])])
  ]
)
""", encoding="utf-8")
print(version)
sys.exit(0)
