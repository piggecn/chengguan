@echo off
rem 城管巡查 WebView 壳 APK 构建脚本（免 Gradle，纯命令行）
setlocal
set SDK=D:\app\Android\Sdk
set BT=%SDK%\build-tools\36.0.0
set PLATFORM=%SDK%\platforms\android-34\android.jar
set JDK=D:\app\jdk-17
set PATH=%JDK%\bin;%PATH%

cd /d %~dp0
if not exist build mkdir build
if exist build\compiled.zip del /q build\compiled.zip
if exist build\classes rmdir /s /q build\classes
mkdir build\classes

echo [1/6] 编译资源...
"%BT%\aapt2.exe" compile --dir app\src\main\res -o build\compiled.zip || goto :err
"%BT%\aapt2.exe" link -o build\base.apk -I "%PLATFORM%" --manifest app\src\main\AndroidManifest.xml build\compiled.zip || goto :err

echo [2/6] 编译 Java...
dir /s /b app\src\main\java\*.java > build\sources.txt
"%JDK%\bin\javac.exe" -source 1.8 -target 1.8 -nowarn -classpath "%PLATFORM%" -d build\classes @build\sources.txt || goto :err

echo [3/6] dex 转换...
call "%BT%\d8.bat" --min-api 24 --output build build\classes || goto :err

echo [4/6] 打包 dex 进 APK...
pushd build
"%JDK%\bin\jar.exe" -uf base.apk classes.dex || goto :err
popd

echo [5/6] 对齐 + 签名...
if not exist chengguan.keystore (
  "%JDK%\bin\keytool.exe" -genkeypair -keystore chengguan.keystore -alias chengguan -keyalg RSA -keysize 2048 -validity 10000 -storepass chengguan2026 -keypass chengguan2026 -dname "CN=chengguan, OU=pycg, O=piggecn, L=PY, C=CN" || goto :err
)
"%BT%\zipalign.exe" -f 4 build\base.apk build\aligned.apk || goto :err
"%BT%\apksigner.bat" sign --ks chengguan.keystore --ks-pass pass:chengguan2026 --key-pass pass:chengguan2026 --out chengguan.apk build\aligned.apk || goto :err

echo [6/6] 验证签名...
"%BT%\apksigner.bat" verify --print-certs chengguan.apk || goto :err

echo.
echo 构建完成：%~dp0chengguan.apk
exit /b 0
:err
echo 构建失败，请检查上方错误。
exit /b 1
