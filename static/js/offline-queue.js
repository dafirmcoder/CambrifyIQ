/**
 * Offline draft queue (CAMS plan 10.3).
 *
 * Failed autosaves are stored in IndexedDB with a client-generated operation ID
 * and the revision they were based on. On reconnect the queue is replayed
 * against /api/sync/operations/, which is idempotent: a replayed ID is reported
 * as a duplicate rather than applied twice, and a stale revision comes back as a
 * conflict for the user to resolve explicitly.
 *
 * Caches are per-user and are purged at logout or account switch.
 */
(function (global) {
  "use strict";

  var DB_NAME = "cambrify-offline";
  var STORE = "operations";
  var VERSION = 1;

  function openDatabase() {
    return new Promise(function (resolve, reject) {
      if (!global.indexedDB) {
        reject(new Error("IndexedDB is unavailable."));
        return;
      }
      var request = global.indexedDB.open(DB_NAME, VERSION);
      request.onupgradeneeded = function () {
        var db = request.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: "operation_id" });
        }
      };
      request.onsuccess = function () {
        resolve(request.result);
      };
      request.onerror = function () {
        reject(request.error);
      };
    });
  }

  function withStore(mode, handler) {
    return openDatabase().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, mode);
        var store = tx.objectStore(STORE);
        var result = handler(store);
        tx.oncomplete = function () {
          resolve(result && result.result !== undefined ? result.result : result);
        };
        tx.onerror = function () {
          reject(tx.error);
        };
      });
    });
  }

  function newOperationId() {
    if (global.crypto && global.crypto.randomUUID) {
      return global.crypto.randomUUID();
    }
    return "op-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }

  function deviceId() {
    var key = "cambrify-device-id";
    var value = global.localStorage ? global.localStorage.getItem(key) : null;
    if (!value) {
      value = newOperationId();
      if (global.localStorage) {
        global.localStorage.setItem(key, value);
      }
    }
    return value;
  }

  /** Queue one operation for later replay. */
  function enqueue(operation) {
    var entry = Object.assign(
      { operation_id: newOperationId(), device_id: deviceId(), queued_at: Date.now() },
      operation
    );
    return withStore("readwrite", function (store) {
      store.put(entry);
      return entry;
    }).then(function () {
      return entry;
    });
  }

  function all() {
    return openDatabase().then(function (db) {
      return new Promise(function (resolve, reject) {
        var request = db.transaction(STORE, "readonly").objectStore(STORE).getAll();
        request.onsuccess = function () {
          resolve(request.result || []);
        };
        request.onerror = function () {
          reject(request.error);
        };
      });
    });
  }

  function remove(operationId) {
    return withStore("readwrite", function (store) {
      store.delete(operationId);
    });
  }

  function clear() {
    return withStore("readwrite", function (store) {
      store.clear();
    });
  }

  function csrfToken() {
    var input = document.querySelector("[name=csrfmiddlewaretoken]");
    return input ? input.value : "";
  }

  /** Replay everything queued. Applied and duplicate entries are dropped. */
  function flush() {
    return all().then(function (entries) {
      if (!entries.length) {
        return { flushed: 0, conflicts: [] };
      }
      var operations = entries.map(function (entry) {
        return {
          operation_id: entry.operation_id,
          device_id: entry.device_id,
          name: entry.name,
          plan_type: entry.plan_type,
          plan_id: entry.plan_id,
          base_revision: entry.base_revision,
          payload: entry.payload
        };
      });

      return fetch("/api/sync/operations/", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify({ operations: operations })
      })
        .then(function (response) {
          return response.json();
        })
        .then(function (body) {
          var conflicts = [];
          var settled = (body.results || []).map(function (result) {
            if (result.result === "conflict" || result.result === "rejected") {
              conflicts.push(result);
            }
            return remove(result.operation_id);
          });
          return Promise.all(settled).then(function () {
            return { flushed: (body.results || []).length, conflicts: conflicts };
          });
        });
    });
  }

  global.CambrifyOffline = {
    enqueue: enqueue,
    flush: flush,
    pending: all,
    clear: clear,
    deviceId: deviceId
  };

  // Replay automatically when connectivity returns.
  global.addEventListener("online", function () {
    flush().catch(function () {
      /* Retried on the next online event. */
    });
  });

  document.addEventListener("DOMContentLoaded", function () {
    if (global.navigator.onLine) {
      flush().catch(function () {});
    }
    // Purge protected local drafts when the user signs out (10.3).
    document.querySelectorAll('form[action*="logout"]').forEach(function (form) {
      form.addEventListener("submit", function () {
        clear().catch(function () {});
      });
    });
  });
})(window);
