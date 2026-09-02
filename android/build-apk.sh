#!/usr/bin/env bash
# 城管巡查 WebView 壳 APK 构建脚本（免 Gradle，纯命令行）
set -e
SDK=/d/app/Android/Sdk
BT=$SDK/build-tools/36.0.0
PLATFORM=$SDK/platforms/android-34/android.jar
JDK=/d/app/jdk-17
cd "$(dirname "$0")"

rm -rf build && mkdir -p build/classes

echo "[1/6] 编译资源"
"$BT/aapt2.exe" compile --dir app/src/main/res -o build/compiled.zip
"$BT/aapt2.exe" link -o build/base.apk -I "$PLATFORM" \
  --min-sdk-version 24 --target-sdk-version 34 \
  --version-code 2 --version-name "1.0.1" \
  --manifest app/src/main/AndroidManifest.xml build/compiled.zip

echo "[2/6] 编译 Java"
find app/src/main/java -name '*.java' > build/sources.txt
"$JDK/bin/javac.exe" -encoding UTF-8 -source 1.8 -target 1.8 -nowarn \
  -classpath "$PLATFORM" -d build/classes @build/sources.txt

echo "[3/6] dex"
"$JDK/bin/java" -cp "$BT/lib/d8.jar" com.android.tools.r8.D8 --min-api 24 --output build $(find build/classes -name '*.class')

echo "[4/6] 打包 dex"
( cd build && "$JDK/bin/jar.exe" -uf base.apk classes.dex )

echo "[5/6] 签名"
if [ ! -f chengguan.keystore ]; then
  "$JDK/bin/keytool.exe" -genkeypair -keystore chengguan.keystore -alias chengguan \
    -keyalg RSA -keysize 2048 -validity 10000 \
    -storepass chengguan2026 -keypass chengguan2026 \
    -dname "CN=chengguan, OU=pycg, O=piggecn, L=PY, C=CN"
fi
"$BT/zipalign.exe" -f 4 build/base.apk build/aligned.apk
"$JDK/bin/java" -jar "$BT/lib/apksigner.jar" sign \
  --ks chengguan.keystore --ks-pass pass:chengguan2026 --key-pass pass:chengguan2026 \
  --out chengguan.apk build/aligned.apk

echo "[6/6] 验证"
"$JDK/bin/java" -jar "$BT/lib/apksigner.jar" verify --print-certs chengguan.apk | head -3
echo "构建完成: $(pwd)/chengguan.apk"
