#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
version=${1:-1.0.0}
build_dir="$project_dir/build/deb-root"
output_dir="$project_dir/dist"

case "$version" in
    *[!0-9A-Za-z.+:~-]*|'') echo "Invalid Debian version: $version" >&2; exit 2 ;;
esac

rm -rf -- "$build_dir"
install -d "$build_dir/DEBIAN" \
    "$build_dir/usr/bin" \
    "$build_dir/usr/lib/ciacore-wardrive/assets" \
    "$build_dir/usr/share/applications" \
    "$build_dir/usr/share/doc/ciacore-wardrive" \
    "$build_dir/usr/share/icons/hicolor/256x256/apps" \
    "$output_dir"

sed "s/^Version: .*/Version: $version/" \
    "$project_dir/packaging/debian/control" > "$build_dir/DEBIAN/control"
install -m 0755 "$project_dir/packaging/ciacore-wardrive" "$build_dir/usr/bin/ciacore-wardrive"
install -m 0644 "$project_dir/wardrive_gui.py" "$build_dir/usr/lib/ciacore-wardrive/wardrive_gui.py"
install -m 0644 "$project_dir/assets/ciacore-header.png" "$build_dir/usr/lib/ciacore-wardrive/assets/ciacore-header.png"
install -m 0644 "$project_dir/assets/ciacore-app-icon.png" \
    "$build_dir/usr/share/icons/hicolor/256x256/apps/ciacore-wardrive.png"
install -m 0644 "$project_dir/packaging/ciacore-wardrive.desktop" \
    "$build_dir/usr/share/applications/ciacore-wardrive.desktop"
install -m 0644 "$project_dir/README.md" "$build_dir/usr/share/doc/ciacore-wardrive/README.md"

dpkg-deb --root-owner-group --build "$build_dir" "$output_dir/ciacore-wardrive_${version}_all.deb"
echo "Built $output_dir/ciacore-wardrive_${version}_all.deb"
