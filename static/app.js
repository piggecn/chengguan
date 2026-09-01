// 社区城管巡查 — 前端脚本：登录（选单位→选人→四格密码）+ 小区自动补全 + 离线暂存队列 + PWA
(function () {
  "use strict";

  // ---------- PWA：注册 Service Worker ----------
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }

  // ---------- 登录页：选单位 → 加载该单位人员 ----------
  var unitSel = document.getElementById("unit");
  var nameSel = document.getElementById("name");
  var loginBoxes = document.querySelectorAll(".pin-box");
  var loginForm = document.getElementById("login-form");
  var pinHidden = document.getElementById("pin");
  var urlParams = new URLSearchParams(location.search);
  var shareUnit = urlParams.get("unit") || "";
  var shareName = urlParams.get("name") || "";
  var sharePin = urlParams.get("pin") || "";

  if (unitSel && nameSel) {
    var rememberedUnit = null, rememberedName = null;
    try {
      rememberedUnit = localStorage.getItem("cg-unit") || "";
      rememberedName = localStorage.getItem("cg-name") || "";
    } catch (e) { /* 隐私模式忽略 */ }

    function fillPinBoxes(pin) {
      if (!/^\d{4}$/.test(pin || "")) { return false; }
      loginBoxes.forEach(function (b, i) { b.value = pin[i]; });
      pinHidden.value = pin;
      return true;
    }

    function tryAutoLogin() {
      if (fillPinBoxes(sharePin)) {
        setTimeout(function () {
          try {
            localStorage.setItem("cg-unit", unitSel.value);
            localStorage.setItem("cg-name", nameSel.value);
          } catch (e) { /* ignore */ }
          loginForm.submit();
        }, 200);
      }
    }

    function loadNames(unit, cb) {
      nameSel.disabled = true;
      nameSel.innerHTML = '<option value="">加载中…</option>';
      fetch("/api/users?unit=" + encodeURIComponent(unit))
        .then(function (r) { return r.json(); })
        .then(function (names) {
          nameSel.innerHTML = '<option value="">② 请选择姓名</option>';
          names.forEach(function (n) {
            var opt = document.createElement("option");
            opt.value = n;
            opt.textContent = n;
            nameSel.appendChild(opt);
          });
          nameSel.disabled = false;
          var want = shareName || rememberedName;
          if (want) { nameSel.value = want; }
          if (cb) { cb(); }
        })
        .catch(function () {
          nameSel.innerHTML = '<option value="">加载失败，请重选单位</option>';
        });
    }

    if (shareUnit || rememberedUnit) {
      unitSel.value = shareUnit || rememberedUnit;
      loadNames(unitSel.value, function () {
        if (shareUnit && shareName && sharePin) { tryAutoLogin(); }
      });
    }
    unitSel.addEventListener("change", function () {
      loadNames(unitSel.value);
    });

    // 四格密码：自动跳格、退格回跳、输满 4 位自动登录
    var boxes = loginBoxes;
    var form = loginForm;
    boxes.forEach(function (box, idx) {
      box.addEventListener("input", function () {
        box.value = box.value.replace(/\D/g, "").slice(0, 1);
        if (box.value && idx < boxes.length - 1) {
          boxes[idx + 1].focus();
        }
        if (idx === boxes.length - 1) {
          var all = true;
          boxes.forEach(function (b) { if (!b.value) all = false; });
          if (all) {
            var pin = "";
            boxes.forEach(function (b) { pin += b.value; });
            pinHidden.value = pin;
            try {
              localStorage.setItem("cg-unit", unitSel.value);
              localStorage.setItem("cg-name", nameSel.value);
            } catch (e) { /* ignore */ }
            form.submit();
          }
        }
      });
      box.addEventListener("keydown", function (e) {
        if (e.key === "Backspace" && !box.value && idx > 0) {
          boxes[idx - 1].focus();
        }
      });
    });
    form.addEventListener("submit", function () {
      var pin = "";
      boxes.forEach(function (b) { pin += b.value; });
      pinHidden.value = pin;
      try {
        localStorage.setItem("cg-unit", unitSel.value);
        localStorage.setItem("cg-name", nameSel.value);
      } catch (e) { /* ignore */ }
    });
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

  // ---------- 导出：折叠面板，携带当前筛选条件 ----------
  var exportToggle = document.getElementById("export-toggle");
  var exportPanel = document.getElementById("export-panel");
  var exportHint = document.getElementById("export-hint");
  var filterForm = document.querySelector("form.filter-row");
  function buildExportUrl(path) {
    var params = new URLSearchParams();
    ["team", "category", "reporter", "status"].forEach(function (k) {
      if (filterForm && filterForm.elements[k] && filterForm.elements[k].value) {
        params.set(k, filterForm.elements[k].value);
      }
    });
    var s = document.getElementById("exp-start"),
        e = document.getElementById("exp-end");
    if (s && s.value) { params.set("start", s.value); }
    if (e && e.value) { params.set("end", e.value); }
    return path + (params.toString() ? "?" + params.toString() : "");
  }
  if (exportToggle && exportPanel) {
    exportToggle.addEventListener("click", function () {
      var hidden = exportPanel.classList.toggle("hidden");
      exportToggle.textContent = hidden ? "导出" : "收起";
    });
  }
  var zipBtn = document.getElementById("export-zip");
  var docBtn = document.getElementById("export-doc");
  if (zipBtn) {
    zipBtn.addEventListener("click", function () {
      window.location.href = buildExportUrl("/export");
    });
  }
  if (docBtn) {
    docBtn.addEventListener("click", function () {
      window.location.href = buildExportUrl("/export/doc");
    });
  }

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

  // ---------- 账号管理页：单位联动角色 ----------
  var addUnit = document.getElementById("add-unit");
  var addRole = document.getElementById("add-role");
  var roleDataEl = document.getElementById("role-data");
  if (addRole && roleDataEl) {
    var roleData = JSON.parse(roleDataEl.textContent);
    function fillRoles(unit) {
      addRole.innerHTML = '<option value="" disabled selected>请选择角色</option>';
      var opts = (unit === "办公室") ? roleData.office : roleData.team;
      opts.forEach(function (p) {
        var o = document.createElement("option");
        o.value = p[0];
        o.textContent = p[1];
        addRole.appendChild(o);
      });
    }
    if (!addUnit) {
      // 中队长固定单位
      var hiddenUnit = document.querySelector('input[name="unit"]');
      fillRoles(hiddenUnit ? hiddenUnit.value : "");
    } else {
      addUnit.addEventListener("change", function () { fillRoles(addUnit.value); });
    }
  }

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

  // ---------- 照片数量提示 ----------
  var photoInputs = document.querySelectorAll('input[type="file"][name="photos"]');
  photoInputs.forEach(function (input) {
    var hint = document.getElementById("photo-hint");
    input.addEventListener("change", function () {
      if (hint) {
        var n = input.files.length;
        hint.textContent = n > 0 ? "已选 " + n + " 张照片" : "未选择照片";
      }
    });
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
    return /\/personnel/.test(url || "");
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
