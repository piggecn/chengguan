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
  if (unitSel && nameSel) {
    function loadNames(unit) {
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
          if (rememberedName) { nameSel.value = rememberedName; }
        })
        .catch(function () {
          nameSel.innerHTML = '<option value="">加载失败，请重选单位</option>';
        });
    }
    var rememberedUnit = null, rememberedName = null;
    try {
      rememberedUnit = localStorage.getItem("cg-unit") || "";
      rememberedName = localStorage.getItem("cg-name") || "";
    } catch (e) { /* 隐私模式忽略 */ }
    if (rememberedUnit) {
      unitSel.value = rememberedUnit;
      loadNames(rememberedUnit);
    }
    unitSel.addEventListener("change", function () {
      loadNames(unitSel.value);
    });

    // 四格密码：自动跳格、退格回跳、输满 4 位自动登录
    var boxes = document.querySelectorAll(".pin-box");
    var pinHidden = document.getElementById("pin");
    var form = document.getElementById("login-form");
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
    return { url: form.action, fields: fields, files: files };
  }

  function draftToFormData(draft) {
    var fd = new FormData();
    Object.keys(draft.fields).forEach(function (k) { fd.append(k, draft.fields[k]); });
    draft.files.forEach(function (f) { fd.append(f.name, f.file, f.file.name); });
    return fd;
  }

  // 拦截表单提交：网络失败 → 存草稿，跳首页
  document.querySelectorAll("form[data-draft]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var fd = new FormData(form);
      fetch(form.action, { method: "POST", body: fd })
        .then(function (resp) {
          if (!resp.ok) { throw new Error("HTTP " + resp.status); }
          window.location.href = resp.redirected && resp.url ? resp.url : "/";
        })
        .catch(function () {
          saveDraft(formToDraft(form)).then(function () {
            alert("网络不好，已暂存本机（含照片），回到首页后可补传。");
            window.location.href = "/";
          });
        });
    });
  });

  // 首页：待上传横幅
  var banner = document.getElementById("draft-banner");
  var bannerText = document.getElementById("draft-text");
  var retryBtn = document.getElementById("draft-retry");
  if (banner) {
    listDrafts().then(function (drafts) {
      if (drafts.length) {
        bannerText.textContent = drafts.length + " 条记录待上传（离线暂存）";
        banner.classList.remove("hidden");
      }
    });
  }
  if (retryBtn) {
    retryBtn.addEventListener("click", function () {
      retryBtn.disabled = true;
      retryBtn.textContent = "补传中…";
      listDrafts().then(function (drafts) {
        var chain = Promise.resolve();
        drafts.forEach(function (d) {
          chain = chain.then(function () {
            return fetch(d.url, { method: "POST", body: draftToFormData(d) })
              .then(function (resp) {
                if (!resp.ok) { throw new Error("HTTP " + resp.status); }
                return deleteDraft(d.id);
              })
              .catch(function () { /* 这条留着，下一条继续 */ });
          });
        });
        return chain;
      }).then(function () {
        window.location.reload();
      });
    });
  }
})();
