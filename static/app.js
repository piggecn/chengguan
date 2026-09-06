// 城管台账 — 前端脚本：四格密码登录 + 小区自动补全 + 离线暂存队列 + PWA
(function () {
  "use strict";

  // ---------- 安卓键盘收起后强制重绘（修复白色残影遮挡内容） ----------
  if (window.visualViewport) {
    var vv = window.visualViewport;
    var lastVVH = vv.height;
    vv.addEventListener("resize", function () {
      // 键盘弹出/收起时高度突变，部分机型残留白色遮挡，强制整页重绘
      if (Math.abs(vv.height - lastVVH) > 120) {
        var b = document.body;
        b.style.display = "none";
        void b.offsetHeight;
        b.style.display = "";
      }
      lastVVH = vv.height;
    });
  }

  // ---------- PWA：注册 Service Worker ----------
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }

  // ---------- 登录页：四格密码 ----------
  // 不再有选单位/选姓名，也不再支持把密码塞进分享链接自动登录——
  // 那样密码会落在双方浏览器历史和聊天记录里。
  var loginBoxes = document.querySelectorAll(".pin-box");
  var pinHidden = document.getElementById("pin");

  if (loginBoxes.length) {
    var form = pinHidden ? pinHidden.closest("form") : null;

    loginBoxes.forEach(function (box, idx) {
      box.addEventListener("input", function () {
        box.value = box.value.replace(/\D/g, "").slice(0, 1);
        if (box.value && idx < loginBoxes.length - 1) {
          loginBoxes[idx + 1].focus();
        }
        if (idx === loginBoxes.length - 1 && box.value) {
          var all = true;
          loginBoxes.forEach(function (b) { if (!b.value) all = false; });
          if (all) {
            var pin = "";
            loginBoxes.forEach(function (b) { pin += b.value; });
            pinHidden.value = pin;
            if (form) { form.submit(); }
          }
        }
      });
      box.addEventListener("keydown", function (e) {
        if (e.key === "Backspace" && !box.value && idx > 0) {
          loginBoxes[idx - 1].focus();
        }
      });
    });
    if (form) {
      form.addEventListener("submit", function () {
        var pin = "";
        loginBoxes.forEach(function (b) { pin += b.value; });
        pinHidden.value = pin;
      });
    }
  }

  // ---------- 统计页选项卡切换 ----------
  var tabBtns = document.querySelectorAll(".tab-btn");
  if (tabBtns.length) {
    tabBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        tabBtns.forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        document.querySelectorAll(".panel").forEach(function (p) {
          p.classList.add("hidden");
        });
        var panel = document.getElementById(btn.dataset.tab);
        if (panel) { panel.classList.remove("hidden"); }
      });
    });
  }

  // ---------- 首页：筛选折叠面板 ----------
  var filterToggle = document.getElementById("filter-toggle");
  var filterPanel = document.getElementById("filter-panel");
  var filterToggleLabel = document.getElementById("filter-toggle-label");
  if (filterToggle && filterPanel) {
    filterToggle.addEventListener("click", function () {
      var hidden = filterPanel.classList.toggle("hidden");
      if (filterToggleLabel) {
        filterToggleLabel.textContent = hidden ? "筛选" : "收起";
      }
    });
  }

  // ---------- 账户菜单（手机头像 + 桌面顶栏） ----------
  var accBtnD = document.getElementById("account-btn-d");
  var accBtnM = document.getElementById("account-btn-m");
  var accMenuD = document.getElementById("account-menu-d");
  var accMenuM = document.getElementById("account-menu-m");
  function toggleMenu(btn, menu) {
    if (!btn || !menu) { return; }
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var willOpen = menu.classList.contains("hidden");
      if (accMenuD) { accMenuD.classList.add("hidden"); }
      if (accMenuM) { accMenuM.classList.add("hidden"); }
      if (willOpen) { menu.classList.remove("hidden"); }
    });
  }
  toggleMenu(accBtnD, accMenuD);
  toggleMenu(accBtnM, accMenuM);
  document.addEventListener("click", function () {
    if (accMenuD) { accMenuD.classList.add("hidden"); }
    if (accMenuM) { accMenuM.classList.add("hidden"); }
  });

  // ---------- 账号管理页：复制登录链接（服务端已按配置域名生成，打开即自动登录） ----------
  document.querySelectorAll(".share-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var url = btn.dataset.link;
      function done() {
        btn.textContent = "已复制链接";
        setTimeout(function () { btn.textContent = "复制登录链接"; }, 1500);
      }
      function fallback() {
        var ta = document.createElement("textarea");
        ta.value = url;
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); } catch (e) {}
        document.body.removeChild(ta);
        done();
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(done, fallback);
      } else {
        fallback();
      }
    });
  });

  // ---------- 小区自动补全 ----------
  var datalist = document.getElementById("community-list");
  if (datalist) {
    var communityInput = document.querySelector('input[name="community"]');
    var timer = null;
    if (communityInput) {
      communityInput.addEventListener("input", function () {
        clearTimeout(timer);
        var q = communityInput.value.trim();
        timer = setTimeout(function () {
          fetch("/api/communities?q=" + encodeURIComponent(q))
            .then(function (r) { return r.json(); })
            .then(function (names) {
              datalist.innerHTML = "";
              names.forEach(function (n) {
                var opt = document.createElement("option");
                opt.value = n;
                datalist.appendChild(opt);
              });
            })
            .catch(function () {});
        }, 200);
      });
    }
  }

  // ---------- 照片预览 + 粘贴导入 ----------
  var photoInputs = document.querySelectorAll('input[type="file"][name="photos"]');
  photoInputs.forEach(function (input) {
    var hint = document.getElementById("photo-hint");
    var previewRow = document.getElementById("photo-preview");
    var files = [];
    function syncInput() {
      try {
        var dt = new DataTransfer();
        files.forEach(function (f) { dt.items.add(f); });
        input.files = dt.files;
      } catch (e) { /* 不支持 DataTransfer 时保留原 files */ }
    }
    function render() {
      if (!previewRow) { return; }
      previewRow.innerHTML = "";
      files.forEach(function (f, idx) {
        var box = document.createElement("div");
        box.className = "pv-item";
        var img = document.createElement("img");
        img.src = URL.createObjectURL(f);
        var del = document.createElement("button");
        del.type = "button";
        del.className = "pv-del";
        del.textContent = "×";
        del.setAttribute("aria-label", "移除照片");
        del.addEventListener("click", function () {
          files.splice(idx, 1);
          syncInput();
          render();
        });
        box.appendChild(img);
        box.appendChild(del);
        previewRow.appendChild(box);
      });
      if (hint) {
        hint.textContent = files.length > 0 ? "已选 " + files.length + " 张照片" : "未选择照片";
      }
    }
    // 并进来一批图片，回填到 input.files（提交表单时才随 multipart 上传）
    function addFiles(list) {
      var n = 0;
      for (var i = 0; i < list.length; i++) {
        var f = list[i];
        if (f && f.type.indexOf("image/") === 0) { files.push(f); n++; }
      }
      if (!n) { return 0; }
      syncInput();
      render();
      if (hint) {
        hint.textContent = "已加入 " + n + " 张 · 共 " + files.length + " 张";
      }
      return n;
    }
    input.__addPhotoFiles = addFiles;
    input.addEventListener("change", function () {
      for (var i = 0; i < input.files.length; i++) {
        files.push(input.files[i]);
      }
      syncInput();
      render();
    });
  });

  // 电脑端从微信复制图片后，聚焦本页直接 Ctrl+V / ⌘V 就能贴进来。
  // 手机端浏览器和 APK 内嵌浏览器拿不到系统剪贴板图片，这条只对桌面端有效。
  document.addEventListener("paste", function (e) {
    if (!photoInputs.length) { return; }
    var items = (e.clipboardData && e.clipboardData.items) || [];
    var images = [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].kind !== "file") { continue; }
      var f = items[i].getAsFile();
      if (!f || f.type.indexOf("image/") !== 0) { continue; }
      // 剪贴板里的图经常没有文件名，没有名的会被后端 save_photos 当空跳过
      if (!f.name) {
        try {
          f = new File([f], "照片_" + Date.now() + ".png", { type: f.type });
        } catch (err) { /* 不支持 File 构造器的老浏览器，原样传 */ }
      }
      images.push(f);
    }
    if (!images.length) { return; }   // 纯文本粘贴交回浏览器默认处理
    e.preventDefault();
    photoInputs[photoInputs.length - 1].__addPhotoFiles(images);
  });

  // ---------- 离线暂存队列（IndexedDB） ----------
  var DB_NAME = "chengguan-drafts";
  var STORE = "drafts";

  function openDb() {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = function () {
        req.result.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function saveDraft(entry) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, "readwrite");
        tx.objectStore(STORE).add(entry);
        tx.oncomplete = function () { resolve(); };
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }

  function listDrafts() {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var req = db.transaction(STORE, "readonly").objectStore(STORE).getAll();
        req.onsuccess = function () { resolve(req.result || []); };
        req.onerror = function () { reject(req.error); };
      });
    });
  }

  function deleteDraft(id) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, "readwrite");
        tx.objectStore(STORE).delete(id);
        tx.oncomplete = function () { resolve(); };
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }

  // 把表单序列化为可重放的草稿（字段 + 文件 Blob）
  function formToDraft(form) {
    var fields = {};
    var files = [];
    form.querySelectorAll("input, select, textarea").forEach(function (el) {
      if (!el.name) return;
      if (el.type === "file") {
        for (var i = 0; i < el.files.length; i++) {
          files.push({ name: el.name, file: el.files[i] });
        }
      } else {
        fields[el.name] = el.value;
      }
    });
    return { url: form.action, fields: fields, files: files, ts: Date.now() };
  }

  function draftToFormData(draft) {
    var fd = new FormData();
    Object.keys(draft.fields).forEach(function (k) { fd.append(k, draft.fields[k]); });
    (draft.files || []).forEach(function (f) { fd.append(f.name, f.file, f.file.name); });
    return fd;
  }

  function isLoginPage(url) {
    return /\/login/.test(url || "");
  }

  // 拦截表单提交：登录过期/服务端校验失败 → 提示原因；网络失败 → 存草稿
  document.querySelectorAll("form[data-draft]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var fd = new FormData(form);
      fetch(form.action, { method: "POST", body: fd })
        .then(function (resp) {
          if (resp.redirected && isLoginPage(resp.url)) {
            throw { needLogin: true };
          }
          if (!resp.ok) {
            return resp.text().then(function (t) {
              var m = t.match(/class="error">([^<]+)</);
              throw { serverError: m ? m[1] : ("提交失败（HTTP " + resp.status + "），请重试") };
            });
          }
          window.location.href = resp.redirected && resp.url ? resp.url : "/";
        })
        .catch(function (err) {
          if (err && err.needLogin) {
            alert("登录已过期，请重新登录后再提交。");
            return;
          }
          if (err && err.serverError) {
            alert(err.serverError);
            return;
          }
          saveDraft(formToDraft(form)).then(function () {
            alert("网络不好，已暂存本机（含照片），回到首页后可补传。");
            window.location.href = "/";
          });
        });
    });
  });

  // 首页：待上传草稿面板
  var draftArea = document.getElementById("draft-area");
  var draftText = document.getElementById("draft-text");
  var draftToggle = document.getElementById("draft-toggle");
  var draftRetry = document.getElementById("draft-retry");
  var draftPanel = document.getElementById("draft-panel");

  function draftTitle(d) {
    var f = d.fields || {};
    return f.community || f.result || "（无标题）";
  }

  function renderDrafts() {
    return listDrafts().then(function (drafts) {
      if (!drafts.length) {
        draftArea.classList.add("hidden");
        draftPanel.innerHTML = "";
        return;
      }
      draftArea.classList.remove("hidden");
      draftText.textContent = drafts.length + " 条记录待上传（离线暂存）";
      draftPanel.innerHTML = "";
      drafts.forEach(function (d) {
        var item = document.createElement("div");
        item.className = "draft-item";
        var info = document.createElement("div");
        info.className = "draft-info";
        info.innerHTML = "<b>" + escapeHtml(draftTitle(d)) + "</b>" +
          (d.ts ? "<span class='muted small'>" + new Date(d.ts).toLocaleString() + "</span>" : "");
        var btnRetry = document.createElement("button");
        btnRetry.className = "btn btn-mini btn-primary";
        btnRetry.textContent = "补传";
        btnRetry.addEventListener("click", function () { retryOne(d); });
        var btnDel = document.createElement("button");
        btnDel.className = "btn btn-mini btn-danger";
        btnDel.textContent = "删除";
        btnDel.addEventListener("click", function () {
          if (!confirm("删除这条待上传记录？")) return;
          deleteDraft(d.id).then(renderDrafts);
        });
        var actions = document.createElement("div");
        actions.className = "row gap";
        actions.appendChild(btnRetry);
        actions.appendChild(btnDel);
        item.appendChild(info);
        item.appendChild(actions);
        draftPanel.appendChild(item);
      });
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function retryOne(d) {
    var path = (d.url || "").replace(/^https?:\/\/[^/]+/, "");
    if (!path) { alert("无法识别这条记录，请删除后重录。"); return; }
    fetch(path, { method: "POST", body: draftToFormData(d) })
      .then(function (resp) {
        if (resp.redirected && isLoginPage(resp.url)) {
          throw { needLogin: true };
        }
        if (!resp.ok) {
          return resp.text().then(function (t) {
            var m = t.match(/class="error">([^<]+)</);
            throw { serverError: m ? m[1] : ("HTTP " + resp.status) };
          });
        }
        return deleteDraft(d.id);
      })
      .then(function () { renderDrafts(); })
      .catch(function (err) {
        if (err && err.needLogin) {
          alert("登录已过期，请重新登录后再补传。");
        } else if (err && err.serverError) {
          alert("补传失败：" + err.serverError);
        } else {
          alert("补传失败：网络不通，稍后再试。");
        }
      });
  }

  if (draftArea) {
    renderDrafts();
  }
  if (draftToggle) {
    draftToggle.addEventListener("click", function () {
      var hidden = draftPanel.classList.toggle("hidden");
      draftToggle.textContent = hidden ? "查看" : "收起";
    });
  }
  if (draftRetry) {
    draftRetry.addEventListener("click", function () {
      listDrafts().then(function (drafts) {
        if (!drafts.length) return;
        var chain = Promise.resolve();
        var ok = 0, fail = 0, loginExpired = false;
        drafts.forEach(function (d) {
          chain = chain.then(function () {
            var path = (d.url || "").replace(/^https?:\/\/[^/]+/, "");
            if (!path) { fail++; return; }
            return fetch(path, { method: "POST", body: draftToFormData(d) })
              .then(function (resp) {
                if (resp.redirected && isLoginPage(resp.url)) {
                  loginExpired = true;
                  throw { needLogin: true };
                }
                if (!resp.ok) { throw new Error("HTTP " + resp.status); }
                return deleteDraft(d.id).then(function () { ok++; });
              })
              .catch(function () { fail++; });
          });
        });
        return chain.then(function () {
          if (loginExpired) {
            alert("登录已过期，请重新登录后再补传。");
          } else {
            alert("补传完成：成功 " + ok + " 条" + (fail ? "，失败 " + fail + " 条" : ""));
          }
          renderDrafts();
        });
      });
    });
  }
})();
