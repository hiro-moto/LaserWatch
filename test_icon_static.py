from pathlib import Path

root = Path(__file__).resolve().parent
assert (root / 'assets' / 'icon.png').exists()
assert (root / 'assets' / 'icon.ico').exists()
assert (root / 'docs' / 'icon_preview.png').exists()
app = (root / 'laserwatch' / 'app.py').read_text(encoding='utf-8')
main = (root / 'laserwatch' / 'main_window.py').read_text(encoding='utf-8')
spec = (root / 'LaserWatch.spec').read_text(encoding='utf-8')
iss = (root / 'installer' / 'LaserWatch.iss').read_text(encoding='utf-8')
readme = (root / 'README.md').read_text(encoding='utf-8')
assert 'app.setWindowIcon' in app
assert 'self.setWindowIcon' in main
assert "datas=[('assets', 'assets')]" in spec
assert "icon='assets/icon.ico'" in spec
assert r'SetupIconFile=..\assets\icon.ico' in iss
assert 'docs/icon_preview.png' in readme
print('icon integration static checks: PASS')
