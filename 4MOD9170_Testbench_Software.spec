# -*- mode: python ; coding: utf-8 -*-


block_cipher = None


a = Analysis(['4MOD9170_Testbench_Software.py'],
             pathex=['/home/test4mod9170/Desktop/4MOD9170_PEGO_Testbench_Software/4MOD9170_TestbenchSoftware'],
             binaries=[],
             datas=[('.venv/lib/python3.9/site-packages/pyphen/dictionaries', 'pyphen/dictionaries'), ('.venv/lib/python3.9/site-packages/blabel/data/print_template.html', 'blabel/data')],
             hiddenimports=[],
             hookspath=[],
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)
pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)

exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,  
          [],
          name='4MOD9170_Testbench_Software',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          upx_exclude=[],
          runtime_tmpdir=None,
          console=True,
          disable_windowed_traceback=False,
          target_arch=None,
          codesign_identity=None,
          entitlements_file=None )
