/**
 * Plan builder autosave (CAMS plan 8.5 and 10.3).
 *
 * Saves are debounced and carry the revision token the page was rendered with.
 * A 409 response means another device saved first: the user is told explicitly
 * and asked to reload rather than having their work silently overwritten.
 */
(function () {
  "use strict";

  var DEBOUNCE_MS = 900;

  function csrfToken(scope) {
    var input = (scope || document).querySelector("[name=csrfmiddlewaretoken]");
    return input ? input.value : "";
  }

  function setState(text, tone) {
    document.querySelectorAll("[data-save-state]").forEach(function (node) {
      node.textContent = text;
      node.dataset.tone = tone || "idle";
    });
  }

  function currentRevision() {
    var holder = document.querySelector("[data-revision]");
    return holder ? holder.getAttribute("data-revision") : null;
  }

  function setRevision(value) {
    document.querySelectorAll("[data-revision]").forEach(function (node) {
      node.setAttribute("data-revision", value);
    });
    document.querySelectorAll("[data-revision-label]").forEach(function (node) {
      node.textContent = value;
    });
  }

  function handleConflict(body) {
    setState("Saved elsewhere — reload needed", "conflict");
    var detail = (body && body.detail && body.detail.join(" ")) || "This plan changed on another device.";
    var banner = document.querySelector("[data-conflict-banner]");
    if (!banner) {
      banner = document.createElement("div");
      banner.setAttribute("data-conflict-banner", "");
      banner.className = "callout callout--danger callout--sticky";
      banner.innerHTML =
        "<strong>Edit conflict</strong><p>" +
        detail +
        '</p><button type="button" class="button button--primary button--small">Reload this plan</button>';
      banner.querySelector("button").addEventListener("click", function () {
        window.location.reload();
      });
      document.querySelector("main").prepend(banner);
    }
  }

  function post(url, payload, scope, options) {
    payload.append("csrfmiddlewaretoken", csrfToken(scope));
    var revision = currentRevision();
    if (revision) {
      payload.append("revision", revision);
    }
    setState("Saving…", "busy");

    return fetch(url, {
      method: "POST",
      body: payload,
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin"
    })
      .then(function (response) {
        return response.json().then(function (body) {
          return { status: response.status, body: body };
        });
      })
      .then(function (result) {
        if (result.status === 409) {
          handleConflict(result.body);
          return null;
        }
        if (!result.body || result.body.ok !== true) {
          var message = (result.body && result.body.detail && result.body.detail.join(" ")) || "Could not save.";
          setState(message, "error");
          return null;
        }
        setRevision(result.body.revision);
        setState("Saved at " + (result.body.saved_at || "now"), "ok");
        return result.body;
      })
      .catch(function () {
        // Offline or unreachable: queue the operation for idempotent replay.
        if (window.CambrifyOffline && options && options.operation) {
          var operation = options.operation();
          operation.base_revision = currentRevision();
          window.CambrifyOffline.enqueue(operation)
            .then(function () {
              setState("Offline — saved on this device", "offline");
            })
            .catch(function () {
              setState("Offline — reconnect to save", "error");
            });
        } else {
          setState("Offline — changes kept locally", "offline");
        }
        return null;
      });
  }

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments;
      var context = this;
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        fn.apply(context, args);
      }, wait);
    };
  }

  /* ---------------- Lesson plan ---------------- */

  var lessonForm = document.querySelector("[data-autosave-url]");
  if (lessonForm && !lessonForm.hasAttribute("data-readonly")) {
    var saveLesson = debounce(function () {
      var payload = new FormData();
      lessonForm.querySelectorAll("[data-autosave]").forEach(function (field) {
        if (field.multiple) {
          Array.prototype.forEach.call(field.selectedOptions, function (option) {
            payload.append(field.name, option.value);
          });
          if (field.selectedOptions.length === 0) {
            payload.append(field.name, "");
          }
        } else {
          payload.append(field.name, field.value);
        }
      });
      post(lessonForm.getAttribute("data-autosave-url"), payload, lessonForm, {
        operation: function () {
          var fields = {};
          lessonForm.querySelectorAll("[data-autosave]").forEach(function (field) {
            if (field.multiple) {
              fields.objective_ids = Array.prototype.map.call(field.selectedOptions, function (o) {
                return o.value;
              });
            } else if (field.name === "subtopic") {
              fields.subtopic_id = field.value;
            } else if (field.value !== "") {
              fields[field.name] =
                field.type === "number" ? parseInt(field.value, 10) : field.value;
            }
          });
          return {
            name: "lesson_plan.save",
            plan_type: "lesson_plan",
            plan_id: lessonForm.getAttribute("data-plan-id"),
            payload: fields
          };
        }
      }).then(function (body) {
        if (!body) return;
        var total = document.querySelector("[data-attendance-total]");
        if (total) {
          total.value = body.attendance_total === null ? "—" : body.attendance_total;
        }
      });
    }, DEBOUNCE_MS);

    lessonForm.addEventListener("input", saveLesson);
    lessonForm.addEventListener("change", saveLesson);
  }

  /* ---------------- Work plan rows ---------------- */

  document.querySelectorAll("[data-row-url]").forEach(function (row) {
    var save = debounce(function () {
      var payload = new FormData();
      row.querySelectorAll("[data-row-field]").forEach(function (field) {
        if (field.multiple) {
          Array.prototype.forEach.call(field.selectedOptions, function (option) {
            payload.append(field.name, option.value);
          });
          if (field.selectedOptions.length === 0) {
            payload.append(field.name, "");
          }
        } else {
          payload.append(field.name, field.value);
        }
      });
      post(row.getAttribute("data-row-url"), payload, document, {
        operation: function () {
          var select = row.querySelector("select[data-row-field]");
          var remarks = row.querySelector("textarea[data-row-field]");
          return {
            name: "work_plan.save_row",
            plan_type: "work_plan",
            plan_id: row.getAttribute("data-plan-id"),
            payload: {
              row_id: row.getAttribute("data-row-id"),
              objective_ids: select
                ? Array.prototype.map.call(select.selectedOptions, function (o) { return o.value; })
                : [],
              remarks: remarks ? remarks.value : ""
            }
          };
        }
      }).then(function (body) {
        if (!body || !body.objective_labels) return;
        var list = row.querySelector(".objective-list");
        if (list) {
          list.innerHTML = "";
          body.objective_labels.forEach(function (label) {
            var item = document.createElement("li");
            item.textContent = label;
            list.appendChild(item);
          });
        }
      });
    }, DEBOUNCE_MS);

    row.addEventListener("input", save);
    row.addEventListener("change", save);
  });

  /* ---------------- Work plan resources ---------------- */

  var resourceForm = document.querySelector("[data-resources-url]");
  if (resourceForm) {
    var saveResources = debounce(function () {
      var field = resourceForm.querySelector("[data-autosave]");
      if (!field || field.disabled) return;
      var payload = new FormData();
      payload.append("resources", field.value);
      post(resourceForm.getAttribute("data-resources-url"), payload, resourceForm, {
        operation: function () {
          return {
            name: "work_plan.save_resources",
            plan_type: "work_plan",
            plan_id: resourceForm.getAttribute("data-plan-id"),
            payload: { resources: field.value }
          };
        }
      });
    }, DEBOUNCE_MS);
    resourceForm.addEventListener("input", saveResources);
  }
})();
