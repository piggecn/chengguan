package com.piggecn.chengguan;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.util.List;

/**
 * 极简 FileProvider：把应用私有下载目录里的 APK 暴露给系统安装器。
 */
public class FileProvider extends ContentProvider {

    public static final String AUTHORITY = "com.piggecn.chengguan.fileprovider";
    private static final String SUBDIR = "downloads";

    @Override
    public boolean onCreate() {
        return true;
    }

    private File rootDir() {
        File f = new File(getContext().getExternalFilesDir(null), SUBDIR);
        if (!f.exists()) {
            f.mkdirs();
        }
        return f;
    }

    private File fileFor(Uri uri) throws FileNotFoundException {
        List<String> segs = uri.getPathSegments();
        if (segs.isEmpty()) {
            throw new FileNotFoundException("empty path");
        }
        String name = segs.get(segs.size() - 1);
        File root = rootDir();
        File f = new File(root, name);
        try {
            if (!f.getCanonicalPath().startsWith(root.getCanonicalPath())) {
                throw new FileNotFoundException("outside root");
            }
        } catch (Exception e) {
            throw new FileNotFoundException("resolve error");
        }
        return f;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        File f = fileFor(uri);
        int m = "r".equals(mode) ? ParcelFileDescriptor.MODE_READ_ONLY
                : ParcelFileDescriptor.MODE_READ_WRITE;
        return ParcelFileDescriptor.open(f, m);
    }

    @Override
    public String getType(Uri uri) {
        return "application/vnd.android.package-archive";
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
                        String[] selectionArgs, String sortOrder) {
        try {
            File f = fileFor(uri);
            MatrixCursor c = new MatrixCursor(new String[]{
                    OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE});
            c.addRow(new Object[]{f.getName(), f.length()});
            return c;
        } catch (FileNotFoundException e) {
            return null;
        }
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        return null;
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        return 0;
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        return 0;
    }
}
