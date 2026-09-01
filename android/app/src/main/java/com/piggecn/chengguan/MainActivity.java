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
import android.net.http.SslError;
import android.os.Bundle;
import android.webkit.CookieManager;
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
import java.net.HttpURLConnection;
import java.net.URL;

public class MainActivity extends Activity {

    // 服务器地址在这里改
    private static final String APP_URL = "https://pycg.pigge.cn:8888";
    // 当前版本号（发新版时与 manifest 里的 versionCode 一起 +1）
    private static final int CURRENT_VERSION_CODE = 1;
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
        checkUpdate();
    }

    // ---------- 自更新：检查 latest.json → 弹窗 → 下载 → 调系统安装 ----------
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
        startActivity(intent);
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
