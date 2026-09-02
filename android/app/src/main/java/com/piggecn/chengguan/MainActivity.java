package com.piggecn.chengguan;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.ProgressDialog;
import android.content.ClipData;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.net.http.SslError;
import android.os.Bundle;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.URLUtil;
import android.webkit.SslErrorHandler;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLDecoder;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends Activity {

    // 服务器地址在这里改
    private static final String APP_URL = "https://pycg.pigge.cn:8888";
    // 当前版本号（发新版时与 manifest 里的 versionCode 一起 +1）
    private static final int CURRENT_VERSION_CODE = 3;
    private static final String UPDATE_JSON = APP_URL + "/static/apk/latest.json";
    private static final String PREFS = "cg_prefs";
    private static final String KEY_COOKIES = "cookies";

    private WebView webView;
    private ValueCallback<Uri[]> filePathCallback;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 开启并恢复 Cookie，保证登录态持久
        CookieManager cm = CookieManager.getInstance();
        cm.setAcceptCookie(true);
        cm.setAcceptThirdPartyCookies(new WebView(this), true);
        restoreCookies();

        webView = new WebView(this);
        setContentView(webView);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                if (url.startsWith(APP_URL)) {
                    view.loadUrl(url);
                    return true;
                }
                return false;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                saveCookies();
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler,
                                           SslError error) {
                // 内网/自签证书场景允许继续，避免白屏
                handler.proceed();
            }
        });

        // 台账/照片等附件下载：App 内直接拉取（不走系统下载管理器——国内网络
        // 验证不过时它会永远排队），存到系统「下载」目录，文件管理里直接可见
        webView.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent,
                                        String contentDisposition, String mimeType,
                                        long contentLength) {
                startDownload(url, userAgent, contentDisposition, mimeType);
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                }
                filePathCallback = callback;
                Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("image/*");
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                startActivityForResult(Intent.createChooser(intent, "选择照片（可多选，可用相机）"),
                        1001);
                return true;
            }
        });

        webView.loadUrl(APP_URL);
        maybeCheckUpdate();
    }

    @Override
    protected void onResume() {
        super.onResume();
        // 「以后再说」之后回到前台也会再给一次更新机会（有节流）
        maybeCheckUpdate();
    }

    // ---------- 附件下载 ----------

    /**
     * 服务端中文文件名走 Content-Disposition 的 filename*=UTF-8''xx（RFC 5987），
     * URLUtil.guessFileName 解析不了会把整段尾巴当文件名（点开头=安卓隐藏文件），
     * 这里优先自己解 filename*，解不出再退回 URLUtil。
     */
    private static String guessDownloadName(String url, String contentDisposition,
                                            String mimeType) {
        if (contentDisposition != null) {
            Matcher m = Pattern.compile(
                    "filename\\*\\s*=\\s*(?:UTF-8|utf-8)''([^;\\s]+)",
                    Pattern.CASE_INSENSITIVE).matcher(contentDisposition);
            if (m.find()) {
                try {
                    return URLDecoder.decode(m.group(1), "UTF-8");
                } catch (Exception ignored) {
                }
            }
        }
        return URLUtil.guessFileName(url, contentDisposition, mimeType);
    }

    /** 下载完成/失败后弹提示，失败给原因，避免"到底下没下"说不清 */
    private static final String TAG = "CGDownload";
    private String[] pendingDownload;

    /** 写入完成后把 MediaStore 的 IS_PENDING 置 0，文件管理里立刻可见 */
    private void finishPendingRow(Uri row) {
        if (row != null) {
            android.content.ContentValues cv = new android.content.ContentValues();
            cv.put(android.provider.MediaStore.Downloads.IS_PENDING, 0);
            getContentResolver().update(row, cv, null, null);
        }
    }

    private void startDownload(final String url, final String userAgent,
                               final String contentDisposition, final String mimeType) {
        try {
            final String name = guessDownloadName(url, contentDisposition, mimeType);
            if (Build.VERSION.SDK_INT >= 29) {
                // Android 10+：走 MediaStore「下载」集合，无需任何存储权限
                android.content.ContentValues cv = new android.content.ContentValues();
                cv.put(android.provider.MediaStore.Downloads.DISPLAY_NAME, name);
                cv.put(android.provider.MediaStore.Downloads.MIME_TYPE,
                        mimeType != null ? mimeType : "application/octet-stream");
                cv.put(android.provider.MediaStore.Downloads.IS_PENDING, 1);
                final Uri row = getContentResolver().insert(
                        android.provider.MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
                if (row == null) {
                    Toast.makeText(this, "无法创建下载文件", Toast.LENGTH_LONG).show();
                    return;
                }
                doDownload(url, userAgent, name,
                        getContentResolver().openOutputStream(row), row, null);
            } else {
                // Android 9 及以下：需要存储权限，没有就现场申请，授权后继续
                if (checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE)
                        != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                    pendingDownload = new String[]{url, userAgent,
                            contentDisposition, mimeType};
                    requestPermissions(new String[]{
                            android.Manifest.permission.WRITE_EXTERNAL_STORAGE}, 2001);
                    return;
                }
                File dir = Environment.getExternalStoragePublicDirectory(
                        Environment.DIRECTORY_DOWNLOADS);
                if (!dir.exists()) {
                    dir.mkdirs();
                }
                final File out = uniqueFile(dir, name);
                doDownload(url, userAgent, name, new FileOutputStream(out), null, out);
            }
        } catch (final Exception e) {
            android.util.Log.e(TAG, "startDownload failed", e);
            Toast.makeText(this, "下载失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    /** 旧系统的重名处理：name(1).ext */
    private static File uniqueFile(File dir, String name) {
        File f = new File(dir, name);
        if (!f.exists()) {
            return f;
        }
        int dot = name.lastIndexOf('.');
        String base = dot > 0 ? name.substring(0, dot) : name;
        String ext = dot > 0 ? name.substring(dot) : "";
        for (int i = 1; f.exists(); i++) {
            f = new File(dir, base + "(" + i + ")" + ext);
        }
        return f;
    }

    private void doDownload(final String url, final String userAgent, final String name,
                            final OutputStream out, final Uri mediaRow, final File legacyFile) {
        final ProgressDialog pd = new ProgressDialog(this);
        pd.setTitle("正在下载");
        pd.setMessage(name);
        pd.setProgressStyle(ProgressDialog.STYLE_HORIZONTAL);
        pd.setCancelable(false);
        pd.show();
        new Thread(new Runnable() {
            @Override
            public void run() {
                boolean ok = false;
                try {
                    HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
                    conn.setConnectTimeout(15000);
                    conn.setReadTimeout(30000);
                    String cookies = CookieManager.getInstance().getCookie(url);
                    if (cookies != null) {
                        conn.setRequestProperty("Cookie", cookies);
                    }
                    conn.setRequestProperty("User-Agent", userAgent);
                    final int total = conn.getContentLength();
                    InputStream in = conn.getInputStream();
                    byte[] buf = new byte[16384];
                    int read;
                    int done = 0;
                    while ((read = in.read(buf)) > 0) {
                        out.write(buf, 0, read);
                        done += read;
                        if (total > 0) {
                            final int pct = done * 100 / total;
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    if (pd.isShowing()) {
                                        pd.setProgress(pct);
                                    }
                                }
                            });
                        }
                    }
                    out.flush();
                    ok = true;
                } catch (final Exception e) {
                    android.util.Log.e(TAG, "download failed", e);
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            Toast.makeText(MainActivity.this,
                                    "下载失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
                        }
                    });
                }
                try {
                    out.close();
                } catch (Exception ignored) {
                }
                final boolean success = ok;
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        pd.dismiss();
                        if (success) {
                            finishPendingRow(mediaRow);
                            Toast.makeText(MainActivity.this,
                                    "下载完成：" + name + "（已存到系统下载）",
                                    Toast.LENGTH_LONG).show();
                        } else {
                            if (mediaRow != null) {
                                getContentResolver().delete(mediaRow, null, null);
                            }
                            if (legacyFile != null) {
                                legacyFile.delete();
                            }
                        }
                    }
                });
            }
        }).start();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions,
                                           int[] grantResults) {
        if (requestCode == 2001) {
            String[] p = pendingDownload;
            pendingDownload = null;
            if (p != null && grantResults.length > 0
                    && grantResults[0] == android.content.pm.PackageManager.PERMISSION_GRANTED) {
                startDownload(p[0], p[1], p[2], p[3]);
            } else if (p != null) {
                Toast.makeText(this, "没有存储权限，无法保存到下载目录", Toast.LENGTH_LONG).show();
            }
        } else {
            super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        }
    }

    // ---------- 自更新：检查 latest.json → 弹窗 → 下载 → 调系统安装 ----------
    private static final long UPDATE_CHECK_INTERVAL = 6 * 3600 * 1000L;

    private void maybeCheckUpdate() {
        SharedPreferences sp = getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        long last = sp.getLong("last_update_check", 0);
        if (System.currentTimeMillis() - last < UPDATE_CHECK_INTERVAL) {
            return;
        }
        sp.edit().putLong("last_update_check", System.currentTimeMillis()).apply();
        checkUpdate();
    }

    private void checkUpdate() {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    HttpURLConnection conn = (HttpURLConnection) new URL(UPDATE_JSON).openConnection();
                    conn.setConnectTimeout(10000);
                    conn.setReadTimeout(10000);
                    conn.setRequestProperty("User-Agent", "Mozilla/5.0");
                    if (conn.getResponseCode() != 200) {
                        return;
                    }
                    BufferedReader br = new BufferedReader(
                            new InputStreamReader(conn.getInputStream()));
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = br.readLine()) != null) {
                        sb.append(line);
                    }
                    br.close();
                    final JSONObject json = new JSONObject(sb.toString());
                    final int vc = json.optInt("versionCode", 0);
                    final String vn = json.optString("versionName", "");
                    final String url = json.optString("url", "");
                    final String note = json.optString("note", "");
                    if (vc <= CURRENT_VERSION_CODE) {
                        return;
                    }
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            promptUpdate(vn, url, note);
                        }
                    });
                } catch (Exception ignored) {
                    // 检查失败静默，不影响使用
                }
            }
        }).start();
    }

    private void promptUpdate(final String versionName, final String url, final String note) {
        new AlertDialog.Builder(this)
                .setTitle("发现新版本 v" + versionName)
                .setMessage(note + "\n\n是否下载更新？")
                .setPositiveButton("更新", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        downloadApk(url);
                    }
                })
                .setNegativeButton("以后再说", null)
                .show();
    }

    private void downloadApk(final String path) {
        final ProgressDialog pd = new ProgressDialog(this);
        pd.setTitle("下载更新包");
        pd.setMessage("正在下载…");
        pd.setProgressStyle(ProgressDialog.STYLE_HORIZONTAL);
        pd.setCancelable(false);
        pd.show();
        new Thread(new Runnable() {
            @Override
            public void run() {
                File dir = new File(getExternalFilesDir(null), "downloads");
                dir.mkdirs();
                final File out = new File(dir, "chengguan.apk");
                try {
                    String full = path.startsWith("http") ? path : APP_URL + path;
                    HttpURLConnection conn = (HttpURLConnection) new URL(full).openConnection();
                    conn.setConnectTimeout(15000);
                    conn.setReadTimeout(15000);
                    int total = conn.getContentLength();
                    InputStream in = conn.getInputStream();
                    FileOutputStream fos = new FileOutputStream(out);
                    byte[] buf = new byte[8192];
                    int read;
                    int done = 0;
                    while ((read = in.read(buf)) > 0) {
                        fos.write(buf, 0, read);
                        done += read;
                        final int pct = total > 0 ? done * 100 / total : 0;
                        runOnUiThread(new Runnable() {
                            @Override
                            public void run() {
                                if (pd.isShowing()) {
                                    pd.setProgress(pct);
                                }
                            }
                        });
                    }
                    fos.close();
                    in.close();
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            pd.dismiss();
                            installApk(out);
                        }
                    });
                } catch (final Exception e) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            pd.dismiss();
                            Toast.makeText(MainActivity.this,
                                    "下载失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
                        }
                    });
                }
            }
        }).start();
    }

    private void installApk(File apk) {
        Uri uri = Uri.parse("content://" + FileProvider.AUTHORITY + "/downloads/" + apk.getName());
        Intent intent = new Intent(Intent.ACTION_VIEW);
        intent.setDataAndType(uri, "application/vnd.android.package-archive");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try {
            startActivity(intent);
        } catch (Exception e) {
            Toast.makeText(this,
                    "无法打开安装程序：请在系统设置→应用→城管巡查→允许「安装未知应用」后重试",
                    Toast.LENGTH_LONG).show();
        }
    }

    /** 把登录 Cookie 备份到本地，防止 WebView 被系统回收后丢失 */
    private void saveCookies() {
        String cookies = CookieManager.getInstance().getCookie(APP_URL);
        if (cookies == null || cookies.isEmpty()) {
            return;
        }
        getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().putString(KEY_COOKIES, cookies).apply();
        CookieManager.getInstance().flush();
    }

    /** 冷启动时把备份的 Cookie 写回 WebView */
    private void restoreCookies() {
        SharedPreferences sp = getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String cookies = sp.getString(KEY_COOKIES, null);
        if (cookies == null || cookies.isEmpty()) {
            return;
        }
        for (String pair : cookies.split(";")) {
            String p = pair.trim();
            if (p.contains("=")) {
                CookieManager.getInstance().setCookie(APP_URL, p);
            }
        }
        CookieManager.getInstance().flush();
    }

    @Override
    protected void onPause() {
        super.onPause();
        saveCookies();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == 1001) {
            if (filePathCallback == null) {
                return;
            }
            if (resultCode == RESULT_OK && data != null) {
                Uri[] results;
                if (data.getClipData() != null) {
                    ClipData cd = data.getClipData();
                    results = new Uri[cd.getItemCount()];
                    for (int i = 0; i < cd.getItemCount(); i++) {
                        results[i] = cd.getItemAt(i).getUri();
                    }
                } else if (data.getData() != null) {
                    results = new Uri[]{data.getData()};
                } else {
                    results = null;
                }
                filePathCallback.onReceiveValue(results);
            } else {
                filePathCallback.onReceiveValue(null);
            }
            filePathCallback = null;
        } else {
            super.onActivityResult(requestCode, resultCode, data);
        }
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
