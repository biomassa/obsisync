# Maintainer: biomassa <kustota@gmail.com>
pkgname=obsisync
pkgver=0.1.2
pkgrel=1
pkgdesc="Sync an Obsidian vault with iCloud Drive"
arch=('x86_64')
url="https://github.com/biomassa/obsisync"
license=('MIT')
# The binary is Nuitka-compiled and bundles Qt, so there are no runtime deps.
depends=()
options=(!strip)
source=()

package() {
  install -Dm755 "${startdir}/dist/obsisync-x86_64.AppImage" "${pkgdir}/usr/bin/obsisync"
  install -Dm644 "${startdir}/assets/icon.png" \
    "${pkgdir}/usr/share/icons/hicolor/256x256/apps/obsisync.png"
  install -Dm644 /dev/stdin "${pkgdir}/usr/share/applications/obsisync.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=obsisync
Comment=Sync an Obsidian vault with iCloud Drive
Exec=obsisync
Icon=obsisync
Categories=Utility;
Terminal=false
EOF
  install -Dm644 "${startdir}/LICENSE" "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"
}
